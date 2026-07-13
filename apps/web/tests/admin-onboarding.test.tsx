import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ResumableOnboarding,
  ResumableOnboardingStart,
} from "@/components/admin-onboarding";
import { ApiError } from "@/lib/api";
import type { OnboardingStep, TenantOnboarding } from "@/lib/platform-data";

const apiRequest = vi.hoisted(() => vi.fn());
const push = vi.hoisted(() => vi.fn());

vi.mock("@/lib/api", async (importOriginal) => {
  const original = await importOriginal<typeof import("@/lib/api")>();
  return { ...original, apiRequest };
});

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push }),
}));

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

function onboarding(
  currentStep: OnboardingStep,
  overrides: Partial<TenantOnboarding> = {},
): TenantOnboarding {
  const index = [
    "owner",
    "phone",
    "bumpa",
    "initial_sync",
    "hermes",
    "review",
    "completed",
  ].indexOf(currentStep);
  return {
    id: "onboarding-live",
    tenant_id: "tenant-live",
    status: currentStep === "completed" ? "completed" : "in_progress",
    current_step: currentStep,
    revision: index + 1,
    tenant: {
      id: "tenant-live",
      slug: "anika-studio",
      name: "Anika Studio",
      status: currentStep === "completed" ? "active" : "provisioning",
    },
    owner:
      index > 0
        ? {
            user_id: "owner-live",
            membership_id: "membership-live",
            name: "Ada Okafor",
            email_masked: "a•••@example.com",
            status: "active",
          }
        : null,
    phone:
      index > 1
        ? {
            identity_id: "phone-live",
            label: "Owner",
            phone_masked: "+23480••••678",
            status: "active",
            opt_out: false,
          }
        : null,
    bumpa:
      index > 2
        ? {
            connection_id: "bumpa-live",
            provider: "bumpa",
            scope_type: "business_id",
            scope_id_last4: "1042",
            status: "active",
          }
        : null,
    initial_sync:
      index > 3
        ? {
            attempt: 1,
            requested_from: "2026-06-13",
            requested_to: "2026-07-13",
            job_id: "job-live",
            job_status: "succeeded",
            sync_run_id: "sync-live",
            sync_status: "success",
            completion_quality: "complete",
            orders_availability: "available",
            orders_count: 42,
          }
        : null,
    hermes:
      index > 4
        ? {
            profile_id: "profile-live",
            profile_name: "tenant_anika_studio",
            provider: "hermes",
            status: "active",
            api_port: 4012,
          }
        : null,
    failure: null,
    created_at: "2026-07-13T10:00:00Z",
    updated_at: "2026-07-13T10:05:00Z",
    completed_at: currentStep === "completed" ? "2026-07-13T10:05:00Z" : null,
    ...overrides,
  };
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  apiRequest.mockReset();
  push.mockReset();
  window.sessionStorage.clear();
});

