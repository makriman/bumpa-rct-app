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

import McpPage from "@/app/settings/mcp/page";
import type { McpConnection, McpRegistryItem } from "@/lib/platform-data";

const apiRequest = vi.hoisted(() => vi.fn());
const reload = vi.hoisted(() => vi.fn(() => Promise.resolve()));
let connectionRows: McpConnection[] = [];

const defaultRegistry: McpRegistryItem[] = [
  {
    provider: "google_sheets",
    name: "Google Sheets",
    enabled: true,
    connection_method: "oauth",
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
let registryRows: McpRegistryItem[] = defaultRegistry;

vi.mock("@/lib/api", () => ({ apiRequest, demoFallbackEnabled: false }));
vi.mock("@/lib/use-api-resource", () => ({
  useApiResource: (path: string) => ({
    data: path === "/mcp/registry" ? registryRows : connectionRows,
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
  registryRows = defaultRegistry;
  apiRequest.mockReset();
  reload.mockClear();
});

describe("consumer connection lifecycle", () => {
  it("keeps reviewed OAuth connectors unavailable until their apps are ready", () => {
    render(<McpPage />);
    expect(
      screen.getByText("Coming soon", { selector: ".badge" }),
    ).toBeVisible();
    expect(
      screen.getByText(
        /Google Workspace and Meta Ads connections stay unavailable/,
      ),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "Request read-only" }),
    ).not.toBeInTheDocument();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("requires confirmation before enabling a write tool", async () => {
    registryRows = [
      {
        provider: "home_assistant",
        name: "Home Assistant",
        enabled: true,
        connection_method: "manual_token",
        default_mode: "read_only",
        tools: [
          {
            name: "call_service",
            label: "Call an approved service",
            kind: "write",
          },
        ],
      },
    ];
    connectionRows = [
      {
        id: "connection-live",
        provider: "home_assistant",
        status: "active",
        scopes: [],
        read_only: false,
        admin_approved: true,
        oauth_available: false,
        permissions: { call_service: "deny" },
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
        "/settings/mcp-connections/connection-live/permissions/call_service",
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

  it("connects an operator-approved Home Assistant instance without OAuth", async () => {
    registryRows = [
      {
        provider: "home_assistant",
        name: "Home Assistant",
        enabled: true,
        connection_method: "manual_token",
        default_mode: "read_only",
        tools: [
          {
            name: "get_state",
            label: "Read an approved entity state",
            kind: "read",
          },
        ],
      },
    ];
    connectionRows = [
      {
        id: "home-assistant-live",
        provider: "home_assistant",
        status: "approved",
        scopes: [],
        read_only: true,
        admin_approved: true,
        oauth_available: false,
        permissions: { get_state: "read" },
      },
    ];
    apiRequest.mockResolvedValue({
      ...connectionRows[0],
      status: "active",
    });
    render(<McpPage />);
    fireEvent.click(
      screen.getByRole("button", { name: "Connect trusted instance" }),
    );
    fireEvent.change(screen.getByLabelText("Home Assistant URL"), {
      target: { value: "https://shop-automation.example.com" },
    });
    fireEvent.change(screen.getByLabelText("Long-lived access token"), {
      target: { value: "dedicated-home-assistant-token" },
    });
    fireEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "Connect trusted instance",
      }),
    );
    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        "/settings/mcp-connections/home-assistant-live/home-assistant/connect",
        {
          method: "POST",
          body: JSON.stringify({
            base_url: "https://shop-automation.example.com",
            access_token: "dedicated-home-assistant-token",
          }),
        },
      ),
    );
  });
});
