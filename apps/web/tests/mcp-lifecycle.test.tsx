import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import McpPage from "@/app/settings/mcp/page";
import { McpApprovals } from "@/components/mcp-admin-page";
import type {
  McpAdminConnection,
  McpConnection,
  McpRegistryItem,
} from "@/lib/platform-data";

const apiRequest = vi.hoisted(() => vi.fn());
const reload = vi.hoisted(() => vi.fn(() => Promise.resolve()));
let connectionRows: McpConnection[] = [];
let adminRows: McpAdminConnection[] = [];

const registry: McpRegistryItem[] = [
  {
    provider: "google_sheets",
    name: "Google Sheets",
    enabled: true,
    default_mode: "read_only",
    tools: [
      {
        name: "read_sheet",
        label: "Read an approved spreadsheet",
        kind: "read",
      },
      {
        name: "append_rows",
        label: "Append spreadsheet rows",
        kind: "write",
      },
    ],
  },
];

vi.mock("@/lib/api", () => ({
  apiRequest,
  demoFallbackEnabled: false,
}));

vi.mock("@/lib/use-api-resource", () => ({
  useApiResource: (path: string) => ({
    data:
      path === "/mcp/registry"
        ? registry
        : path.startsWith("/admin/")
          ? adminRows
          : connectionRows,
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
  connectionRows = [];
  adminRows = [];
  apiRequest.mockReset();
  reload.mockClear();
  window.history.replaceState({}, "", "/");
});

describe("MCP connection lifecycle", () => {
  it("requests read-only access through an explicit confirmation", async () => {
    apiRequest.mockResolvedValue({ status: "admin_pending" });
    render(<McpPage />);

    fireEvent.click(screen.getByRole("button", { name: "Request read-only" }));
    expect(
      screen.getByRole("heading", { name: "Request Google Sheets" }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Confirm request" }));

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith("/settings/mcp-connections", {
        method: "POST",
        body: JSON.stringify({
          provider: "google_sheets",
          scopes: [],
          read_only: true,
        }),
      }),
    );
    expect(reload).toHaveBeenCalled();
    expect(
      await screen.findByText(/platform operator must approve it/i),
    ).toBeInTheDocument();
  });

  it("requires a second confirmation before enabling a write tool", async () => {
    connectionRows = [
      {
        id: "connection-live",
        provider: "google_sheets",
        status: "active",
        scopes: ["https://www.googleapis.com/auth/spreadsheets"],
        read_only: false,
        admin_approved: true,
        oauth_available: true,
        permissions: { read_sheet: "read", append_rows: "deny" },
      },
    ];
    apiRequest.mockResolvedValue(connectionRows[0]);
    render(<McpPage />);

    fireEvent.click(screen.getByRole("button", { name: "Enable safely" }));
    expect(
      screen.getByRole("heading", { name: "Enable a confirmed-write tool" }),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Enable with confirmation" }),
    );

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        "/settings/mcp-connections/connection-live/permissions/append_rows",
        {
          method: "PATCH",
          body: JSON.stringify({
            permission: "write_with_confirmation",
            acknowledge_write_confirmation: true,
          }),
        },
      ),
    );
  });

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
