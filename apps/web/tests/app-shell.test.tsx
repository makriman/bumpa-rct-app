import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AppShell, crossSurfaceHref } from "@/components/app-shell";

const replace = vi.fn();
const apiRequest = vi.hoisted(() => vi.fn());
const router = { replace };

vi.mock("next/navigation", () => ({
  usePathname: () => "/chat",
  useRouter: () => router,
}));

vi.mock("@/lib/api", () => ({
  demoFallbackEnabled: false,
  apiRequest,
}));

const session = {
  user: {
    id: "user-1",
    name: "Ada Owner",
    email: null,
    phone_e164: "+2348012345678",
  },
  platform_roles: [],
  memberships: [
    {
      id: "membership-1",
      tenant_id: "tenant-1",
      role: "owner",
      status: "active",
    },
  ],
  current_tenant_id: "tenant-1",
};

afterEach(() => {
  cleanup();
  replace.mockReset();
  apiRequest.mockReset();
});

describe("workspace mobile navigation", () => {
  it("routes workspace switches across the dedicated production hosts", () => {
    expect(
      crossSurfaceHref(
        "tenant",
        "https://admin.bumpabestie.165-227-228-20.sslip.io/admin",
      ),
    ).toBe("https://bumpabestie.165-227-228-20.sslip.io/chat");
    expect(
      crossSurfaceHref(
        "admin",
        "https://bumpabestie.165-227-228-20.sslip.io/chat",
      ),
    ).toBe("https://admin.bumpabestie.165-227-228-20.sslip.io/admin");
    expect(
      crossSurfaceHref(
        "admin",
        "https://www.bumpabestie.165-227-228-20.sslip.io/chat?view=owner#latest",
      ),
    ).toBe("https://admin.bumpabestie.165-227-228-20.sslip.io/admin");
  });

  it("offers a direct workspace switch for a dual-role administrator", async () => {
    apiRequest.mockResolvedValueOnce({
      ...session,
      platform_roles: ["operator", "superadmin"],
    });
    render(
      <AppShell surface="admin" title="Platform administration">
        <p>Admin content</p>
      </AppShell>,
    );

    const switcher = await screen.findByRole("link", {
      name: "Switch to your tenant workspace",
    });
    expect(switcher).toHaveAttribute("href", "/chat");
    expect(screen.getByText("Open your store membership")).toBeVisible();
  });

  it("offers a direct return to administration from a tenant workspace", async () => {
    apiRequest.mockResolvedValueOnce({
      ...session,
      platform_roles: ["operator"],
    });
    render(
      <AppShell surface="user" title="Bestie chat">
        <p>Workspace content</p>
      </AppShell>,
    );

    const switcher = await screen.findByRole("link", {
      name: "Switch to platform administration",
    });
    expect(switcher).toHaveAttribute("href", "/admin");
    expect(screen.getByText("Manage tenant mappings")).toBeVisible();
    expect(
      screen.queryByRole("link", { name: "Administrators" }),
    ).not.toBeInTheDocument();
  });

  it("shows administrator management only to superadmins", async () => {
    apiRequest.mockResolvedValueOnce({
      ...session,
      platform_roles: ["superadmin"],
    });
    render(
      <AppShell surface="admin" title="Platform administration">
        <p>Admin content</p>
      </AppShell>,
    );

    expect(
      await screen.findByRole("link", { name: "Administrators" }),
    ).toBeVisible();
  });

  it("moves focus into the drawer, makes the page inert, and restores focus on Escape", async () => {
    apiRequest.mockResolvedValueOnce(session);
    render(
      <AppShell surface="user" title="Bestie chat">
        <p>Workspace content</p>
      </AppShell>,
    );

    const trigger = await screen.findByRole("button", {
      name: "Open navigation",
    });
    fireEvent.click(trigger);

    const home = screen.getByRole("link", { name: "Bumpa Bestie home" });
    await waitFor(() => expect(home).toHaveFocus());
    expect(trigger).toHaveAttribute("aria-expanded", "true");
    expect(
      screen.getByText("Workspace content").closest(".app-main"),
    ).toHaveAttribute("inert");

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(trigger).toHaveAttribute("aria-expanded", "false");
    expect(
      screen.getByText("Workspace content").closest(".app-main"),
    ).not.toHaveAttribute("inert");
  });

  it("revokes the session before redirecting to login", async () => {
    let finishLogout: (value: { message: string }) => void = () => undefined;
    const logoutResponse = new Promise<{ message: string }>((resolve) => {
      finishLogout = resolve;
    });
    apiRequest
      .mockResolvedValueOnce(session)
      .mockReturnValueOnce(logoutResponse);

    render(
      <AppShell surface="user" title="Bestie chat">
        <p>Workspace content</p>
      </AppShell>,
    );

    const logout = await screen.findByRole("button", { name: "Log out" });
    fireEvent.click(logout);

    expect(apiRequest).toHaveBeenLastCalledWith("/auth/logout", {
      method: "POST",
    });
    expect(screen.getByRole("button", { name: "Logging out…" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Logging out…" }),
    ).toHaveAttribute("aria-busy", "true");
    expect(replace).not.toHaveBeenCalled();

    finishLogout({ message: "Logged out" });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("announces a logout failure and allows another attempt", async () => {
    apiRequest
      .mockResolvedValueOnce(session)
      .mockRejectedValueOnce(new Error("Logout service unavailable"));

    render(
      <AppShell surface="user" title="Bestie chat">
        <p>Workspace content</p>
      </AppShell>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Logout service unavailable",
    );
    expect(screen.getByRole("button", { name: "Log out" })).toBeEnabled();
    expect(replace).not.toHaveBeenCalled();
  });
});
