import React from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import BumpaPage from "@/app/settings/bumpa/page";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.restoreAllMocks();
});

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("Bumpa refresh", () => {
  it.each([
    [
      { "sales.gross_profit": "unavailable" },
      "Gross profit is unavailable because Bumpa cannot calculate it for this store.",
    ],
    [
      { "sales.net_profit": "unavailable" },
      "Net profit is unavailable because Bumpa cannot calculate it for this store.",
    ],
    [
      {
        "sales.gross_profit": "unavailable",
        "sales.net_profit": "unavailable",
      },
      "Gross and net profit are unavailable because Bumpa cannot calculate them for this store.",
    ],
  ])(
    "presents an accepted %j limitation as usable current data",
    async (profitResults, expectedMessage) => {
      vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
        const url = String(input);
        if (url.endsWith("/settings/bumpa")) {
          return jsonResponse({
            status: "active",
            provider: "bumpa",
            scope_type: "business_id",
            scope_id_last4: "1234",
            last_successful_sync_at: "2026-07-12T10:00:05Z",
            last_error: null,
          });
        }
        if (url.endsWith("/bumpa/sync-runs")) {
          return jsonResponse([
            {
              id: "run-profit-limited",
              status: "partial",
              completion_quality: "accepted_partial",
              partial_reason: "profit_not_calculable",
              dataset_results: {
                orders: "available",
                ...profitResults,
              },
              started_at: "2026-07-12T10:00:00Z",
              finished_at: "2026-07-12T10:00:05Z",
              error: null,
            },
          ]);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

      render(<BumpaPage />);

      expect(await screen.findByText("Last data refresh")).toBeInTheDocument();
      expect(screen.getAllByText("Partial")).not.toHaveLength(0);
      expect(screen.queryByText("Last error")).not.toBeInTheDocument();
      expect(
        screen.getByRole("status", { name: "Bumpa data limitation" }),
      ).toHaveTextContent(
        `${expectedMessage} All other data from this refresh is current.`,
      );
    },
  );

  it("announces a usable profit-limited refresh without calling it a failure", async () => {
    let syncRunPolls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/settings/bumpa")) {
        return jsonResponse({ status: "active", provider: "bumpa" });
      }
      if (url.endsWith("/bumpa/sync-runs")) {
        syncRunPolls += 1;
        return syncRunPolls === 1
          ? jsonResponse([])
          : jsonResponse([
              {
                id: "run-profit-limited",
                status: "partial",
                completion_quality: "accepted_partial",
                partial_reason: "profit_not_calculable",
                dataset_results: {
                  orders: "available",
                  "sales.net_profit": "unavailable",
                },
                started_at: "2026-07-12T10:00:00Z",
                finished_at: "2026-07-12T10:00:05Z",
                error: null,
              },
            ]);
      }
      if (url.endsWith("/bumpa/sync/latest") && init?.method === "POST") {
        return jsonResponse(
          { status: "queued", job_id: "job-profit-limited" },
          202,
        );
      }
      if (url.endsWith("/bumpa/sync-jobs/job-profit-limited")) {
        return jsonResponse({
          job_id: "job-profit-limited",
          status: "succeeded",
          sync_run_id: "run-profit-limited",
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<BumpaPage />);
    const refresh = await screen.findByRole("button", {
      name: /Request refresh/,
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(refresh);
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(
      screen.getByText(
        "Bumpa data refreshed. Net profit is unavailable because Bumpa cannot calculate it for this store. All other data from this refresh is current.",
      ),
    ).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("keeps degraded partial refreshes visibly distinct", async () => {
    let syncRunPolls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/settings/bumpa")) {
        return jsonResponse({ status: "active", provider: "bumpa" });
      }
      if (url.endsWith("/bumpa/sync-runs")) {
        syncRunPolls += 1;
        return syncRunPolls === 1
          ? jsonResponse([])
          : jsonResponse([
              {
                id: "run-degraded",
                status: "partial",
                completion_quality: "degraded",
                partial_reason: "orders_unavailable",
                started_at: "2026-07-12T10:00:00Z",
                finished_at: "2026-07-12T10:00:05Z",
                error: null,
              },
            ]);
      }
      if (url.endsWith("/bumpa/sync/latest") && init?.method === "POST") {
        return jsonResponse({ status: "queued", job_id: "job-degraded" }, 202);
      }
      if (url.endsWith("/bumpa/sync-jobs/job-degraded")) {
        return jsonResponse({
          job_id: "job-degraded",
          status: "succeeded",
          sync_run_id: "run-degraded",
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<BumpaPage />);
    const refresh = await screen.findByRole("button", {
      name: /Request refresh/,
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(refresh);
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(
      screen
        .getByText(
          "Bumpa refresh completed, but some data could not be refreshed. Review the latest run for details.",
        )
        .closest(".toast-warning"),
    ).not.toBeNull();
    expect(
      screen.queryByRole("status", { name: "Bumpa data limitation" }),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "Bumpa data warning" }),
    ).toHaveTextContent("Some data needs attention");
  });

  it("labels a legacy terminal run as unverified and never current", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/settings/bumpa")) {
        return jsonResponse({
          status: "active",
          provider: "bumpa",
          scope_type: "business_id",
          scope_id_last4: "1234",
          last_successful_sync_at: null,
          last_error: null,
        });
      }
      if (url.endsWith("/bumpa/sync-runs")) {
        return jsonResponse([
          {
            id: "run-legacy",
            status: "success",
            completion_quality: "legacy",
            partial_reason: null,
            dataset_results: { "sales.total_sales": "available" },
            started_at: "2026-07-12T10:00:00Z",
            finished_at: "2026-07-12T10:00:05Z",
            error: null,
          },
        ]);
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<BumpaPage />);

    expect(await screen.findByText("Last data refresh")).toBeInTheDocument();
    expect(screen.getByText("Not yet")).toBeInTheDocument();
    expect(
      screen.getByRole("status", {
        name: "Bumpa data verification warning",
      }),
    ).toHaveTextContent("Latest refresh is unverified");
    expect(
      screen.queryByRole("status", { name: "Bumpa data limitation" }),
    ).not.toBeInTheDocument();
    expect(screen.queryByText("Most data is current")).not.toBeInTheDocument();
  });

  it("polls a queued production sync with backoff until the new run succeeds", async () => {
    let syncRunPolls = 0;
    let jobPolls = 0;
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (url.endsWith("/settings/bumpa")) {
          return jsonResponse({
            status: "active",
            provider: "bumpa",
            scope_type: "business",
            scope_id_last4: "1234",
            last_successful_sync_at: null,
            last_error: null,
          });
        }
        if (url.endsWith("/bumpa/sync-runs")) {
          syncRunPolls += 1;
          if (syncRunPolls === 1) return jsonResponse([]);
          return jsonResponse([
            {
              id: "run-live",
              status: "success",
              started_at: "2026-07-12T10:00:00Z",
              finished_at: "2026-07-12T10:00:05Z",
            },
          ]);
        }
        if (url.endsWith("/bumpa/sync/latest") && init?.method === "POST") {
          return jsonResponse({ status: "queued", job_id: "job-live" }, 202);
        }
        if (url.endsWith("/bumpa/sync-jobs/job-live")) {
          jobPolls += 1;
          return jsonResponse(
            jobPolls === 1
              ? { job_id: "job-live", status: "running", sync_run_id: null }
              : {
                  job_id: "job-live",
                  status: "succeeded",
                  sync_run_id: "run-live",
                },
          );
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<BumpaPage />);

    const refresh = await screen.findByRole("button", {
      name: /Request refresh/,
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(refresh);
      await Promise.resolve();
    });

    expect(screen.getByText("Bumpa refresh is in progress.")).toHaveAttribute(
      "role",
      "status",
    );
    expect(refresh).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000);
    });
    expect(
      screen.getByText("Bumpa refresh is in progress."),
    ).toBeInTheDocument();
    expect(refresh).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(
      screen.getByText("Bumpa refresh completed successfully."),
    ).toBeInTheDocument();
    expect(refresh).toBeEnabled();
    expect(jobPolls).toBe(2);
    expect(syncRunPolls).toBe(2);
    const request = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/bumpa/sync/latest"),
    );
    expect(request?.[1]?.method).toBe("POST");
    expect(new Headers(request?.[1]?.headers).get("Idempotency-Key")).toMatch(
      /^[0-9a-f-]{36}$/,
    );
  });

  it("stops polling and exposes the provider error when the new run fails", async () => {
    let syncRunPolls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/settings/bumpa")) {
        return jsonResponse({ status: "active", provider: "bumpa" });
      }
      if (url.endsWith("/bumpa/sync-runs")) {
        syncRunPolls += 1;
        return syncRunPolls === 1
          ? jsonResponse([])
          : jsonResponse([
              {
                id: "run-failed",
                status: "failed",
                started_at: "2026-07-12T10:00:00Z",
                finished_at: "2026-07-12T10:00:01Z",
                error: "Bumpa rejected the business credentials",
              },
            ]);
      }
      if (url.endsWith("/bumpa/sync/latest") && init?.method === "POST") {
        return jsonResponse({ status: "queued", job_id: "job-failed" }, 202);
      }
      if (url.endsWith("/bumpa/sync-jobs/job-failed")) {
        return jsonResponse({
          job_id: "job-failed",
          status: "succeeded",
          sync_run_id: "run-failed",
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<BumpaPage />);
    const refresh = await screen.findByRole("button", {
      name: /Request refresh/,
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(refresh);
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Bumpa rejected the business credentials",
    );
    expect(refresh).toBeEnabled();
    expect(syncRunPolls).toBe(2);
  });

  it("uses the accepted job correlation instead of a concurrent tenant run", async () => {
    let syncRunPolls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/settings/bumpa")) {
        return jsonResponse({ status: "active", provider: "bumpa" });
      }
      if (url.endsWith("/bumpa/sync-runs")) {
        syncRunPolls += 1;
        return syncRunPolls === 1
          ? jsonResponse([])
          : jsonResponse([
              {
                id: "run-concurrent-success",
                status: "success",
                completion_quality: "complete",
                started_at: "2026-07-12T10:00:03Z",
                finished_at: "2026-07-12T10:00:04Z",
                error: null,
              },
              {
                id: "run-requested-failure",
                status: "failed",
                completion_quality: "failed",
                started_at: "2026-07-12T10:00:00Z",
                finished_at: "2026-07-12T10:00:05Z",
                error: "The requested store could not be refreshed",
              },
            ]);
      }
      if (url.endsWith("/bumpa/sync/latest") && init?.method === "POST") {
        return jsonResponse({ status: "queued", job_id: "job-requested" }, 202);
      }
      if (url.endsWith("/bumpa/sync-jobs/job-requested")) {
        return jsonResponse({
          job_id: "job-requested",
          status: "succeeded",
          sync_run_id: "run-requested-failure",
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<BumpaPage />);
    const refresh = await screen.findByRole("button", {
      name: /Request refresh/,
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(refresh);
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The requested store could not be refreshed",
    );
    expect(
      screen.queryByText("Bumpa refresh completed successfully."),
    ).not.toBeInTheDocument();
    expect(syncRunPolls).toBe(2);
  });

  it("stops immediately when the correlated job reaches dead letter", async () => {
    let syncRunPolls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/settings/bumpa")) {
        return jsonResponse({ status: "active", provider: "bumpa" });
      }
      if (url.endsWith("/bumpa/sync-runs")) {
        syncRunPolls += 1;
        return jsonResponse([]);
      }
      if (url.endsWith("/bumpa/sync/latest") && init?.method === "POST") {
        return jsonResponse({ status: "queued", job_id: "job-dead" }, 202);
      }
      if (url.endsWith("/bumpa/sync-jobs/job-dead")) {
        return jsonResponse({
          job_id: "job-dead",
          status: "dead_letter",
          sync_run_id: null,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<BumpaPage />);
    const refresh = await screen.findByRole("button", {
      name: /Request refresh/,
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(refresh);
      await Promise.resolve();
      await vi.advanceTimersByTimeAsync(1000);
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The Bumpa refresh job was stopped after repeated failures",
    );
    expect(refresh).toBeEnabled();
    expect(syncRunPolls).toBe(1);
  });

  it("bounds polling and tells the user when no requested run reaches a terminal state", async () => {
    let syncRunPolls = 0;
    let jobPolls = 0;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
      const url = String(input);
      if (url.endsWith("/settings/bumpa")) {
        return jsonResponse({ status: "active", provider: "bumpa" });
      }
      if (url.endsWith("/bumpa/sync-runs")) {
        syncRunPolls += 1;
        return jsonResponse([]);
      }
      if (url.endsWith("/bumpa/sync/latest") && init?.method === "POST") {
        return jsonResponse({ status: "queued", job_id: "job-slow" }, 202);
      }
      if (url.endsWith("/bumpa/sync-jobs/job-slow")) {
        jobPolls += 1;
        return jsonResponse({
          job_id: "job-slow",
          status: "running",
          sync_run_id: null,
        });
      }
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<BumpaPage />);
    const refresh = await screen.findByRole("button", {
      name: /Request refresh/,
    });
    await waitFor(() => expect(refresh).toBeEnabled());
    vi.useFakeTimers();
    await act(async () => {
      fireEvent.click(refresh);
      await Promise.resolve();
      await vi.runAllTimersAsync();
    });

    expect(screen.getByRole("alert")).toHaveTextContent(
      "The refresh is still processing after one minute",
    );
    expect(refresh).toBeEnabled();
    expect(jobPolls).toBe(7);
    expect(syncRunPolls).toBe(1);
  });

  it("keeps refresh disabled when the live connection is not active", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input) => {
      const url = String(input);
      if (url.endsWith("/settings/bumpa")) {
        return jsonResponse({ status: "not_connected", provider: "disabled" });
      }
      if (url.endsWith("/bumpa/sync-runs")) return jsonResponse([]);
      throw new Error(`Unexpected request: ${url}`);
    });

    render(<BumpaPage />);

    expect(
      await screen.findByRole("heading", { name: "Bumpa is not connected" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Request refresh/ }),
    ).toBeDisabled();
  });
});
