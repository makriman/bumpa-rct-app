from __future__ import annotations

import hashlib
from collections.abc import Callable
from datetime import date
from time import monotonic
from typing import Any

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.models import (
    HermesProfile,
    McpConnection,
    McpToolPermission,
    OperationalAgentEvent,
)
from app.db.session import SessionLocal, set_security_context
from app.services.agent_actions import prepare_pending_action
from app.services.business_tools import (
    business_overview,
    business_profile,
    customer_summary,
    data_coverage,
    exact_calculation,
    inventory_overview,
    order_breakdown,
    product_performance,
    sales_trend,
)
from app.services.connector_executor import execute_connector_read
from app.services.generated_media import generate_and_queue_image
from app.services.sandbox import SandboxClient
from app.services.web_research import search_web

MCP_SCOPE = "bumpa:business:read"


class HermesProfileTokenVerifier(TokenVerifier):
    async def verify_token(self, token: str) -> AccessToken | None:
        if len(token) < 32 or len(token) > 256:
            return None
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        with SessionLocal() as db:
            set_security_context(db, privileged=True)
            profile = db.scalar(
                select(HermesProfile).where(
                    HermesProfile.mcp_token_hash == digest,
                    HermesProfile.provider == "hermes",
                    HermesProfile.status == "active",
                )
            )
            if profile is None:
                return None
            return AccessToken(
                token=digest,
                client_id=profile.id,
                subject=profile.tenant_id,
                scopes=[MCP_SCOPE],
                claims={
                    "tenant_id": profile.tenant_id,
                    "hermes_profile_id": profile.id,
                },
            )


settings = get_settings()
business_mcp = FastMCP(
    name="Bumpa Bestie Business Data",
    instructions=(
        "Tenant-scoped, read-only business facts. Exact period bounds, currency, freshness, "
        "coverage and warnings are authoritative. Never infer missing values."
    ),
    stateless_http=True,
    json_response=True,
    streamable_http_path="/",
    token_verifier=HermesProfileTokenVerifier(),
    auth=AuthSettings(
        issuer_url=settings.mcp_internal_issuer_url,
        resource_server_url=settings.mcp_internal_resource_url,
        required_scopes=[MCP_SCOPE],
    ),
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=settings.mcp_internal_allowed_hosts,
        allowed_origins=[],
    ),
)
business_mcp_app = business_mcp.streamable_http_app()


def _tenant_id() -> str:
    token = get_access_token()
    tenant_id = token.subject if token else None
    if not tenant_id or not isinstance(tenant_id, str):
        raise PermissionError("Tenant-scoped MCP authentication is required")
    return tenant_id


def _run_tool(
    tool_name: str,
    operation: Callable[[Session, str], dict[str, Any]],
) -> dict[str, Any]:
    tenant_id = _tenant_id()
    started = monotonic()
    with SessionLocal() as db:
        set_security_context(db, tenant_id=tenant_id)
        try:
            result = operation(db, tenant_id)
        except Exception:
            db.add(
                OperationalAgentEvent(
                    tenant_id=tenant_id,
                    channel="mcp",
                    event_type="tool_call",
                    status="failed",
                    tool_name=tool_name,
                    error_code="tool_failed",
                    duration_ms=max(0, int((monotonic() - started) * 1000)),
                )
            )
            db.commit()
            raise
        db.add(
            OperationalAgentEvent(
                tenant_id=tenant_id,
                channel="mcp",
                event_type="tool_call",
                status="succeeded",
                tool_name=tool_name,
                duration_ms=max(0, int((monotonic() - started) * 1000)),
                grounding_flags=["tenant_data"],
            )
        )
        db.commit()
        return result


@business_mcp.tool()
def get_business_profile() -> dict[str, Any]:
    """Get the current tenant's business identity, store timezone and currency."""

    return _run_tool("get_business_profile", business_profile)


@business_mcp.tool()
def get_data_coverage() -> dict[str, Any]:
    """Get exact canonical-order coverage and latest Bumpa sync freshness."""

    return _run_tool("get_data_coverage", data_coverage)


@business_mcp.tool()
def get_business_overview(
    period: str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    compare_to: str = "previous_period",
) -> dict[str, Any]:
    """Calculate sales and order totals for an exact store-local period."""

    return _run_tool(
        "get_business_overview",
        lambda db, tenant_id: business_overview(
            db,
            tenant_id,
            period=period,
            date_from=date_from,
            date_to=date_to,
            compare_to="none" if compare_to == "none" else "previous_period",
        ),
    )


