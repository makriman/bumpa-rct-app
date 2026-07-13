"use client";

import { useEffect, useMemo, useState } from "react";
import { AppShell } from "@/components/app-shell";
import { LiveDataBanner } from "@/components/live-data-banner";
import {
  Badge,
  Card,
  Modal,
  PageHeader,
  StatePanel,
  Toast,
} from "@/components/ui";
import { apiRequest } from "@/lib/api";
import {
  titleCase,
  type McpConnection,
  type McpRegistryItem,
  type McpRegistryTool,
  type McpToolPermission,
} from "@/lib/platform-data";
import {
  previewMcpConnections,
  previewMcpRegistry,
} from "@/lib/preview-fixtures";
import { useApiResource } from "@/lib/use-api-resource";

const descriptions: Record<string, string> = {
  google_drive:
    "Use approved files and business documents as read-only context.",
  google_sheets: "Use approved spreadsheets for business context and analysis.",
  gmail: "Search approved messages without exposing your mailbox credentials.",
  calendar: "Use upcoming business commitments as planning context.",
  meta_ads: "Review approved campaign performance alongside store activity.",
};

type ConnectionRequest = {
  item: McpRegistryItem;
  readOnly: boolean;
};

type WriteRequest = {
  connection: McpConnection;
  tool: McpRegistryTool;
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
  const [request, setRequest] = useState<ConnectionRequest | null>(null);
  const [writeRequest, setWriteRequest] = useState<WriteRequest | null>(null);
  const [revoke, setRevoke] = useState<McpConnection | null>(null);

  useEffect(() => {
    const outcome = new URLSearchParams(window.location.search).get("oauth");
    if (outcome === "success") {
      setToast(
        "Provider authorization completed. Your encrypted connection is active.",
      );
      void connections.reload();
    } else if (outcome === "cancelled") {
      setToast(
        "Provider authorization was cancelled. No new credentials were stored.",
      );
    } else if (outcome === "error") {
      setError(
        "The provider could not complete authorization. Your previous access remains safe.",
      );
    }
    if (outcome) window.history.replaceState({}, "", window.location.pathname);
    // The resource hook exposes a stable reload callback for this mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const connectionsByProvider = useMemo(
    () =>
      new Map(
        (connections.data ?? []).map((connection) => [
          connection.provider,
          connection,
        ]),
      ),
    [connections.data],
  );

  async function createConnection() {
    if (!request || busy) return;
    const operation = `request:${request.item.provider}`;
    setBusy(operation);
    setError("");
    try {
      await apiRequest("/settings/mcp-connections", {
        method: "POST",
        body: JSON.stringify({
          provider: request.item.provider,
          scopes: [],
          read_only: request.readOnly,
        }),
      });
      await connections.reload();
      setRequest(null);
      setToast(
        `${request.item.name} request recorded. A platform operator must approve it before authorization.`,
      );
    } catch (reason) {
      setError(
        messageFor(reason, "The connection request could not be saved."),
      );
    } finally {
      setBusy("");
    }
  }

  async function beginOAuth(connection: McpConnection) {
    const operation = `oauth:${connection.id}`;
    setBusy(operation);
    setError("");
    try {
      const result = await apiRequest<{
        authorization_url: string;
        expires_in_seconds: number;
      }>(`/settings/mcp-connections/${connection.id}/oauth/start`, {
        method: "POST",
      });
      const target = new URL(result.authorization_url);
      if (
        target.protocol !== "https:" ||
        !["accounts.google.com", "www.facebook.com"].includes(target.hostname)
      ) {
        throw new Error(
          "The server returned an unapproved authorization destination.",
        );
      }
      window.location.assign(target.toString());
    } catch (reason) {
      setError(messageFor(reason, "Authorization could not be started."));
      setBusy("");
    }
  }

  async function setPermission(
    connection: McpConnection,
    tool: McpRegistryTool,
    permission: McpToolPermission,
  ) {
    const operation = `permission:${connection.id}:${tool.name}`;
    setBusy(operation);
    setError("");
    try {
      await apiRequest(
        `/settings/mcp-connections/${connection.id}/permissions/${tool.name}`,
        {
          method: "PATCH",
          body: JSON.stringify({
            permission,
            acknowledge_write_confirmation:
              permission === "write_with_confirmation",
          }),
        },
      );
      await connections.reload();
      setWriteRequest(null);
      setToast(
        permission === "write_with_confirmation"
          ? `${tool.label} enabled. Every actual write still requires fresh confirmation.`
          : `${tool.label} permission updated.`,
      );
    } catch (reason) {
      setError(messageFor(reason, "The tool permission could not be updated."));
    } finally {
      setBusy("");
    }
  }

  async function revokeConnection() {
    if (!revoke || busy) return;
    const operation = `revoke:${revoke.id}`;
    setBusy(operation);
    setError("");
    try {
      await apiRequest(`/settings/mcp-connections/${revoke.id}`, {
        method: "DELETE",
      });
      await connections.reload();
      setRevoke(null);
      setToast("Connection revoked. Stored OAuth credentials were removed.");
    } catch (reason) {
      setError(messageFor(reason, "The connection could not be revoked."));
    } finally {
      setBusy("");
    }
  }

  const isLive = registry.source === "live" && connections.source === "live";
  return (
    <AppShell surface="user" title="Connections">
      <PageHeader
        title="Business connections"
        description="Bring approved business context into Bestie through a controlled, auditable connection."
      />
      <LiveDataBanner
        label="connection registry"
        source={registry.source}
        status={registry.status}
        count={registry.data?.length}
        error={registry.error}
      />
      <div className="alert alert-info">
        Bumpa Bestie never accepts arbitrary MCP server addresses. Every
        connector comes from the platform registry, starts read-only, requires
        operator approval, and stores OAuth tokens encrypted. A permitted write
        still requires fresh confirmation at the moment it runs.
      </div>
      {error && (
        <div className="alert alert-danger" role="alert">
          {error}
        </div>
      )}
      {registry.status === "loading" || connections.status === "loading" ? (
        <StatePanel type="loading" />
      ) : registry.status === "error" || connections.status === "error" ? (
        <StatePanel
          type="error"
          description={registry.error ?? connections.error ?? undefined}
          action={
            <button
              className="button button-secondary"
              onClick={() =>
                void Promise.all([registry.reload(), connections.reload()])
              }
            >
              Try again
            </button>
          }
        />
      ) : (
        <div className="grid grid-2">
          {(registry.data ?? []).map((item) => {
            const connection = connectionsByProvider.get(item.provider);
            return (
              <ConnectionCard
                key={item.provider}
                item={item}
                connection={connection}
                busy={busy}
                live={isLive}
                onRequest={(readOnly) => setRequest({ item, readOnly })}
                onOAuth={(value) => void beginOAuth(value)}
                onPermission={(value, tool, permission) => {
                  if (permission === "write_with_confirmation") {
                    setWriteRequest({ connection: value, tool });
                  } else {
                    void setPermission(value, tool, permission);
                  }
                }}
                onRevoke={setRevoke}
              />
            );
          })}
        </div>
      )}
      {request && (
        <Modal
          title={`Request ${request.item.name}`}
          onClose={() => !busy && setRequest(null)}
          actions={
            <>
              <button
                className="button button-secondary"
                onClick={() => setRequest(null)}
                disabled={Boolean(busy)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                onClick={() => void createConnection()}
                disabled={Boolean(busy)}
                aria-busy={Boolean(busy)}
              >
                {busy ? "Requesting…" : "Confirm request"}
              </button>
            </>
          }
        >
          <p>
            This requests <strong>{request.item.name}</strong> in{" "}
            <strong>
              {request.readOnly ? "read-only" : "controlled-write"}
            </strong>{" "}
            mode. It does not open an OAuth window until a platform operator has
            approved the request.
          </p>
          {!request.readOnly && (
            <div className="alert alert-warning">
              Write-capable tools are denied by default. You must enable each
              one explicitly, and every write invocation still needs fresh
              confirmation.
            </div>
          )}
        </Modal>
      )}
      {writeRequest && (
        <Modal
          title="Enable a confirmed-write tool"
          onClose={() => !busy && setWriteRequest(null)}
          actions={
            <>
              <button
                className="button button-secondary"
                onClick={() => setWriteRequest(null)}
                disabled={Boolean(busy)}
              >
                Cancel
              </button>
              <button
                className="button button-primary"
                onClick={() =>
                  void setPermission(
                    writeRequest.connection,
                    writeRequest.tool,
                    "write_with_confirmation",
                  )
                }
                disabled={Boolean(busy)}
              >
                {busy ? "Saving…" : "Enable with confirmation"}
              </button>
            </>
          }
        >
          <p>
            <strong>{writeRequest.tool.label}</strong> may change external data.
            Enabling it does not authorize a write now. Bestie must still show
            the exact action and receive fresh confirmation for every
            invocation.
          </p>
        </Modal>
      )}
      {revoke && (
        <Modal
          title={`Revoke ${titleCase(revoke.provider)}`}
          onClose={() => !busy && setRevoke(null)}
          actions={
            <>
              <button
                className="button button-secondary"
                onClick={() => setRevoke(null)}
                disabled={Boolean(busy)}
              >
                Keep connection
              </button>
              <button
                className="button button-danger"
                onClick={() => void revokeConnection()}
                disabled={Boolean(busy)}
              >
                {busy ? "Revoking…" : "Revoke and remove credentials"}
              </button>
            </>
          }
        >
          <p>
            Access stops immediately. Encrypted OAuth credentials and tool
            permissions are removed, while the audit record is retained.
          </p>
        </Modal>
      )}
      {toast && <Toast message={toast} onClose={() => setToast("")} />}
    </AppShell>
  );
}

function ConnectionCard({
  item,
  connection,
  busy,
  live,
  onRequest,
  onOAuth,
  onPermission,
  onRevoke,
}: {
  item: McpRegistryItem;
  connection?: McpConnection;
  busy: string;
  live: boolean;
  onRequest: (readOnly: boolean) => void;
  onOAuth: (connection: McpConnection) => void;
  onPermission: (
    connection: McpConnection,
    tool: McpRegistryTool,
    permission: McpToolPermission,
  ) => void;
  onRevoke: (connection: McpConnection) => void;
}) {
  const canAuthorize =
    connection?.admin_approved &&
    connection.oauth_available &&
    ["approved", "oauth_in_progress", "active"].includes(connection.status);
  return (
    <Card className="connection-card">
      <div className="connection-icon" aria-hidden="true">
        ◇
      </div>
      <div className="connection-body" style={{ minWidth: 0 }}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            flexWrap: "wrap",
          }}
        >
          <strong>{item.name}</strong>
          <Badge>
            {connection
              ? titleCase(connection.status)
              : item.enabled
                ? "Available"
                : "Not configured"}
          </Badge>
        </div>
        <p>{descriptions[item.provider] ?? "Approved business context."}</p>
        {connection ? (
          <>
            <div className="detail-row">
              <span className="detail-label">Access mode</span>
              <span>
                {connection.read_only ? "Read-only" : "Controlled writes"}
              </span>
            </div>
            <div className="detail-row">
              <span className="detail-label">Operator approval</span>
              <span>{connection.admin_approved ? "Approved" : "Pending"}</span>
            </div>
            <div style={{ marginTop: 16 }}>
              <strong>Tool permissions</strong>
              <div className="timeline" style={{ marginTop: 10 }}>
                {item.tools.map((tool) => {
                  const permission =
                    connection.permissions[tool.name] ?? "deny";
                  const operation = `permission:${connection.id}:${tool.name}`;
                  const canChange = connection.admin_approved && live && !busy;
                  return (
                    <div className="timeline-item" key={tool.name}>
                      <div
                        style={{
                          display: "flex",
                          justifyContent: "space-between",
                          gap: 12,
                          alignItems: "center",
                          flexWrap: "wrap",
                        }}
                      >
                        <div>
                          <strong>{tool.label}</strong>
                          <p>
                            {tool.kind === "write"
                              ? "External write · confirmation required"
                              : "Read-only context"}
                          </p>
                        </div>
                        <button
                          className="button button-ghost button-small"
                          disabled={
                            !canChange ||
                            (tool.kind === "write" && connection.read_only)
                          }
                          aria-busy={busy === operation}
                          onClick={() =>
                            onPermission(
                              connection,
                              tool,
                              permission === "deny"
                                ? tool.kind === "write"
                                  ? "write_with_confirmation"
                                  : "read"
                                : "deny",
                            )
                          }
                        >
                          {busy === operation
                            ? "Saving…"
                            : permission === "deny"
                              ? tool.kind === "write"
                                ? "Enable safely"
                                : "Allow read"
                              : "Disable"}
                        </button>
                      </div>
                    </div>
                  );
                })}
              </div>
            </div>
            <div
              style={{
                display: "flex",
                gap: 8,
                flexWrap: "wrap",
                marginTop: 16,
              }}
            >
              {canAuthorize && (
                <button
                  className="button button-primary button-small"
                  disabled={!live || Boolean(busy)}
                  aria-busy={busy === `oauth:${connection.id}`}
                  onClick={() => onOAuth(connection)}
                >
                  {busy === `oauth:${connection.id}`
                    ? "Opening provider…"
                    : connection.status === "active"
                      ? "Reauthorize"
                      : "Authorize provider"}
                </button>
              )}
              <button
                className="button button-secondary button-small"
                disabled={!live || Boolean(busy)}
                onClick={() => onRevoke(connection)}
              >
                Revoke
              </button>
            </div>
          </>
        ) : item.enabled ? (
          <div
            style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 14 }}
          >
            <button
              className="button button-primary button-small"
              disabled={!live || Boolean(busy)}
              onClick={() => onRequest(true)}
            >
              Request read-only
            </button>
            {item.tools.some((tool) => tool.kind === "write") && (
              <button
                className="button button-secondary button-small"
                disabled={!live || Boolean(busy)}
                onClick={() => onRequest(false)}
              >
                Request controlled writes
              </button>
            )}
          </div>
        ) : (
          <p className="table-secondary" style={{ marginTop: 12 }}>
            OAuth client setup is not complete for this approved connector.
          </p>
        )}
      </div>
    </Card>
  );
}

function messageFor(reason: unknown, fallback: string): string {
  return reason instanceof Error ? reason.message : fallback;
}
