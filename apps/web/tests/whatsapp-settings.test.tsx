import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import WhatsAppPage from "@/app/settings/whatsapp/page";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

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

describe("WhatsApp identity settings", () => {
  it("keeps owner mappings platform-managed and removes teammate access", async () => {
    let numbers = [
      {
        id: "phone-owner",
        user_id: "user-owner",
        phone_e164: "+15550102716",
        label: "Demo owner",
        status: "approved",
        opt_out: false,
      },
      {
        id: "phone-team",
        user_id: "user-team",
        phone_e164: "+2348333333333",
        label: "Sales",
        status: "approved",
        opt_out: false,
      },
    ];
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/settings/team")) {
          return jsonResponse([
            {
              membership_id: "membership-owner",
              user_id: "user-owner",
              name: "Demo Owner",
              email: null,
              phone_e164: "+15550102716",
              role: "owner",
              status: "active",
            },
            {
              membership_id: "membership-team",
              user_id: "user-team",
              name: "Sales Teammate",
              email: null,
              phone_e164: "+2348333333333",
              role: "member",
              status: "active",
            },
          ]);
        }
        if (
          url.endsWith("/settings/whatsapp-numbers/phone-team") &&
          init?.method === "DELETE"
        ) {
          numbers = numbers.filter((number) => number.id !== "phone-team");
          return new Response(null, { status: 204 });
        }
        if (url.endsWith("/settings/whatsapp-numbers")) {
          return jsonResponse(numbers);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<WhatsAppPage />);

    expect(await screen.findByText("Demo owner")).toBeInTheDocument();
    expect(screen.getByText("Platform managed")).toBeDisabled();
    expect(screen.getByText("Sales")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Remove access" }));
    const dialog = within(screen.getByRole("dialog"));
    expect(dialog.getByText("Sales")).toBeInTheDocument();
    fireEvent.click(dialog.getByRole("button", { name: "Remove access" }));

    expect(
      await screen.findByText(
        "WhatsApp access removed for that team identity.",
      ),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.queryByText("Sales")).not.toBeInTheDocument(),
    );
    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining("/settings/whatsapp-numbers/phone-team"),
      expect.objectContaining({ method: "DELETE" }),
    );
  });
});
