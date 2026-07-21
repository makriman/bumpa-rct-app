import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AppShell } from "@/components/app-shell";

const replace = vi.hoisted(() => vi.fn());
const apiRequest = vi.hoisted(() => vi.fn());

vi.mock("next/navigation", () => ({
  usePathname: () => "/profile",
}));

vi.mock("@/lib/browser-navigation", () => ({ replaceLocation: replace }));

vi.mock("@/lib/api", () => ({
  demoFallbackEnabled: false,
  apiRequest,
}));

const session = {
  user: { name: "Ada Owner" },
  memberships: [{ tenant_id: "tenant-1", role: "owner", status: "active" }],
  current_tenant_id: "tenant-1",
};

afterEach(() => {
  cleanup();
  replace.mockReset();
  apiRequest.mockReset();
});

describe("consumer account shell", () => {
  it("exposes only chat and account destinations", async () => {
    apiRequest.mockResolvedValueOnce(session);
    render(
      <AppShell title="Profile">
        <p>Account content</p>
      </AppShell>,
    );

    expect(
      await screen.findByRole("link", { name: "Bestie chat" }),
    ).toBeVisible();
    expect(screen.getByRole("link", { name: "Profile" })).toBeVisible();
    expect(screen.queryByText(/platform|research/i)).not.toBeInTheDocument();
  });

  it("traps drawer focus and restores it on Escape", async () => {
    apiRequest.mockResolvedValueOnce(session);
    render(
      <AppShell title="Profile">
        <p>Account content</p>
      </AppShell>,
    );

    const trigger = await screen.findByRole("button", {
      name: "Open navigation",
    });
    fireEvent.click(trigger);
    await waitFor(() =>
      expect(
        screen.getByRole("link", { name: "Bumpa Bestie home" }),
      ).toHaveFocus(),
    );
    expect(
      screen.getByText("Account content").closest(".app-main"),
    ).toHaveAttribute("inert");

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(trigger).toHaveFocus());
    expect(
      screen.getByText("Account content").closest(".app-main"),
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
      <AppShell title="Profile">
        <p>Account content</p>
      </AppShell>,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Log out" }));

    expect(apiRequest).toHaveBeenLastCalledWith("/auth/logout", {
      method: "POST",
    });
    expect(screen.getByRole("button", { name: "Logging out…" })).toBeDisabled();
    expect(replace).not.toHaveBeenCalled();

    finishLogout({ message: "Logged out" });
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
  });

  it("announces logout failure and allows retry", async () => {
    apiRequest
      .mockResolvedValueOnce(session)
      .mockRejectedValueOnce(new Error("Logout service unavailable"));
    render(
      <AppShell title="Profile">
        <p>Account content</p>
      </AppShell>,
    );

    fireEvent.click(await screen.findByRole("button", { name: "Log out" }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Logout service unavailable",
    );
    expect(screen.getByRole("button", { name: "Log out" })).toBeEnabled();
  });
});
