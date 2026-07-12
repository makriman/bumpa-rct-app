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
  source: "live" as "live" | null,
  data: [] as Array<{
    user_id: string;
    name: string | null;
    phone_e164: string;
    status: string;
    platform_roles: Array<"operator" | "superadmin">;
    created_at: string;
  }> | null,
}));

const currentAdmin = {
  user_id: "admin-current",
  name: "Maks Admin",
  phone_e164: "+441234567890",
  status: "active",
  platform_roles: ["operator", "superadmin"] as Array<
    "operator" | "superadmin"
  >,
  created_at: "2026-07-12T12:00:00Z",
};

const otherAdmin = {
  user_id: "admin-other",
  name: "Ada Operator",
  phone_e164: "+2348012345678",
  status: "active",
  platform_roles: ["operator"] as Array<"operator" | "superadmin">,
  created_at: "2026-07-13T09:00:00Z",
};

vi.mock("@/lib/api", () => ({
  apiRequest,
  demoFallbackEnabled: false,
}));

vi.mock("@/lib/use-api-resource", () => ({
  useApiResource: (path: string) =>
    path === "/admin/platform-admins"
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
    expect(screen.getByText("Current administrator")).toBeInTheDocument();
    expect(screen.queryByText(currentAdmin.phone_e164)).not.toBeInTheDocument();
    expect(screen.getByText("+44123 ••• 7890")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", {
        name: "Revoke Maks Admin's platform access",
      }),
    ).not.toBeInTheDocument();
  });

  it("validates and grants operator access without exposing secrets", async () => {
    apiRequest.mockResolvedValueOnce({
      ...otherAdmin,
      user_id: "admin-new",
      name: "Nneka Admin",
    });
    render(<UserList />);

    fireEvent.click(
      screen.getByRole("button", { name: "＋ Add administrator" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Grant administrator access" }),
    );
    expect(screen.getByText("Enter the administrator's name.")).toBeVisible();
    expect(screen.getByText(/Use E\.164 format/)).toBeVisible();

    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "  Nneka Admin  " },
    });
    fireEvent.change(screen.getByLabelText("WhatsApp phone number"), {
      target: { value: "+2348098765432" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Grant administrator access" }),
    );

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith("/admin/platform-admins", {
        method: "POST",
        body: JSON.stringify({
          name: "Nneka Admin",
          phone_e164: "+2348098765432",
          role: "operator",
        }),
      }),
    );
    expect(reloadAdmins).toHaveBeenCalledTimes(1);
    expect(
      (
        await screen.findByText(
          "Nneka Admin can now administer tenant mappings.",
        )
      ).closest('[role="status"]'),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("heading", { name: "Add a platform administrator" }),
    ).not.toBeInTheDocument();
  });

  it("discards the administrator draft when Escape closes the dialog", () => {
    render(<UserList />);
    fireEvent.click(
      screen.getByRole("button", { name: "＋ Add administrator" }),
    );
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Discard this draft" },
    });
    fireEvent.change(screen.getByLabelText("WhatsApp phone number"), {
      target: { value: "+2348098765433" },
    });

    fireEvent.keyDown(document, { key: "Escape" });
    expect(
      screen.queryByRole("heading", { name: "Add a platform administrator" }),
    ).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "＋ Add administrator" }),
    );
    expect(screen.getByLabelText("Full name")).toHaveValue("");
    expect(screen.getByLabelText("WhatsApp phone number")).toHaveValue("");
  });

  it("requires an explicit confirmation before revoking access", async () => {
    apiRequest.mockResolvedValueOnce(undefined);
    render(<UserList />);

    fireEvent.click(
      screen.getByRole("button", {
        name: "Revoke Ada Operator's platform access",
      }),
    );
    expect(
      screen.getByRole("heading", { name: "Revoke platform access?" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Any store membership they hold remains unchanged/),
    ).toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "Revoke platform access" }),
    );

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        "/admin/platform-admins/admin-other",
        { method: "DELETE" },
      ),
    );
    expect(reloadAdmins).toHaveBeenCalledTimes(1);
    expect(
      (
        await screen.findByText("Ada Operator's platform access was revoked.")
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
        name: "Revoke Ada Operator's platform access",
      }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Revoke platform access" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The last active administrator cannot be revoked",
    );
    expect(
      screen.getByRole("heading", { name: "Revoke platform access?" }),
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
        name: "Administrators could not be loaded",
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
        name: "No platform administrators returned",
      }),
    ).toBeInTheDocument();
  });
});
