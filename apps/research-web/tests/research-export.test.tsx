import React from "react";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { Exports } from "@/components/research-pages";

vi.mock("@/components/app-shell", () => ({
  AppShell: ({ children }: { children: React.ReactNode }) => (
    <main>{children}</main>
  ),
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

function jsonResponse(data: unknown, status = 200): Response {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "content-type": "application/json" },
  });
}

describe("production research exports", () => {
  it("queues an export when the live artifact inventory is available", async () => {
    const queuedExport = {
      id: "report-live",
      report_type: "sme_usage",
      status: "queued",
      title: "SME Usage",
      summary: null,
      created_at: "2026-07-12T12:00:00Z",
      finished_at: null,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (
          url.includes("/research/reports?artifact_kind=export") &&
          !init?.method
        ) {
          return jsonResponse([]);
        }
        if (url.endsWith("/research/exports") && init?.method === "POST") {
          return jsonResponse(queuedExport, 201);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<Exports />);

    const create = await screen.findByRole("button", {
      name: "Create export",
    });
    await waitFor(() => expect(create).toBeEnabled());
    expect(
      screen.queryByText(/production report queue adapter is not configured/i),
    ).not.toBeInTheDocument();

    fireEvent.click(create);
    expect(
      screen.getByText(/generation runs on the durable report queue/i),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Generate export" }));

    expect(
      await screen.findByText(
        "Export queued. Its status will appear in the artifact list shortly.",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("SME Usage")).toBeInTheDocument();
    expect(screen.getByText("Queued")).toBeInTheDocument();

    const request = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/research/exports") && init?.method === "POST",
    );
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      report_type: "sme_usage",
      filters: {},
      formats: ["csv"],
    });
  });

  it("exposes the full export catalogue and reason-gates filtered raw packages", async () => {
    const queuedExport = {
      id: "raw-export-live",
      report_type: "raw_export_package",
      artifact_kind: "export",
      status: "queued",
      title: "Raw Export Package",
      summary: null,
      created_at: "2026-07-13T12:00:00Z",
      finished_at: null,
    };
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(async (input, init) => {
        const url = String(input);
        if (
          url.includes("/research/reports?artifact_kind=export") &&
          !init?.method
        ) {
          return jsonResponse([]);
        }
        if (url.endsWith("/research/exports") && init?.method === "POST") {
          return jsonResponse(queuedExport, 200);
        }
        throw new Error(`Unexpected request: ${url}`);
      });

    render(<Exports />);
    const open = await screen.findByRole("button", {
      name: "Create export",
    });
    await waitFor(() => expect(open).toBeEnabled());
    fireEvent.click(open);

    const type = screen.getByLabelText("Artifact type");
    expect(
      screen.getByText("Business outcome correlation"),
    ).toBeInTheDocument();
    expect(screen.getByText("Anonymized export package")).toBeInTheDocument();
    fireEvent.change(type, { target: { value: "raw_export_package" } });

    const generate = screen.getByRole("button", { name: "Generate export" });
    expect(generate).toBeDisabled();
    fireEvent.change(screen.getByLabelText("From date"), {
      target: { value: "2026-07-01" },
    });
    fireEvent.change(screen.getByLabelText("Channel"), {
      target: { value: "whatsapp" },
    });
    fireEvent.change(screen.getByLabelText("Required access reason"), {
      target: { value: "Validate approved research source records" },
    });
    expect(generate).toBeEnabled();
    fireEvent.click(generate);

    expect(
      (await screen.findAllByText("Raw Export Package")).length,
    ).toBeGreaterThan(0);
    const request = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/research/exports") && init?.method === "POST",
    );
    expect(request?.[1]?.headers).toEqual(
      expect.objectContaining({
        "X-Access-Reason": "Validate approved research source records",
      }),
    );
    expect(JSON.parse(String(request?.[1]?.body))).toEqual({
      report_type: "raw_export_package",
      filters: { date_from: "2026-07-01", channel: "whatsapp" },
      formats: ["csv"],
    });
  });
});
