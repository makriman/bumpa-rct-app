from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import McpConnection, McpToolPermission
from app.schemas import McpProvider, McpToolPermissionValue
from app.services.mcp_oauth import validate_tool_permission


class McpPermissionDenied(PermissionError):
    """Raised when an MCP tool call fails the mandatory server-side gate."""


def authorize_mcp_tool(
    db: Session,
    *,
    tenant_id: str,
    connection_id: str,
    tool_name: str,
    write_confirmed: bool = False,
) -> McpConnection:
    """Authorize an allowlisted tool without trusting connector-supplied metadata.

    Every future MCP executor must call this function immediately before a tool
    call. Write permission is deliberately insufficient on its own: a fresh,
    explicit confirmation from the user is also mandatory for that invocation.
    """

    connection = db.scalar(
        select(McpConnection).where(
            McpConnection.id == connection_id,
            McpConnection.tenant_id == tenant_id,
        )
    )
    if (
        connection is None
        or connection.status != "active"
        or not connection.admin_approved
        or not connection.encrypted_credentials
    ):
        raise McpPermissionDenied("MCP connection is not active and approved")
    permission = db.scalar(
        select(McpToolPermission).where(
            McpToolPermission.tenant_id == tenant_id,
            McpToolPermission.mcp_connection_id == connection.id,
            McpToolPermission.tool_name == tool_name,
        )
    )
    if permission is None or permission.permission == "deny":
        raise McpPermissionDenied("MCP tool is denied")
    try:
        validate_tool_permission(
            cast(McpProvider, connection.provider),
            tool_name,
            cast(McpToolPermissionValue, permission.permission),
            read_only=connection.read_only,
        )
    except ValueError as exc:
        raise McpPermissionDenied("MCP tool permission is invalid") from exc
    if permission.permission == "write_with_confirmation" and not write_confirmed:
        raise McpPermissionDenied("MCP write requires fresh user confirmation")
    return connection
