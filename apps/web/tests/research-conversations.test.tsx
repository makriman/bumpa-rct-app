import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Conversations } from "@/components/research-pages";

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

const summary = {
  id: "CONV-LIVE-42",
  tenant_pseudonym: "SME-LIVE-17",
  participant_pseudonyms: ["USR-LIVE-9"],
  channel: "whatsapp",
  event_count: 2,
  primary_intents: { sales_analysis: 1, inventory_management: 1 },
  latest_redacted_text: "What should I restock next?",
  started_at: "2026-07-12T09:20:00Z",
  last_activity_at: "2026-07-12T09:42:00Z",
};

describe("research conversation explorer", () => {
  it("loads live pseudonymous summaries and opens the redacted event timeline", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/research/conversations"))
          return jsonResponse([summary]);
        if (url.endsWith("/research/conversations/CONV-LIVE-42")) {
          return jsonResponse({
            ...summary,
            events: [
              {
                id: "EVT-LIVE-1",
                user_pseudonym: "USR-LIVE-9",
                channel: "whatsapp",
                event_type: "question",
                redacted_text: "Which products sold best this week?",
                primary_intent: "sales_analysis",
                business_function: "sales",
                ai_help_type: "data_lookup",
                complexity: "simple_lookup",
                bumpa_data_used: "products",
                created_at: "2026-07-12T09:20:00Z",
              },
              {
                id: "EVT-LIVE-2",
                user_pseudonym: "USR-LIVE-9",
                channel: "whatsapp",
                event_type: "question",
                redacted_text: "What should I restock next?",
                primary_intent: "inventory_management",
                business_function: "stock",
                ai_help_type: "recommendation",
                complexity: "single_step_reasoning",
                bumpa_data_used: "products,orders",
                created_at: "2026-07-12T09:42:00Z",
              },
            ],
          });
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<Conversations />);

    expect(
      await screen.findByText(/Live research conversations · 1 record/),
    ).toBeInTheDocument();
    expect(screen.getByText("CONV-LIVE-42")).toBeInTheDocument();
    expect(screen.getByText("SME-LIVE-17")).toBeInTheDocument();
    expect(screen.getByText("What should I restock next?")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Open CONV-LIVE-42" }));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
    expect(
      screen.getByText("Which products sold best this week?"),
    ).toBeInTheDocument();
    expect(screen.getAllByText("USR-LIVE-9").length).toBeGreaterThan(0);
    expect(
      screen.getByText(
        /raw identities and raw message text are not available/i,
      ),
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("shows an honest empty state when no consented conversations exist", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(jsonResponse([]));
    render(<Conversations />);
    expect(
      await screen.findByText("No consented conversations yet"),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/no consented research records for this view/i),
    ).toBeInTheDocument();
  });

  it("keeps the summary visible and reports a failed detail request", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) =>
      String(input).endsWith("/research/conversations")
        ? jsonResponse([summary])
        : jsonResponse({ detail: "Research conversation not found" }, 404),
    );
    render(<Conversations />);
    fireEvent.click(
      await screen.findByRole("button", { name: "Open CONV-LIVE-42" }),
    );
    expect(
      await screen.findByText("Conversation unavailable"),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Research conversation not found"),
    ).toBeInTheDocument();
    await waitFor(() =>
      expect(screen.getByText("CONV-LIVE-42")).toBeInTheDocument(),
    );
  });
});
