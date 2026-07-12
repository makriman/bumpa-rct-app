import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import LoginPage from "@/app/login/page";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("WhatsApp OTP sign-in", () => {
  it("does not prefill a demo identity in a non-demo build", () => {
    render(<LoginPage />);

    expect(screen.getByLabelText("WhatsApp phone number")).toHaveValue("");
    expect(screen.queryByText("Local demo access")).not.toBeInTheDocument();
  });

  it("lets the user request a replacement code", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify({ dev_code: null }), {
        status: 202,
        headers: { "content-type": "application/json" },
      }),
    );
    render(<LoginPage />);

    fireEvent.change(screen.getByLabelText("WhatsApp phone number"), {
      target: { value: "+2348012345678" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send WhatsApp code" }));
    expect(
      await screen.findByRole("heading", { name: "Check WhatsApp." }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Resend code" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    for (const [, init] of fetchMock.mock.calls) {
      expect(init?.method).toBe("POST");
      expect(JSON.parse(String(init?.body))).toEqual({
        phone_e164: "+2348012345678",
      });
    }
  });
});
