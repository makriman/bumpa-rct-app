import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { McpApprovals } from "@/components/mcp-admin-page";
import type { McpAdminConnection } from "@/lib/platform-data";

const apiRequest = vi.hoisted(() => vi.fn());
const reload = vi.hoisted(() => vi.fn(() => Promise.resolve()));
let adminRows: McpAdminConnection[] = [];

vi.mock("@/lib/api", () => ({
  apiRequest,
  demoFallbackEnabled: false,
}));

vi.mock("@/lib/use-api-resource", () => ({
  useApiResource: (path: string) => ({
    data: path.startsWith("/admin/") ? adminRows : [],
    status: "ready",
    source: "live",
    error: null,
    reload,
  }),
}));

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

afterEach(() => {
  cleanup();
  adminRows = [];
  apiRequest.mockReset();
  reload.mockClear();
  window.history.replaceState({}, "", "/");
});

describe("MCP approval lifecycle", () => {
  it("records an operator approval reason before OAuth can start", async () => {
    adminRows = [
      {
        id: "connection-pending",
        tenant_id: "tenant-001",
        tenant_name: "Anika Studio",
        created_by: "user-001",
        created_at: "2026-07-13T10:00:00Z",
        provider: "google_sheets",
        status: "admin_pending",
        scopes: ["https://www.googleapis.com/auth/spreadsheets.readonly"],
        read_only: true,
        admin_approved: false,
        oauth_available: true,
        permissions: {},
      },
    ];
    apiRequest.mockResolvedValue({ ...adminRows[0], status: "approved" });
    render(<McpApprovals />);

    fireEvent.click(screen.getByRole("button", { name: "Review approval" }));
    fireEvent.change(screen.getByLabelText("Decision reason"), {
      target: { value: "Verified business need and least privilege" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Approve request" }));

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        "/admin/mcp-connections/connection-pending",
        {
          method: "PATCH",
          body: JSON.stringify({
            decision: "approve",
            reason: "Verified business need and least privilege",
          }),
        },
      ),
    );
    expect(reload).toHaveBeenCalled();
  });
});
