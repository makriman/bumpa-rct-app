import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import ChatPage from "@/app/chat/page";
import ProfilePage from "@/app/profile/page";

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

describe("deployment-facing user pages", () => {
  it("loads real conversation summaries and saved messages without demo fixtures", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/chat/conversations")) {
          return jsonResponse([
            {
              id: "conversation-live",
              channel: "web",
              title: "Real inventory question",
              status: "active",
              updated_at: "2026-07-12T10:42:00Z",
            },
          ]);
        }
        if (url.endsWith("/chat/conversations/conversation-live")) {
          return jsonResponse({
            id: "conversation-live",
            messages: [
              {
                id: "message-inbound",
                direction: "inbound",
                content: "Which item needs restocking?",
                created_at: "2026-07-12T10:42:00Z",
              },
              {
                id: "message-outbound",
                direction: "outbound",
                content: "Your tenant answer from the API.",
                created_at: "2026-07-12T10:42:02Z",
              },
            ],
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ChatPage />);

    expect(screen.queryByText(/Adire Table Runner/)).not.toBeInTheDocument();
    expect(screen.queryByLabelText("Demo prompts")).not.toBeInTheDocument();
    expect(
      await screen.findByRole("button", { name: /Real inventory question/ }),
    ).toBeInTheDocument();
    expect(screen.getByText("Tenant API")).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: /Real inventory question/ }),
    );

    expect(
      await screen.findByText("Which item needs restocking?"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Your tenant answer from the API."),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Saved tenant conversation · API/),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows a retryable error instead of substituting chat history", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Conversation service is unavailable" }, 503),
    );

    render(<ChatPage />);

    expect(
      await screen.findByText("Conversation history unavailable"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Conversation service is unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(
      screen.queryByText(/Best sellers this week/),
    ).not.toBeInTheDocument();
  });

  it("loads the authenticated profile and tenant while keeping unsupported writes disabled", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({
          user: {
            id: "user-live",
            name: "Nneka Mensah",
            email: "nneka@example.com",
            phone_e164: "+233200000001",
          },
          platform_roles: [],
          memberships: [
            {
              id: "membership-live",
              tenant_id: "tenant-live",
              role: "owner",
              status: "active",
            },
          ],
          current_tenant_id: "tenant-live",
        });
      }
      if (url.endsWith("/tenants/current")) {
        return jsonResponse({
          id: "tenant-live",
          slug: "real-studio",
          name: "Real Studio",
          status: "active",
          business_category: "Fashion",
          country: "Ghana",
          city: "Accra",
          timezone: "Africa/Accra",
          currency_code: "GHS",
          research_consent_status: "pending",
          role: "owner",
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<ProfilePage />);

    expect(await screen.findByText("Live profile")).toBeInTheDocument();
    expect(screen.getByDisplayValue("Nneka Mensah")).toBeDisabled();
    expect(screen.getByDisplayValue("nneka@example.com")).toBeDisabled();
    expect(screen.getByDisplayValue("+233200000001")).toBeDisabled();
    expect(screen.getByText("Real Studio")).toBeInTheDocument();
    expect(screen.getByText("Accra, Ghana")).toBeInTheDocument();
    expect(screen.getByText("GHS")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Editing unavailable" }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", {
        name: "Sign out other devices unavailable",
      }),
    ).toBeDisabled();
    expect(screen.queryByText("Kaia Home")).not.toBeInTheDocument();
  });

  it("offers a profile retry without rendering demo identity data", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      jsonResponse({ detail: "Tenant context is unavailable" }, 503),
    );

    render(<ProfilePage />);

    expect(
      await screen.findByText("We could not load your profile"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Tenant context is unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(screen.queryByText("Amara Okafor")).not.toBeInTheDocument();
    expect(screen.queryByText("Kaia Home")).not.toBeInTheDocument();
    await waitFor(() => expect(globalThis.fetch).toHaveBeenCalledTimes(2));
  });
});
