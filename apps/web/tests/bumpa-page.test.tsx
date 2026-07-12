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
  it("polls a queued production sync with backoff until the new run succeeds", async () => {
    let syncRunPolls = 0;
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
          if (syncRunPolls === 2) {
            return jsonResponse([
              {
                id: "run-live",
                status: "running",
                started_at: "2026-07-12T10:00:00Z",
              },
            ]);
          }
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
    expect(screen.getByText("Running sync")).toBeInTheDocument();
    expect(refresh).toBeDisabled();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(
      screen.getByText("Bumpa refresh completed successfully."),
    ).toBeInTheDocument();
    expect(refresh).toBeEnabled();
    expect(syncRunPolls).toBe(3);
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

  it("bounds polling and tells the user when no requested run reaches a terminal state", async () => {
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
        return jsonResponse({ status: "queued", job_id: "job-slow" }, 202);
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
    expect(syncRunPolls).toBe(8);
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
