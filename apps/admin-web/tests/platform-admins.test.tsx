import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { UserList } from "@/components/admin-pages";

const apiRequest = vi.hoisted(() => vi.fn());
const reloadAdmins = vi.hoisted(() => vi.fn(() => Promise.resolve()));
const resource = vi.hoisted(() => ({
  status: "ready" as "loading" | "ready" | "error",
  error: null as string | null,
  source: "live" as "live" | "demo" | null,
  data: [] as Array<{
    user_id: string;
    name: string | null;
    phone_e164: string;
    status: string;
    has_active_mapping: boolean;
    platform_roles: Array<"operator" | "researcher" | "superadmin">;
    created_at: string;
  }> | null,
}));

const currentAdmin = {
  user_id: "admin-current",
  name: "Maks Admin",
  phone_e164: "+441234567890",
  status: "active",
  has_active_mapping: true,
  platform_roles: ["superadmin", "operator", "researcher"] as Array<
    "operator" | "researcher" | "superadmin"
  >,
  created_at: "2026-07-12T12:00:00Z",
};

const otherAdmin = {
  user_id: "admin-other",
  name: "Ada Operator",
  phone_e164: "+2348012345678",
  status: "active",
  has_active_mapping: true,
  platform_roles: ["operator"] as Array<
    "operator" | "researcher" | "superadmin"
  >,
  created_at: "2026-07-13T09:00:00Z",
};

const unmappedRoleHolder = {
  user_id: "researcher-unmapped",
  name: "Dormant Researcher",
  phone_e164: "+2348011112222",
  status: "active",
  has_active_mapping: false,
  platform_roles: ["researcher"] as Array<
    "operator" | "researcher" | "superadmin"
  >,
  created_at: "2026-07-13T10:00:00Z",
};

vi.mock("@/lib/api", () => ({
  apiRequest,
  demoFallbackEnabled: false,
}));

vi.mock("@/lib/use-api-resource", () => ({
  useApiResource: (path: string) =>
    path === "/admin/platform-access"
      ? {
          ...resource,
          reload: reloadAdmins,
          replace: vi.fn(),
        }
      : {
          data: {
            user: { id: "admin-current" },
            platform_roles: ["operator", "superadmin"],
            memberships: [
              {
                id: "membership-demo",
                tenant_id: "tenant-demo",
                role: "owner",
                status: "active",
              },
            ],
            current_tenant_id: "tenant-demo",
          },
          status: "ready",
          source: "live",
          error: null,
          reload: vi.fn(),
          replace: vi.fn(),
        },
}));

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

beforeEach(() => {
  resource.status = "ready";
  resource.error = null;
  resource.source = "live";
  resource.data = [currentAdmin, otherAdmin];
  apiRequest.mockReset();
  reloadAdmins.mockClear();
});

afterEach(cleanup);

