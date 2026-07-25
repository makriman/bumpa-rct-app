#!/usr/bin/env python3
"""Verify every runtime Hermes profile can authenticate to the private MCP service.

This script runs inside the Hermes container. It intentionally reports only
profile/tool counts; bearer credentials and tenant identifiers never leave the
container.
"""

from __future__ import annotations

import asyncio
import re
import stat
import sys
from pathlib import Path
from urllib.parse import urlsplit

REQUIRED_TOOLS = frozenset(
    {
        "calculate_exactly",
        "generate_image",
        "get_business_overview",
        "get_business_profile",
        "get_customer_summary",
        "get_data_coverage",
        "get_inventory_overview",
        "get_order_breakdown",
        "get_product_performance",
        "get_sales_trend",
        "list_connected_services",
        "prepare_connector_action",
        "read_connected_service",
        "research_web",
        "sandbox_destroy",
        "sandbox_exec",
        "sandbox_file",
        "sandbox_run_code",
    }
)
PROFILE_NAME = re.compile(r"tenant_[a-z0-9_]+")


class VerificationError(RuntimeError):
    """A content-free readiness failure safe for deployment logs."""


def validate_private_mcp_url(value: str) -> str:
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise VerificationError("MCP URL has an invalid port") from exc
    if not (
        parsed.scheme == "http"
        and parsed.hostname == "api"
        and port == 8000
        and parsed.username is None
        and parsed.password is None
        and parsed.path == "/internal/mcp/"
        and not parsed.query
        and not parsed.fragment
    ):
        raise VerificationError("MCP URL is outside the private application boundary")
    return value


def discover_profiles(root: Path, expected_count: int) -> list[Path]:
    if root.is_symlink() or not root.is_dir():
        raise VerificationError("Hermes runtime profile root is unsafe")
    try:
        profiles = sorted(
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
            and PROFILE_NAME.fullmatch(path.name)
        )
    except OSError as exc:
        raise VerificationError("Hermes runtime profiles cannot be inspected") from exc
    if len(profiles) != expected_count:
        raise VerificationError("Hermes runtime profile count does not match reconciliation")
    return profiles


def load_profile_key(profile: Path) -> str:
    environment = profile / ".env"
    if environment.is_symlink() or not environment.is_file():
        raise VerificationError("Hermes profile environment is unsafe")
    try:
        if stat.S_IMODE(environment.stat().st_mode) != 0o600:
            raise VerificationError("Hermes profile environment permissions are unsafe")
        values = [
            line.split("=", 1)[1]
            for line in environment.read_text(encoding="utf-8").splitlines()
            if line.startswith("API_SERVER_KEY=")
        ]
    except OSError as exc:
        raise VerificationError("Hermes profile environment cannot be inspected") from exc
    if len(values) != 1 or not 32 <= len(values[0]) <= 256:
        raise VerificationError("Hermes profile MCP credential is invalid")
    return values[0]


async def verify_profile(profile: Path, mcp_url: str) -> int:
    key = load_profile_key(profile)
    try:
        # Imported only in the production runtime so repository-only contract
        # tests do not need to install the Hermes dependency graph.
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(
            mcp_url,
            headers={"Authorization": f"Bearer {key}"},
            timeout=10,
            sse_read_timeout=20,
        ) as (read_stream, write_stream, _session_id):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.list_tools()
    except Exception as exc:
        raise VerificationError("A Hermes profile cannot reach its private MCP service") from exc
    names = {tool.name for tool in result.tools}
    if not REQUIRED_TOOLS.issubset(names):
        raise VerificationError("A Hermes profile is missing required managed tools")
    return len(names)


async def verify_all(mcp_url: str, root: Path, expected_count: int) -> tuple[int, int]:
    profiles = discover_profiles(root, expected_count)
    counts: list[int] = []
    for profile in profiles:
        counts.append(await verify_profile(profile, mcp_url))
    return len(profiles), min(counts, default=0)


def main(argv: list[str]) -> int:
    if len(argv) != 4:
        raise VerificationError(
            "usage: verify_hermes_mcp.py MCP_URL PROFILE_ROOT EXPECTED_PROFILE_COUNT"
        )
    mcp_url = validate_private_mcp_url(argv[1])
    root = Path(argv[2])
    try:
        expected_count = int(argv[3])
    except ValueError as exc:
        raise VerificationError("Expected profile count is invalid") from exc
    if expected_count < 1 or expected_count > 300:
        raise VerificationError("Expected profile count is outside the approved range")
    profile_count, minimum_tool_count = asyncio.run(
        verify_all(mcp_url, root, expected_count)
    )
    print(
        f"Hermes MCP ready: profiles={profile_count} "
        f"minimum_tools={minimum_tool_count} required_tools={len(REQUIRED_TOOLS)}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv))
    except VerificationError as exc:
        print(f"Hermes MCP readiness failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from None