describe("resumable production onboarding", () => {
  it("recovers a committed owner command after its response is lost", async () => {
    const before = onboarding("owner");
    const recovered = onboarding("phone");
    let getCount = 0;
    apiRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/admin/onboardings/onboarding-live" && !init) {
        getCount += 1;
        return Promise.resolve(getCount === 1 ? before : recovered);
      }
      if (path.endsWith("/owner")) {
        return Promise.reject(new TypeError("Network connection lost"));
      }
      throw new Error(`Unexpected API request: ${path}`);
    });

    render(<ResumableOnboarding onboardingId="onboarding-live" />);
    expect(await screen.findByRole("heading", { name: "Owner" })).toBeVisible();
    fireEvent.change(screen.getByLabelText("Owner name"), {
      target: { value: "Ada Okafor" },
    });
    fireEvent.change(screen.getByLabelText("Phone in E.164 format"), {
      target: { value: "+15550102716" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Save owner →" }));

    expect(
      await screen.findByRole("heading", { name: "WhatsApp identity" }),
    ).toBeVisible();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    const commandCall = apiRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/owner"),
    );
    expect(commandCall?.[1]).toMatchObject({
      method: "POST",
      headers: {
        "Idempotency-Key": expect.any(String),
        "If-Match": "1",
      },
    });
  });

  it("keeps the Bumpa key in memory only and clears it after failure", async () => {
    const view = onboarding("bumpa");
    apiRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/admin/onboardings/onboarding-live" && !init) {
        return Promise.resolve(view);
      }
      if (path.endsWith("/bumpa")) {
        return Promise.reject(
          new ApiError({
            status: 502,
            code: "bumpa_verification_failed",
            message: "Bumpa verification failed",
            correlationId: null,
            retryable: true,
          }),
        );
      }
      throw new Error(`Unexpected API request: ${path}`);
    });

    render(<ResumableOnboarding onboardingId="onboarding-live" />);
    const key = await screen.findByLabelText("Bumpa API key");
    fireEvent.change(key, { target: { value: "bumpa-secret-never-persist" } });
    fireEvent.change(screen.getByLabelText("Business ID"), {
      target: { value: "business-1042" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Connect and verify Bumpa →" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Bumpa verification failed",
    );
    expect(screen.getByLabelText("Bumpa API key")).toHaveValue("");
    expect(JSON.stringify(window.sessionStorage)).not.toContain(
      "bumpa-secret-never-persist",
    );
    expect(
      window.sessionStorage.getItem(
        "bumpa-bestie:onboarding:onboarding-live:bumpa",
      ),
    ).toBeNull();
    expect(
      screen.queryByText("bumpa-secret-never-persist"),
    ).not.toBeInTheDocument();

    const firstCommand = apiRequest.mock.calls.find(([path]) =>
      String(path).endsWith("/bumpa"),
    );
    fireEvent.change(screen.getByLabelText("Bumpa API key"), {
      target: { value: "replacement-secret" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Connect and verify Bumpa →" }),
    );
    await waitFor(() => {
      const commands = apiRequest.mock.calls.filter(([path]) =>
        String(path).endsWith("/bumpa"),
      );
      expect(commands).toHaveLength(2);
      expect(
        (commands[1][1]?.headers as Record<string, string>)["Idempotency-Key"],
      ).not.toBe(
        (firstCommand?.[1]?.headers as Record<string, string>)[
          "Idempotency-Key"
        ],
      );
    });
  });

  it("resumes from server state after a reload and never unlocks Hermes before sync acceptance", async () => {
    const syncing = onboarding("initial_sync", {
      initial_sync: {
        attempt: 1,
        requested_from: "2026-06-13",
        requested_to: "2026-07-13",
        job_id: "job-live",
        job_status: "succeeded",
        sync_run_id: "sync-live",
        sync_status: "success",
        completion_quality: "complete",
        orders_availability: "available",
        orders_count: 42,
      },
    });
    apiRequest.mockResolvedValue(syncing);

    const first = render(
      <ResumableOnboarding onboardingId="onboarding-live" />,
    );
    expect(
      await screen.findByRole("heading", { name: "Initial data sync" }),
    ).toBeVisible();
    expect(
      screen.queryByRole("button", { name: /Provision and verify Hermes/ }),
    ).not.toBeInTheDocument();
    first.unmount();

    render(<ResumableOnboarding onboardingId="onboarding-live" />);
    expect(
      await screen.findByRole("button", {
        name: "Validate sync and continue →",
      }),
    ).toBeEnabled();
    fireEvent.click(
      screen.getByRole("button", { name: "Validate sync and continue →" }),
    );

    await waitFor(() => {
      const acceptCall = apiRequest.mock.calls.find(([path]) =>
        String(path).endsWith("/initial-sync/accept"),
      );
      expect(acceptCall?.[1]).toMatchObject({
        method: "POST",
        body: JSON.stringify({ confirmation: "accept" }),
      });
      expect(acceptCall?.[1]?.body).not.toContain("job-live");
    });
  });

  it("starts with one stable idempotency key when the first response is lost", async () => {
    const created = onboarding("owner");
    apiRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/admin/onboardings" && !init) return Promise.resolve([]);
      if (path === "/admin/onboardings" && init?.method === "POST") {
        const postCount = apiRequest.mock.calls.filter(
          ([candidate, candidateInit]) =>
            candidate === path && candidateInit?.method === "POST",
        ).length;
        return postCount === 1
          ? Promise.reject(new TypeError("response lost"))
          : Promise.resolve(created);
      }
      throw new Error(`Unexpected API request: ${path}`);
    });

    render(<ResumableOnboardingStart />);
    await screen.findByText("No unfinished onboarding records.");
    fireEvent.change(screen.getByLabelText("Business name"), {
      target: { value: "Anika Studio" },
    });
    fireEvent.change(screen.getByLabelText("Slug"), {
      target: { value: "anika-studio" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Start onboarding →" }));

    await waitFor(() =>
      expect(push).toHaveBeenCalledWith("/admin/onboarding/onboarding-live"),
    );
    const posts = apiRequest.mock.calls.filter(
      ([path, init]) =>
        path === "/admin/onboardings" && init?.method === "POST",
    );
    expect(posts).toHaveLength(2);
    expect(posts[0][1]?.headers).toEqual(posts[1][1]?.headers);
    expect(posts[0][1]?.body).toBe(posts[1][1]?.body);
    expect(JSON.parse(String(posts[0][1]?.body))).toEqual({
      slug: "anika-studio",
      name: "Anika Studio",
      business_category: null,
      country: "NG",
      city: null,
      timezone: "Africa/Lagos",
      currency_code: "NGN",
    });
  });

  it("allows an accepted partial sync only when orders are available", async () => {
    const partial = onboarding("initial_sync", {
      initial_sync: {
        attempt: 1,
        requested_from: "2026-06-13",
        requested_to: "2026-07-13",
        job_id: "job-partial",
        job_status: "succeeded",
        sync_run_id: "sync-partial",
        sync_status: "partial",
        completion_quality: "accepted_partial",
        orders_availability: "available",
        orders_count: 7,
      },
    });
    apiRequest.mockResolvedValue(partial);

    render(<ResumableOnboarding onboardingId="onboarding-live" />);

    expect(
      await screen.findByRole("button", {
        name: "Validate sync and continue →",
      }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: "Retry initial sync →" }),
    ).not.toBeInTheDocument();
  });

  it("keeps Hermes locked and offers a new controlled attempt after an unusable sync", async () => {
    const failed = onboarding("initial_sync", {
      revision: 9,
      status: "attention_required",
      initial_sync: {
        attempt: 2,
        requested_from: "2026-06-13",
        requested_to: "2026-07-13",
        job_id: "job-dead",
        job_status: "dead_letter",
        sync_run_id: "sync-failed",
        sync_status: "failed",
        completion_quality: "failed",
        orders_availability: "unavailable",
        orders_count: null,
      },
      failure: {
        code: "initial_sync_failed",
        step: "initial_sync",
        retryable: true,
        at: "2026-07-13T10:05:00Z",
      },
    });
    const retrying = onboarding("initial_sync", {
      revision: 10,
      initial_sync: {
        ...failed.initial_sync!,
        attempt: 3,
        job_id: "job-retry",
        job_status: "queued",
        sync_run_id: null,
        sync_status: null,
        completion_quality: null,
        orders_availability: null,
      },
    });
    apiRequest.mockImplementation((path: string, init?: RequestInit) => {
      if (path === "/admin/onboardings/onboarding-live" && !init) {
        return Promise.resolve(failed);
      }
      if (path.endsWith("/initial-sync")) return Promise.resolve(retrying);
      throw new Error(`Unexpected API request: ${path}`);
    });

    render(<ResumableOnboarding onboardingId="onboarding-live" />);
    expect(
      await screen.findByRole("button", { name: "Retry initial sync →" }),
    ).toBeEnabled();
    expect(
      screen.queryByRole("button", { name: /Provision and verify Hermes/ }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Validate sync and continue →" }),
    ).toBeDisabled();

    fireEvent.click(
      screen.getByRole("button", { name: "Retry initial sync →" }),
    );
    await waitFor(() => {
      const retryCall = apiRequest.mock.calls.find(
        ([path, init]) =>
          String(path).endsWith("/initial-sync") && init?.method === "POST",
      );
      expect(retryCall?.[1]).toMatchObject({
        headers: {
          "Idempotency-Key": expect.any(String),
          "If-Match": "9",
        },
        body: JSON.stringify({
          date_from: "2026-06-13",
          date_to: "2026-07-13",
        }),
      });
    });
  });
});
