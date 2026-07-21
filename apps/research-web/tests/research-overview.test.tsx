import React from "react";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ResearchOverview } from "@/components/research-pages";
import {
  previewResearchEvents,
  previewResearchOverview,
} from "@/lib/preview-fixtures";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(data: unknown): Response {
  return new Response(JSON.stringify(data), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

describe("research overview measurement catalogue", () => {
  it("renders the live consent-safe adoption, behavior, operations, and retention evidence", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/research/overview"))
          return jsonResponse(previewResearchOverview);
        if (url.endsWith("/research/events"))
          return jsonResponse(previewResearchEvents);
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<ResearchOverview />);

    expect(
      await screen.findByText(/Live research overview/),
    ).toBeInTheDocument();
    for (const heading of [
      "Research consent",
      "Messages by channel",
      "Active users by channel",
      "Questions by category",
      "Business functions",
      "Reasoning complexity",
      "AI help type",
      "Bumpa data used",
      "Recurring problem areas",
      "Hermes response latency",
      "Bumpa sync freshness",
      "Research operations",
      "Retention by first-observed cohort",
      "Repeat usage by SME",
      "Common sales questions",
      "Common inventory questions",
      "Common customer questions",
      "Common advice requests",
      "Evidence completeness",
    ]) {
      expect(
        screen.getByRole("heading", { name: heading }),
      ).toBeInTheDocument();
    }
    expect(screen.getByText("SME-K4H2")).toBeInTheDocument();
    expect(screen.getAllByText("1.2s").length).toBeGreaterThan(0);
    expect(
      screen.getByText(/Which products sold best this week\?/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/tenant_[0-9a-f-]{8}/i)).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
