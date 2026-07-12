"use client";

import { useState } from "react";
import { AppShell } from "@/components/app-shell";
import { LiveDataBanner } from "@/components/live-data-banner";
import { Badge, PageHeader, StatePanel, Toast } from "@/components/ui";
import { apiRequest } from "@/lib/api";
import {
  titleCase,
  type McpConnection,
  type McpRegistryItem,
} from "@/lib/platform-data";
import {
  previewMcpConnections,
  previewMcpRegistry,
} from "@/lib/preview-fixtures";
import { useApiResource } from "@/lib/use-api-resource";

const descriptions: Record<string, string> = {
  google_drive: "Read approved files and business documents.",
  google_sheets: "Read approved spreadsheets for additional context.",
  gmail: "Search approved messages with read-only scopes.",
  calendar: "Read upcoming business commitments.",
  meta_ads: "Review approved campaign performance.",
};

export default function McpPage() {
  const registry = useApiResource<McpRegistryItem[]>(
    "/mcp/registry",
    previewMcpRegistry,
  );
  const connections = useApiResource<McpConnection[]>(
    "/settings/mcp-connections",
    previewMcpConnections,
  );
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const connect = async (provider: string) => {
    setBusy(provider);
    setError("");
    try {
      await apiRequest("/settings/mcp-connections", {
        method: "POST",
        body: JSON.stringify({ provider, scopes: [], read_only: true }),
      });
      await connections.reload();
      setToast(`${titleCase(provider)} connection request recorded.`);
    } catch (reason) {
      setError(
        reason instanceof Error
          ? reason.message
          : "The connection request could not be saved.",
      );
    } finally {
      setBusy("");
    }
  };
  return (
    <AppShell surface="user" title="Connections">
      <PageHeader
        title="Business connections"
        description="Only providers enabled by the server registry can be requested."
      />
      <LiveDataBanner
        label="connection registry"
        source={registry.source}
        status={registry.status}
        count={registry.data?.length}
        error={registry.error}
      />
      <div className="alert alert-warning">
        All requests are read-only and require operator approval. OAuth launch
        and revocation are disabled until those endpoints exist.
      </div>
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      {registry.status === "loading" ? (
        <StatePanel type="loading" />
      ) : registry.status === "error" ? (
        <StatePanel
          type="error"
          description={registry.error ?? undefined}
          action={
            <button
              className="button button-secondary"
              onClick={() => void registry.reload()}
            >
              Try again
            </button>
          }
        />
      ) : (
        <div className="grid grid-2">
          {(registry.data ?? []).map((item) => {
            const existing = (connections.data ?? []).find(
              (connection) => connection.provider === item.provider,
            );
            const canRequest =
              item.enabled &&
              !existing &&
              registry.source === "live" &&
              connections.source === "live";
            return (
              <section className="card connection-card" key={item.provider}>
                <div className="connection-icon">◇</div>
                <div className="connection-body">
                  <strong>{item.name}</strong>
                  <p>
                    {descriptions[item.provider] ??
                      "Approved read-only business context."}
                  </p>
                  <div style={{ marginTop: 8 }}>
                    <Badge>
                      {existing
                        ? titleCase(existing.status)
                        : item.enabled
                          ? "Available"
                          : "Not enabled"}
                    </Badge>
                    {existing && (
                      <span className="table-secondary">
                        {" "}
                        · {existing.read_only
                          ? "Read-only"
                          : "Write scopes"} ·{" "}
                        {existing.admin_approved
                          ? "Approved"
                          : "Approval pending"}
                      </span>
                    )}
                  </div>
                </div>
                <button
                  className="button button-secondary button-small"
                  disabled={!canRequest || Boolean(busy)}
                  title={
                    !item.enabled
                      ? "This connector is disabled in the server registry."
                      : existing
                        ? "A request already exists."
                        : undefined
                  }
                  onClick={() => void connect(item.provider)}
                >
                  {busy === item.provider
                    ? "Requesting…"
                    : existing
                      ? "Requested"
                      : item.enabled
                        ? "Request"
                        : "Unavailable"}
                </button>
              </section>
            );
          })}
        </div>
      )}
      {connections.status === "error" && (
        <div className="alert alert-danger">
          Existing connections could not be loaded: {connections.error}
        </div>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}
