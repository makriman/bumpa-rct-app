import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  AdminOverview,
  ProviderFailures,
  TenantDetail,
  UsageList,
} from "@/components/admin-pages";

const apiRequest = vi.hoisted(() => vi.fn());
const reloadOperations = vi.hoisted(() => vi.fn(() => Promise.resolve()));

const tenant = {
  id: "tenant-live-001",
  slug: "anika-studio",
  name: "Anika Studio",
  status: "active",
  business_category: "fashion",
  country: "NG",
  city: "Lagos",
  timezone: "Africa/Lagos",
  currency_code: "NGN",
  research_consent_status: "granted",
  created_at: "2026-07-01T10:00:00Z",
};

const operations = {
  tenant_id: tenant.id,
  people: [
    {
      membership_id: "membership-001",
      user_id: "user-001",
      name: "Ada Owner",
      phone_masked: "+23•••••5678",
      role: "owner",
      status: "active",
    },
  ],
  phones: [
    {
      id: "phone-001",
      user_id: "user-001",
      phone_masked: "+23•••••5678",
      label: "Owner",
      status: "approved",
      opt_out: false,
    },
  ],
  bumpa: {
    connected: true,
    status: "active",
    scope_type: "business_id",
    scope_id_last4: "1042",
    provider: "bumpa",
    last_successful_sync_at: "2026-07-12T10:00:00Z",
    last_failed_sync_at: null,
    last_error: null,
  },
  hermes: {
    provisioned: true,
    profile_name: "tenant_anika_studio",
    provider: "hermes",
    status: "active",
    api_port: 8704,
  },
};

vi.mock("@/lib/api", () => ({
  apiRequest,
  demoFallbackEnabled: false,
}));

vi.mock("@/lib/use-api-resource", () => ({
  useApiResource: (path: string) => {
    if (path.endsWith("/operations")) {
      return {
        data: operations,
        status: "ready",
        source: "live",
        error: null,
        reload: reloadOperations,
      };
    }
    if (path === `/admin/tenants/${tenant.id}`) {
      return {
        data: tenant,
        status: "ready",
        source: "live",
        error: null,
        reload: vi.fn(),
        replace: vi.fn(),
      };
    }
    if (path === "/admin/system/errors") {
      return {
        data: [
          {
            id: "system-error-1",
            tenant_id: tenant.id,
            service: "worker",
            severity: "error",
            message: "Bounded terminal-failure canary",
            created_at: "2026-07-14T09:00:00Z",
          },
        ],
        status: "ready",
        source: "live",
        error: null,
        reload: vi.fn(),
      };
    }
    if (path.includes("whatsapp-delivery-failures")) {
      return {
        data: [
          {
            id: "wa-failure-1",
            tenant_id: tenant.id,
            message_reference: "a1b2c3d4e5f6",
            phone_masked: "+23•••••5678",
            status: "failed",
            provider_error_code: "131000",
            provider_error_title: "Message failed to send",
            created_at: "2026-07-12T10:00:00Z",
          },
        ],
        status: "ready",
        source: "live",
        error: null,
        reload: vi.fn(),
      };
    }
    if (path.includes("hermes-call-errors")) {
      return {
        data: [
          {
            id: "hermes-failure-1",
            tenant_id: tenant.id,
            category: "hermes_unavailable",
            retryable: true,
            profile_reference: "b2c3d4e5f6a1",
            created_at: "2026-07-12T11:00:00Z",
          },
        ],
        status: "ready",
        source: "live",
        error: null,
        reload: vi.fn(),
      };
    }
    return {
      data: [],
      status: "ready",
      source: "live",
      error: null,
      reload: vi.fn(),
    };
  },
}));

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

afterEach(() => {
  cleanup();
  apiRequest.mockReset();
  reloadOperations.mockClear();
  vi.restoreAllMocks();
});

