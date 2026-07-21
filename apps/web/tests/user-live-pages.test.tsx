import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  within,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ChatWorkspace } from "@/components/chat/chat-workspace";
import ProfilePage from "@/app/profile/page";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

const router = {
  push: vi.fn(),
  replace: vi.fn(),
};

vi.mock("next/navigation", () => ({
  useRouter: () => router,
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
        if (url.endsWith("/chat/conversations/page?limit=30")) {
          return jsonResponse({
            items: [
              {
                id: "conversation-live",
                channel: "web",
                title: "Real inventory question",
                last_message_preview: "Your tenant answer from the API.",
                updated_at: "2026-07-12T10:42:00Z",
              },
            ],
            next_cursor: null,
          });
        }
        if (
          url.endsWith(
            "/chat/conversations/conversation-live/messages?limit=50",
          )
        ) {
          return jsonResponse({
            items: [
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
            next_cursor: null,
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ChatWorkspace initialConversationId="conversation-live" />);

    expect(
      await screen.findByRole(
        "button",
        { name: /Real inventory question/ },
        { timeout: 5_000 },
      ),
    ).toBeInTheDocument();

    expect(
      await screen.findByText("Which item needs restocking?", undefined, {
        timeout: 5_000,
      }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText("Your tenant answer from the API.").length,
    ).toBeGreaterThanOrEqual(2);
    expect(screen.getByText(/Saved conversation/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows a retryable error instead of substituting chat history", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      jsonResponse({ detail: "Conversation service is unavailable" }, 503),
    );

    render(<ChatWorkspace />);

    expect(
      await screen.findByText("Recent chats are unavailable"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Conversation service is unavailable"),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Try again" })).toBeEnabled();
    expect(
      screen.queryByText(/Best sellers this week/),
    ).not.toBeInTheDocument();
  });

  it("loads and updates the authenticated profile and revokes other sessions", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
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
        if (url.endsWith("/settings/profile") && init?.method === "PATCH") {
          return jsonResponse({
            id: "user-live",
            name: "Nneka A. Mensah",
            email: "nneka.updated@example.com",
            phone_e164: "+233200000001",
          });
        }
        if (url.endsWith("/auth/logout-others") && init?.method === "POST") {
          return jsonResponse({
            message: "Other sessions signed out",
            revoked_sessions: 2,
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
    fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
    const profileDialog = within(screen.getByRole("dialog"));
    fireEvent.change(
      profileDialog.getByLabelText("Full name", { selector: "input" }),
      { target: { value: "Nneka A. Mensah" } },
    );
    fireEvent.change(
      profileDialog.getByLabelText("Email address", { selector: "input" }),
      { target: { value: "nneka.updated@example.com" } },
    );
    fireEvent.click(
      profileDialog.getByRole("button", { name: "Save changes" }),
    );
    await waitFor(() =>
      expect(fetchMock).toHaveBeenCalledWith(
        expect.stringContaining("/settings/profile"),
        expect.objectContaining({ method: "PATCH" }),
      ),
    );

    fireEvent.click(
      await screen.findByRole("button", { name: "Sign out other sessions" }),
    );
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Sign out other sessions",
      }),
    );
    expect(
      await screen.findByText("2 other sessions signed out."),
    ).toBeInTheDocument();
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
