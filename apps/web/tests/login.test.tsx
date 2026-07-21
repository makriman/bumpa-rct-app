import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import LoginPageClient from "@/components/login-page";
import { createPhoneCountries } from "@/lib/phone";

const phoneCountries = createPhoneCountries((iso) => iso);

function LoginPage() {
  return <LoginPageClient phoneCountries={phoneCountries} nextPath={null} />;
}

const accepted = (delivery: "web_pin" | "whatsapp") =>
  new Response(
    JSON.stringify({
      delivery,
      expires_in_seconds: 600,
      dev_code: null,
    }),
    { status: 202, headers: { "content-type": "application/json" } },
  );

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.history.replaceState({}, "", "/login");
});

describe("pilot web sign-in", () => {
  it("does not prefill an identity in a non-demo build", () => {
    render(<LoginPage />);

    expect(
      screen.getByRole("button", { name: "Country code, GB +44" }),
    ).toBeVisible();
    expect(screen.getByLabelText("Mobile number")).toHaveValue("");
    expect(screen.queryByText("Local demo access")).not.toBeInTheDocument();
  });

  it("selects a calling code from the searchable flag picker", () => {
    render(<LoginPage />);

    fireEvent.click(
      screen.getByRole("button", { name: "Country code, GB +44" }),
    );
    fireEvent.change(
      screen.getByRole("textbox", {
        name: "Search countries or calling codes",
      }),
      { target: { value: "IN" } },
    );
    fireEvent.click(screen.getByRole("option", { name: "IN +91" }));

    expect(
      screen.getByRole("button", { name: "Country code, IN +91" }),
    ).toBeVisible();
    expect(screen.queryByRole("listbox")).not.toBeInTheDocument();
  });

  it("validates the national input before making a request", () => {
    const fetchMock = vi.spyOn(globalThis, "fetch");
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Mobile number"), {
      target: { value: "+44 7400 123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue securely" }));

    expect(screen.getByRole("alert", { name: "" })).toHaveTextContent(
      "Enter only the number after +44.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("normalizes a UK number and presents the temporary web PIN path", async () => {
    let releaseVerification: ((response: Response) => void) | undefined;
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(accepted("web_pin"))
      .mockImplementationOnce(
        () =>
          new Promise<Response>((resolve) => {
            releaseVerification = resolve;
          }),
      );
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Mobile number"), {
      target: { value: "07400 123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue securely" }));

    expect(
      await screen.findByRole("heading", { name: "Enter your web PIN." }),
    ).toBeInTheDocument();
    expect(screen.getByText(/no WhatsApp message was sent/i)).toBeVisible();
    expect(screen.getByLabelText("Six-digit web PIN")).toHaveAttribute(
      "type",
      "password",
    );
    expect(screen.getByLabelText("Six-digit web PIN")).toHaveAttribute(
      "autocomplete",
      "current-password",
    );
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      phone_e164: "+447400123456",
    });

    fireEvent.change(screen.getByLabelText("Six-digit web PIN"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.getByRole("button", { name: "Signing in…" })).toBeDisabled();
    expect(JSON.parse(String(fetchMock.mock.calls[1][1]?.body))).toEqual({
      phone_e164: "+447400123456",
      code: "123456",
    });

    releaseVerification?.(
      new Response(JSON.stringify({ detail: "Invalid or expired code" }), {
        status: 401,
        headers: { "content-type": "application/json" },
      }),
    );
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Invalid or expired code",
    );
  });

  it("retries session resolution without consuming the PIN twice", async () => {
    const neverResolve = new Promise<Response>(() => undefined);
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(accepted("web_pin"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ message: "Verified" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ detail: "Service unavailable" }), {
          status: 503,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockReturnValueOnce(neverResolve);
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Mobile number"), {
      target: { value: "07400 123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue securely" }));
    await screen.findByRole("heading", { name: "Enter your web PIN." });
    fireEvent.change(screen.getByLabelText("Six-digit web PIN"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByRole("heading", { name: "Open your workspace." }),
    ).toBeVisible();
    expect(screen.getByRole("alert")).toHaveTextContent(
      /PIN was accepted.*without entering the PIN again/i,
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Try opening workspace again" }),
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(4));
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/auth/verify-otp"),
      ),
    ).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Opening workspace…" }),
    ).toBeDisabled();
  });

  it("keeps a user signed in while explaining missing workspace access", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(accepted("web_pin"))
      .mockResolvedValueOnce(
        new Response(JSON.stringify({ message: "Verified" }), {
          status: 200,
          headers: { "content-type": "application/json" },
        }),
      )
      .mockResolvedValueOnce(
        new Response(
          JSON.stringify({
            memberships: [{ role: "owner", status: "suspended" }],
          }),
          {
            status: 200,
            headers: { "content-type": "application/json" },
          },
        ),
      );
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("Mobile number"), {
      target: { value: "07400 123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue securely" }));
    await screen.findByRole("heading", { name: "Enter your web PIN." });
    fireEvent.change(screen.getByLabelText("Six-digit web PIN"), {
      target: { value: "123456" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(
      await screen.findByRole("heading", {
        name: "You don’t have workspace access.",
      }),
    ).toBeVisible();
    expect(screen.getByText("Your session is active")).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Check access again" }),
    ).toBeEnabled();
    expect(window.location.pathname).toBe("/login");
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(
      fetchMock.mock.calls.filter(([input]) =>
        String(input).endsWith("/auth/verify-otp"),
      ),
    ).toHaveLength(1);
  });

  it("normalizes an India number and presents WhatsApp delivery when configured", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(accepted("whatsapp"));
    render(<LoginPage />);

    fireEvent.click(screen.getByRole("button", { name: /Country code/ }));
    fireEvent.click(screen.getByRole("option", { name: "IN +91" }));
    fireEvent.change(screen.getByLabelText("Mobile number"), {
      target: { value: "98765 43210" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue securely" }));

    expect(
      await screen.findByRole("heading", { name: "Check WhatsApp." }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Six-digit WhatsApp code")).toBeVisible();
    expect(screen.getByLabelText("Six-digit WhatsApp code")).toHaveAttribute(
      "type",
      "text",
    );
    expect(screen.getByLabelText("Six-digit WhatsApp code")).toHaveAttribute(
      "autocomplete",
      "one-time-code",
    );
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      phone_e164: "+919876543210",
    });
  });
});