describe("operator tenant operations", () => {
  it("shows bounded mappings and performs confirmed sync and profile restart mutations", async () => {
    apiRequest.mockResolvedValue({ status: "queued" });
    render(<TenantDetail id={tenant.id} />);

    fireEvent.click(screen.getByRole("tab", { name: "People & phones" }));
    expect(screen.getByText("Ada Owner")).toBeInTheDocument();
    expect(screen.getAllByText("+23•••••5678").length).toBeGreaterThan(0);
    expect(screen.queryByText("+2348012345678")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("tab", { name: "Bumpa" }));
    expect(screen.getByText("••••1042")).toBeInTheDocument();
    expect(screen.queryByText(/api[_ -]?key=/i)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Trigger sync" }));
    fireEvent.click(screen.getByRole("button", { name: "Queue refresh" }));
    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        `/admin/tenants/${tenant.id}/bumpa/sync`,
        expect.objectContaining({
          method: "POST",
          headers: { "Idempotency-Key": expect.stringContaining("admin-") },
        }),
      ),
    );

    fireEvent.click(screen.getByRole("tab", { name: "Hermes" }));
    expect(screen.getByText("tenant_anika_studio")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Restart profile" }));
    fireEvent.click(
      screen.getByRole("dialog").querySelector(".button-danger")!,
    );
    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        `/admin/tenants/${tenant.id}/hermes-profile/restart`,
        {
          method: "POST",
          body: JSON.stringify({
            reason: "operator_health_recovery",
            confirmation: "restart_hermes_profile",
          }),
        },
      ),
    );
    expect(reloadOperations).toHaveBeenCalled();
  });

  it("provides functional member and WhatsApp approval forms", () => {
    render(<TenantDetail id={tenant.id} />);
    fireEvent.click(screen.getByRole("button", { name: "Edit details" }));
    expect(
      screen.getByRole("heading", { name: "Edit tenant details" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Business name")).toHaveValue("Anika Studio");
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByRole("tab", { name: "People & phones" }));
    fireEvent.click(screen.getByRole("button", { name: "Add person" }));
    expect(
      screen.getByRole("heading", { name: "Add tenant member" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Tenant role")).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));

    fireEvent.click(screen.getByRole("button", { name: "Approve number" }));
    expect(
      screen.getByRole("heading", { name: "Approve WhatsApp number" }),
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Tenant member")).toHaveValue("");
  });
});

describe("admin overview semantics", () => {
  it("describes retained error records without inventing an open state", () => {
    render(<AdminOverview />);

    expect(screen.getByText("Recent system errors")).toBeInTheDocument();
    expect(
      screen.getByText("Bounded recent records returned by the API"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Open system errors")).not.toBeInTheDocument();
  });
});

describe("provider failure operations", () => {
  it("shows safe WhatsApp and Hermes diagnostics without raw payload controls", () => {
    render(<ProviderFailures />);
    expect(screen.getByText("131000")).toBeInTheDocument();
    expect(screen.getByText("a1b2c3d4e5f6")).toBeInTheDocument();
    expect(screen.getByText("Hermes Unavailable")).toBeInTheDocument();
    expect(screen.getByText("b2c3d4e5f6a1")).toBeInTheDocument();
    expect(screen.queryByText(/private prompt/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/secret stack/i)).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /resolve/i }),
    ).not.toBeInTheDocument();
  });
});

describe("audited admin export", () => {
  it("requests a confirmation-gated server export instead of serializing loaded rows", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const createObjectURL = vi.fn(() => "blob:admin-export");
    const revokeObjectURL = vi.fn();
    Object.defineProperty(URL, "createObjectURL", {
      configurable: true,
      value: createObjectURL,
    });
    Object.defineProperty(URL, "revokeObjectURL", {
      configurable: true,
      value: revokeObjectURL,
    });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(
      () => undefined,
    );
    apiRequest.mockResolvedValue({
      export_id: "export-001",
      filename: "bumpa-bestie-admin-operations-2026-07-13.csv",
      content_type: "text/csv",
      content: "tenant_id,status\ntenant-live-001,active\n",
      row_count: 1,
      checksum_sha256: "a".repeat(64),
    });

    render(<UsageList />);
    fireEvent.click(
      screen.getByRole("button", { name: "Generate audited export" }),
    );

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith("/admin/exports", {
        method: "POST",
        body: JSON.stringify({
          scope: "tenant_operations",
          format: "csv",
          confirmation: "generate_admin_export",
        }),
      }),
    );
    expect(createObjectURL).toHaveBeenCalledTimes(1);
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:admin-export");
    expect(
      await screen.findByText("1 tenant rows exported and audit logged."),
    ).toBeInTheDocument();
  });
});
