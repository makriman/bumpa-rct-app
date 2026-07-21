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
import type { McpConnection, McpRegistryItem } from "@/lib/platform-data";

const apiRequest = vi.hoisted(() => vi.fn());
const reload = vi.hoisted(() => vi.fn(() => Promise.resolve()));
let connectionRows: McpConnection[] = [];

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
      { name: "append_rows", label: "Append spreadsheet rows", kind: "write" },
    ],
  },
];

vi.mock("@/lib/api", () => ({ apiRequest, demoFallbackEnabled: false }));
vi.mock("@/lib/use-api-resource", () => ({
  useApiResource: (path: string) => ({
    data: path === "/mcp/registry" ? registry : connectionRows,
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
  apiRequest.mockReset();
  reload.mockClear();
});

describe("consumer connection lifecycle", () => {
  it("requests read-only access through explicit confirmation", async () => {
    apiRequest.mockResolvedValue({ status: "admin_pending" });
    render(<McpPage />);
    fireEvent.click(screen.getByRole("button", { name: "Request read-only" }));
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
  });

  it("requires confirmation before enabling a write tool", async () => {
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
});
