import React from "react";
import {
  cleanup,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LiveDataBanner } from "@/components/live-data-banner";
import { useApiResource } from "@/lib/use-api-resource";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

describe("truthful frontend data sources", () => {
  it("labels API rows as live", () => {
    render(
      <LiveDataBanner label="tenants" source="live" status="ready" count={2} />,
    );
    expect(screen.getByText(/Live tenants · 2 records/)).toBeInTheDocument();
    expect(screen.getByText(/came from the API/)).toBeInTheDocument();
  });

  it("uses the same successful response for source and rows", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(JSON.stringify([{ id: "live-row" }]), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    const { result } = renderHook(() => useApiResource("/settings/team"));
    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.source).toBe("live");
    expect(result.current.data).toEqual([{ id: "live-row" }]);
  });

  it("fails closed without substituting fixture rows", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useApiResource("/settings/team"));
    await waitFor(() => expect(result.current.status).toBe("error"));
    expect(result.current.source).toBeNull();
    expect(result.current.data).toBeNull();
    expect(result.current.error).toBe("offline");
  });
});
