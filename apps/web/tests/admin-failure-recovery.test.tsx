import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ErrorList } from "@/components/admin-pages";

const apiRequest = vi.hoisted(() => vi.fn());
const reloadJobs = vi.hoisted(() => vi.fn(() => Promise.resolve()));
const reloadErrors = vi.hoisted(() => vi.fn(() => Promise.resolve()));

const job = {
  id: "job-terminal-001",
  tenant_id: "tenant-safe-001",
  kind: "bumpa.sync",
  status: "dead_letter" as const,
  attempts: 5,
  max_attempts: 5,
  failure_category: "execution_failure",
  replayable: true,
  available_at: "2026-07-12T10:00:00Z",
  finished_at: "2026-07-12T10:05:00Z",
  created_at: "2026-07-12T09:55:00Z",
  updated_at: "2026-07-12T10:05:00Z",
};

vi.mock("@/lib/api", () => ({
  apiRequest,
  demoFallbackEnabled: false,
}));

vi.mock("@/lib/use-api-resource", () => ({
  useApiResource: (path: string) =>
    path.includes("/system/jobs")
      ? {
          data: [job],
          status: "ready",
          source: "live",
          error: null,
          reload: reloadJobs,
        }
      : {
          data: [],
          status: "ready",
          source: "live",
          error: null,
          reload: reloadErrors,
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
  reloadJobs.mockClear();
  reloadErrors.mockClear();
});

describe("operator dead-letter recovery", () => {
  it("shows only the safe job projection and requires an audited recovery reason", async () => {
    apiRequest.mockResolvedValue({
      ...job,
      status: "retry",
      replayable: false,
    });

    render(<ErrorList />);
    expect(screen.getByText("Bumpa · Sync")).toBeInTheDocument();
    expect(screen.getByText(/Attempted 5 of 5 times/)).toBeInTheDocument();
    expect(screen.queryByText(/access.token/i)).not.toBeInTheDocument();
    expect(
      screen.getByText(/Job payloads, results, credentials/),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Review replay" }));
    expect(
      screen.getByRole("heading", { name: "Replay terminal job" }),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Recovery reason"), {
      target: { value: "upstream_credentials_rotated" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm audited replay" }),
    );

    await waitFor(() =>
      expect(apiRequest).toHaveBeenCalledWith(
        "/admin/system/jobs/job-terminal-001/replay",
        {
          method: "POST",
          body: JSON.stringify({ reason: "upstream_credentials_rotated" }),
        },
      ),
    );
    await waitFor(() => expect(reloadJobs).toHaveBeenCalledTimes(1));
    expect(reloadErrors).toHaveBeenCalledTimes(1);
    expect(
      await screen.findByText("Job queued for a fresh, audited attempt."),
    ).toBeInTheDocument();
  });

  it("keeps the confirmation open and announces a replay conflict", async () => {
    apiRequest.mockRejectedValue(
      new Error("Only dead-letter jobs can be replayed"),
    );

    render(<ErrorList />);
    fireEvent.click(screen.getByRole("button", { name: "Review replay" }));
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm audited replay" }),
    );

    expect(
      await screen.findByRole("alert", {
        name: "",
      }),
    ).toHaveTextContent("Only dead-letter jobs can be replayed");
    expect(
      screen.getByRole("heading", { name: "Replay terminal job" }),
    ).toBeInTheDocument();
    expect(reloadJobs).not.toHaveBeenCalled();
  });
});
