import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ConsentPage from "@/app/research-consent/page";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("research consent", () => {
  it("saves informed agreement through the tenant API and announces success", async () => {
    let completeRequest: ((response: Response) => void) | undefined;
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          completeRequest = resolve;
        }),
    );

    render(<ConsentPage />);

    const agree = screen.getByRole("button", {
      name: "I agree to participate",
    });
    expect(agree).toBeDisabled();
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I understand what data is included/,
      }),
    );
    fireEvent.click(
      screen.getByRole("checkbox", {
        name: /I understand participation is voluntary/,
      }),
    );
    fireEvent.click(agree);

    expect(
      screen.getByRole("button", { name: "Saving agreement…" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Continue without research" }),
    ).toBeDisabled();
    const [input, init] = fetchMock.mock.calls[0];
    expect(String(input).endsWith("/tenants/current/research-consent")).toBe(
      true,
    );
    expect(init?.method).toBe("POST");
    expect(JSON.parse(String(init?.body))).toEqual({
      status: "granted",
      policy_version: "v1",
    });

    completeRequest?.(jsonResponse({ status: "granted" }));

    expect(
      await screen.findByText(
        "Your research participation consent has been saved.",
      ),
    ).toHaveAttribute("role", "status");
    expect(
      screen.getByRole("button", { name: "I agree to participate" }),
    ).toBeEnabled();
  });

  it("records a decline without requiring agreement checkboxes", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(jsonResponse({ status: "withdrawn" }));

    render(<ConsentPage />);
    fireEvent.click(
      screen.getByRole("button", { name: "Continue without research" }),
    );

    expect(
      await screen.findByText(
        "Your choice to continue without research participation has been saved.",
      ),
    ).toBeInTheDocument();
    expect(JSON.parse(String(fetchMock.mock.calls[0][1]?.body))).toEqual({
      status: "withdrawn",
      policy_version: "v1",
    });
  });

  it("announces API failures and restores both choices for retry", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Sign in as a workspace owner" }, 403),
    );

    render(<ConsentPage />);
    fireEvent.click(
      screen.getByRole("button", { name: "Continue without research" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Sign in as a workspace owner",
    );
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Continue without research" }),
      ).toBeEnabled(),
    );
  });
});