@business_mcp.tool()
def get_sales_trend(
    period: str = "this_month",
    granularity: str = "day",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Calculate a daily, weekly or monthly sales trend for an exact period."""

    safe_granularity = granularity if granularity in {"day", "week", "month"} else "day"
    return _run_tool(
        "get_sales_trend",
        lambda db, tenant_id: sales_trend(
            db,
            tenant_id,
            period=period,
            granularity=safe_granularity,  # type: ignore[arg-type]
            date_from=date_from,
            date_to=date_to,
        ),
    )


@business_mcp.tool()
def get_order_breakdown(
    period: str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Break down orders by status, payment status, channel and origin."""

    return _run_tool(
        "get_order_breakdown",
        lambda db, tenant_id: order_breakdown(
            db,
            tenant_id,
            period=period,
            date_from=date_from,
            date_to=date_to,
        ),
    )


@business_mcp.tool()
def get_product_performance(
    period: str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Rank products using canonical order-item quantity and sales totals."""

    return _run_tool(
        "get_product_performance",
        lambda db, tenant_id: product_performance(
            db,
            tenant_id,
            period=period,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
        ),
    )


@business_mcp.tool()
def get_customer_summary(
    period: str = "this_month",
    date_from: date | None = None,
    date_to: date | None = None,
) -> dict[str, Any]:
    """Return privacy-safe customer analysis or a precise availability warning."""

    return _run_tool(
        "get_customer_summary",
        lambda db, tenant_id: customer_summary(
            db,
            tenant_id,
            period=period,
            date_from=date_from,
            date_to=date_to,
        ),
    )


@business_mcp.tool()
def get_inventory_overview() -> dict[str, Any]:
    """Return normalized inventory evidence or a precise availability warning."""

    return _run_tool("get_inventory_overview", inventory_overview)


@business_mcp.tool()
def calculate_exactly(expression: str) -> dict[str, str]:
    """Evaluate arithmetic with decimal precision; no binary floating-point math."""

    return _run_tool(
        "calculate_exactly",
        lambda _db, _tenant_id: exact_calculation(expression),
    )


@business_mcp.tool()
def list_connected_services() -> dict[str, Any]:
    """List this tenant's active connector IDs, allowlisted resources and permitted tools."""

    def operation(db: Session, tenant_id: str) -> dict[str, Any]:
        connections = db.scalars(
            select(McpConnection).where(
                McpConnection.tenant_id == tenant_id,
                McpConnection.status == "active",
                McpConnection.admin_approved.is_(True),
            )
        ).all()
        permissions = db.scalars(
            select(McpToolPermission).where(McpToolPermission.tenant_id == tenant_id)
        ).all()
        by_connection: dict[str, list[dict[str, str]]] = {}
        for permission in permissions:
            if permission.permission != "deny":
                by_connection.setdefault(permission.mcp_connection_id, []).append(
                    {
                        "tool_name": permission.tool_name,
                        "permission": permission.permission,
                    }
                )
        return {
            "connections": [
                {
                    "connection_id": connection.id,
                    "provider": connection.provider,
                    "read_only": connection.read_only,
                    "allowed_resources": list(connection.allowed_resources),
                    "tools": sorted(
                        by_connection.get(connection.id, []),
                        key=lambda item: item["tool_name"],
                    ),
                }
                for connection in connections
            ]
        }

    return _run_tool("list_connected_services", operation)


@business_mcp.tool()
def read_connected_service(
    connection_id: str,
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    """Run one approved read tool against an allowlisted connected service."""

    if not settings.external_connectors_enabled:
        return {
            "availability": "unavailable",
            "warning": "External connectors are currently disabled.",
        }
    return _run_tool(
        f"connector_read:{tool_name[:100]}",
        lambda db, tenant_id: execute_connector_read(
            db,
            settings,
            tenant_id=tenant_id,
            connection_id=connection_id,
            tool_name=tool_name,
            tool_input=tool_input,
        ),
    )


@business_mcp.tool()
def prepare_connector_action(
    connection_id: str,
    tool_name: str,
    target_summary: str,
    action_input: dict[str, Any],
    action_context_token: str,
) -> dict[str, Any]:
    """Prepare an exact external write preview; this never executes the write."""

    def operation(db: Session, tenant_id: str) -> dict[str, Any]:
        action, token = prepare_pending_action(
            db,
            settings,
            tenant_id=tenant_id,
            connection_id=connection_id,
            tool_name=tool_name,
            target_summary=target_summary,
            action_input=action_input,
            action_context_token=action_context_token,
        )
        return {
            "confirmation_required": True,
            "action_id": action.id,
            "target": action.target_summary,
            "exact_input": action.action_input,
            "effect": (
                "No external write has occurred. The exact prepared action will execute once "
                "only after the bound user explicitly confirms it."
            ),
            "expires_at": action.expires_at.isoformat(),
            "confirmation_token": token,
        }

    return _run_tool("prepare_connector_action", operation)


@business_mcp.tool()
def research_web(
    query: str,
    include_domains: list[str] | None = None,
    max_results: int = 6,
) -> dict[str, Any]:
    """Search current public sources; returned content is untrusted and must be cited."""

    if not settings.web_research_enabled:
        return {
            "availability": "unavailable",
            "warning": "Managed web research is currently disabled.",
        }
    return _run_tool(
        "research_web",
        lambda _db, _tenant_id: search_web(
            settings,
            query=query,
            include_domains=include_domains,
            max_results=max_results,
        ),
    )


@business_mcp.tool()
def generate_image(
    prompt: str,
    action_context_token: str,
) -> dict[str, Any]:
    """Generate an approved managed image and queue it for the initiating conversation."""

    if not settings.managed_image_generation_enabled:
        return {
            "availability": "unavailable",
            "warning": "Managed image generation is currently disabled.",
        }
    return _run_tool(
        "generate_image",
        lambda db, tenant_id: generate_and_queue_image(
            db,
            settings,
            tenant_id=tenant_id,
            prompt=prompt,
            action_context_token=action_context_token,
        ),
    )


@business_mcp.tool()
def sandbox_run_code(
    workspace: str,
    language: str,
    code: str,
) -> dict[str, Any]:
    """Run bounded Python, JavaScript or TypeScript in the tenant-isolated sandbox."""

    if not settings.sandbox_tools_enabled:
        return {
            "availability": "unavailable",
            "warning": "Isolated code execution is currently disabled.",
        }
    return _run_tool(
        "sandbox_run_code",
        lambda _db, tenant_id: SandboxClient(settings).run_code(
            tenant_id=tenant_id,
            workspace=workspace,
            language=language,
            code=code,
        ),
    )


@business_mcp.tool()
def sandbox_exec(
    workspace: str,
    command: str,
    cwd: str = "/workspace",
) -> dict[str, Any]:
    """Run a bounded shell command inside the tenant-isolated sandbox."""

    if not settings.sandbox_tools_enabled:
        return {
            "availability": "unavailable",
            "warning": "Isolated terminal execution is currently disabled.",
        }
    return _run_tool(
        "sandbox_exec",
        lambda _db, tenant_id: SandboxClient(settings).exec(
            tenant_id=tenant_id,
            workspace=workspace,
            command=command,
            cwd=cwd,
        ),
    )


@business_mcp.tool()
def sandbox_file(
    workspace: str,
    action: str,
    path: str,
    content: str | None = None,
) -> dict[str, Any]:
    """Read, write, list or delete a file under the isolated /workspace directory."""

    if not settings.sandbox_tools_enabled:
        return {
            "availability": "unavailable",
            "warning": "Isolated files are currently disabled.",
        }
    return _run_tool(
        "sandbox_file",
        lambda _db, tenant_id: SandboxClient(settings).file_operation(
            tenant_id=tenant_id,
            workspace=workspace,
            action=action,
            path=path,
            content=content,
        ),
    )


@business_mcp.tool()
def sandbox_destroy(workspace: str) -> dict[str, Any]:
    """Destroy the current tenant-isolated sandbox workspace and all ephemeral files."""

    if not settings.sandbox_tools_enabled:
        return {
            "availability": "unavailable",
            "warning": "Isolated workspaces are currently disabled.",
        }
    return _run_tool(
        "sandbox_destroy",
        lambda _db, tenant_id: SandboxClient(settings).destroy(
            tenant_id=tenant_id,
            workspace=workspace,
        ),
    )