describe("platform administrator management", () => {
  it("renders live masked identities and protects the current superadmin", () => {
    render(<UserList />);

    expect(screen.getByText("Maks Admin")).toBeInTheDocument();
    expect(screen.getByText("Your account")).toBeInTheDocument();
    expect(screen.getByText("Superadmin protected")).toBeInTheDocument();
    expect(screen.queryByText(currentAdmin.phone_e164)).not.toBeInTheDocument();
    expect(screen.getByText("+44123 ••• 7890")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Revoke Maks Admin's admin access",
      }),
    ).not.toBeInTheDocument();
  });

  it("keeps administrator mutations inert when the live API is unavailable", () => {
    resource.source = null;
    render(<UserList />);

    expect(
      screen.getByRole("link", { name: "Manage tenant mappings" }),
    ).toHaveAttribute("href", "/tenants");
    expect(screen.getByText("API unavailable")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Revoke Ada Operator's admin access",
      }),
    ).not.toBeInTheDocument();
  });

  it("grants access only from the mapped collaborator directory", () => {
    render(<UserList />);

    expect(
      screen.getByText(/map their primary phone to an active workspace first/i),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("link", { name: "Manage tenant mappings" }),
    ).toHaveAttribute("href", "/tenants");
    expect(
      screen.queryByRole("button", { name: /add administrator/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Add a platform administrator" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Grant Ada Operator's research access",
      }),
    ).toBeInTheDocument();
    expect(apiRequest).not.toHaveBeenCalled();
  });

  it("keeps unmapped role holders auditable without offering a new grant", () => {
    resource.data = [currentAdmin, unmappedRoleHolder];
    render(<UserList />);

    expect(screen.getByText("Mapping required")).toBeInTheDocument();
    expect(
      screen.getByRole("button", {
        name: "Grant Dormant Researcher's admin access",
      }),
    ).toBeDisabled();
    expect(
      screen.getByRole("button", {
        name: "Revoke Dormant Researcher's research access",
      }),
    ).toBeEnabled();
  });

  it("allows deprivileging a suspended holder while blocking new grants", () => {
    resource.data = [
      currentAdmin,
      { ...otherAdmin, status: "suspended", platform_roles: ["operator"] },
    ];
    render(<UserList />);

    expect(
      screen.getByRole("button", {
        name: "Revoke Ada Operator's admin access",
      }),
    ).toBeEnabled();
    expect(
      screen.getByRole("button", {
        name: "Grant Ada Operator's research access",
      }),
    ).toBeDisabled();
  });

  it("links the empty directory to tenant mapping", () => {
    resource.data = [];
    render(<UserList />);

    expect(
      screen.getByRole("link", { name: "Map a collaborator" }),
    ).toHaveAttribute("href", "/tenants");
    expect(
      screen.getByText(/Map a collaborator's primary phone/i),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /add administrator/i }),
    ).not.toBeInTheDocument();
  });

  it("requires an explicit confirmation before revoking access", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    render(<UserList />);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Revoke Ada Operator's admin access",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "Revoke admin access?" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Any store membership they hold remains unchanged/),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Revoke admin access" }),
    );

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        "/admin/platform-access/admin-other/operator",
        { method: "DELETE" },
      ),
    );
    expect(reloadAdmins).toHaveBeenCalledTimes(1);
    expect(
      (
        await screen.findByText("Ada Operator's admin access was revoked.")
      ).closest('[role="status"]'),
    ).toBeInTheDocument();
  });

  it("confirms and grants research access independently", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    render(<UserList />);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Grant Ada Operator's research access",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "Grant research access?" }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/consented, de-identified research tools/).length,
    ).toBeGreaterThan(0);
    fireEvent.click(
      screen.getByRole("button", { name: "Grant research access" }),
    );

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        "/admin/platform-access/admin-other/researcher",
        { method: "PUT" },
      ),
    );
    expect(reloadAdmins).toHaveBeenCalledTimes(1);
    expect(
      (
        await screen.findByText("Ada Operator now has research access.")
      ).closest('[role="status"]'),
    ).toBeInTheDocument();
  });

  it("keeps confirmation open and announces server-side revoke protection", async () => {
    apiRequest.mockRejectedValueOnce(
      new Error("The last active administrator cannot be revoked"),
    );
    render(<UserList />);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Revoke Ada Operator's admin access",
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Revoke admin access" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The last active administrator cannot be revoked",
    );
    expect(
      screen.getByRole("heading", { name: "Revoke admin access?" }),
    ).toBeInTheDocument();
    expect(reloadAdmins).not.toHaveBeenCalled();
  });

  it("shows loading, error, and empty states from the live API", () => {
    resource.status = "loading";
    resource.data = null;
    const { rerender } = render(<UserList />);
    expect(screen.getByText("Loading content")).toBeInTheDocument();

    resource.status = "error";
    resource.error = "Superadministrator access required";
    rerender(<UserList />);
    expect(
      screen.getByRole("heading", {
        name: "Platform access could not be loaded",
      }),
    ).toBeInTheDocument();
    expect(
      screen.getAllByText(/Superadministrator access required/).length,
    ).toBeGreaterThan(0);

    resource.status = "ready";
    resource.error = null;
    resource.data = [];
    rerender(<UserList />);
    expect(
      screen.getByRole("heading", {
        name: "No mapped collaborators returned",
      }),
    ).toBeInTheDocument();
  });
});
