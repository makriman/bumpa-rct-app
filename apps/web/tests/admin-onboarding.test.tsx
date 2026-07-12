import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Onboarding } from "@/components/admin-pages";

const apiRequest = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", () => ({
  apiRequest,
  demoFallbackEnabled: false,
}));

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

const tenant = {
  id: "tenant-live",
  slug: "anika-studio",
  name: "Anika Studio",
  status: "active",
  business_category: "fashion",
  country: "NG",
  city: "Lagos",
  timezone: "Africa/Lagos",
  currency_code: "NGN",
  research_consent_status: "pending",
};

function responseFor(path: string): unknown {
  if (path === "/admin/tenants") return tenant;
  if (path === "/admin/tenants/tenant-live/users") {
    return { user_id: "owner-live", membership_id: "membership-live" };
  }
  if (path === "/admin/tenants/tenant-live/phones") {
    return { id: "phone-live", status: "active" };
  }
  if (path === "/admin/tenants/tenant-live/hermes-profile") {
    return {
      id: "profile-live",
      profile_name: "tenant_anika_studio",
      status: "provisioning",
    };
  }
  throw new Error(`Unexpected API request: ${path}`);
}

async function reachBumpaStep() {
  fireEvent.change(screen.getByLabelText("Business name"), {
    target: { value: "Anika Studio" },
  });
  fireEvent.change(screen.getByLabelText("Slug"), {
    target: { value: "anika-studio" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save and continue →" }));

  expect(
    await screen.findByRole("heading", { name: "Owner" }),
  ).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Owner name"), {
    target: { value: "Ada Okafor" },
  });
  fireEvent.change(screen.getByLabelText("Phone in E.164 format"), {
    target: { value: "+2348012345678" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save and continue →" }));

  expect(
    await screen.findByRole("heading", { name: "WhatsApp identity" }),
  ).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Save and continue →" }));

  expect(
    await screen.findByRole("heading", { name: "Bumpa" }),
  ).toBeInTheDocument();
}

afterEach(() => {
  cleanup();
  apiRequest.mockReset();
});

describe("production operator onboarding", () => {
  it("verifies a write-only Bumpa credential and preserves exact Hermes state", async () => {
    let finishBumpa: (value: unknown) => void = () => undefined;
    const bumpaResponse = new Promise((resolve) => {
      finishBumpa = resolve;
    });
    apiRequest.mockImplementation((path: string) => {
      if (path === "/admin/tenants/tenant-live/bumpa") return bumpaResponse;
      return Promise.resolve(responseFor(path));
    });

    render(<Onboarding />);
    await reachBumpaStep();

    const secret = "bumpa-secret-never-render-again";
    const keyInput = screen.getByLabelText("Bumpa API key");
    expect(keyInput).toHaveAttribute("type", "password");
    expect(keyInput).toHaveAttribute("autocomplete", "off");
    fireEvent.change(keyInput, { target: { value: secret } });
    fireEvent.change(screen.getByLabelText("Business ID"), {
      target: { value: "business-1042" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Connect and verify Bumpa →" }),
    );

    const saving = screen.getByRole("button", { name: "Saving…" });
    expect(saving).toBeDisabled();
    expect(saving).toHaveAttribute("aria-busy", "true");
    expect(apiRequest).toHaveBeenLastCalledWith(
      "/admin/tenants/tenant-live/bumpa",
      {
        method: "POST",
        body: JSON.stringify({
          api_key: secret,
          scope_type: "business_id",
          scope_id: "business-1042",
          provider: "bumpa",
        }),
      },
    );

    finishBumpa({ id: "bumpa-live", status: "active", provider: "bumpa" });
    expect(
      await screen.findByRole("heading", { name: "Hermes" }),
    ).toBeInTheDocument();
    expect(screen.queryByDisplayValue(secret)).not.toBeInTheDocument();
    expect(screen.queryByText(secret)).not.toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "Provision Hermes profile →" }),
    );

    expect(
      await screen.findByRole("heading", { name: "Review" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Active · Bumpa")).toBeInTheDocument();
    expect(
      screen.getByText("Provisioning · tenant_anika_studio"),
    ).toBeInTheDocument();
    expect(apiRequest).toHaveBeenLastCalledWith(
      "/admin/tenants/tenant-live/hermes-profile",
      { method: "POST" },
    );
  });

  it("announces verification failures, clears the key, and permits a safe retry", async () => {
    apiRequest.mockImplementation((path: string) => {
      if (path === "/admin/tenants/tenant-live/bumpa") {
        return Promise.reject(
          new Error("Bumpa connection verification failed"),
        );
      }
      return Promise.resolve(responseFor(path));
    });

    render(<Onboarding />);
    await reachBumpaStep();

    fireEvent.change(screen.getByLabelText("Bumpa API key"), {
      target: { value: "invalid-secret" },
    });
    fireEvent.change(screen.getByLabelText("Business ID"), {
      target: { value: "business-1042" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Connect and verify Bumpa →" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Bumpa connection verification failed",
    );
    expect(screen.getByLabelText("Bumpa API key")).toHaveValue("");
    expect(screen.queryByText("invalid-secret")).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Connect and verify Bumpa →" }),
    ).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Bumpa API key"), {
      target: { value: "replacement-secret" },
    });
    expect(
      screen.getByRole("button", { name: "Connect and verify Bumpa →" }),
    ).toBeEnabled();
  });
});
