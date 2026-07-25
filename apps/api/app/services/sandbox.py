from __future__ import annotations

import base64
import json
import re
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from app.core.config import Settings

WORKSPACE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")


class SandboxProviderError(RuntimeError):
    """A sanitized Cloudflare Sandbox failure."""


class SandboxClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._base_url = self._validated_base_url(settings.sandbox_worker_url or "")

    def run_code(
        self,
        *,
        tenant_id: str,
        workspace: str,
        language: str,
        code: str,
    ) -> dict[str, Any]:
        if language not in {"python", "javascript", "typescript"}:
            raise ValueError("Sandbox language is unsupported")
        if not 1 <= len(code) <= 50_000:
            raise ValueError("Sandbox code must contain 1 to 50,000 characters")
        return self._request(
            "POST",
            tenant_id=tenant_id,
            workspace=workspace,
            operation="code",
            payload={"language": language, "code": code, "timeout_ms": 30_000},
        )

    def exec(
        self,
        *,
        tenant_id: str,
        workspace: str,
        command: str,
        cwd: str = "/workspace",
    ) -> dict[str, Any]:
        if not 1 <= len(command) <= 8_000:
            raise ValueError("Sandbox command must contain 1 to 8,000 characters")
        if not cwd.startswith("/workspace") or "\x00" in cwd:
            raise ValueError("Sandbox cwd must remain inside /workspace")
        return self._request(
            "POST",
            tenant_id=tenant_id,
            workspace=workspace,
            operation="exec",
            payload={"command": command, "cwd": cwd, "timeout_ms": 30_000},
        )

    def file_operation(
        self,
        *,
        tenant_id: str,
        workspace: str,
        action: str,
        path: str,
        content: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"read", "write", "list", "delete"}:
            raise ValueError("Sandbox file action is unsupported")
        if not path.startswith("/workspace") or "\x00" in path or len(path) > 500:
            raise ValueError("Sandbox path must remain inside /workspace")
        if content is not None and len(content) > 250_000:
            raise ValueError("Sandbox file content exceeds the configured limit")
        return self._request(
            "POST",
            tenant_id=tenant_id,
            workspace=workspace,
            operation="files",
            payload={"action": action, "path": path, "content": content},
        )

    def destroy(self, *, tenant_id: str, workspace: str) -> dict[str, Any]:
        return self._request(
            "DELETE",
            tenant_id=tenant_id,
            workspace=workspace,
            operation="",
            payload=None,
        )

    def video_frames(
        self,
        *,
        tenant_id: str,
        workspace: str,
        content: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        if not content or len(content) > 16_777_216:
            raise ValueError("Sandbox video exceeds the configured limit")
        if mime_type not in {"video/mp4", "video/3gpp", "video/webm"}:
            raise ValueError("Sandbox video format is unsupported")
        return self._request(
            "POST",
            tenant_id=tenant_id,
            workspace=workspace,
            operation="video-frames",
            payload={
                "content_base64": base64.b64encode(content).decode("ascii"),
                "mime_type": mime_type,
            },
            max_response_bytes=2_097_152,
        )

    def document_pages(
        self,
        *,
        tenant_id: str,
        workspace: str,
        content: bytes,
        mime_type: str,
    ) -> dict[str, Any]:
        if not content or len(content) > 16_777_216:
            raise ValueError("Sandbox document exceeds the configured limit")
        if mime_type != "application/pdf":
            raise ValueError("Sandbox document format is unsupported")
        return self._request(
            "POST",
            tenant_id=tenant_id,
            workspace=workspace,
            operation="document-pages",
            payload={
                "content_base64": base64.b64encode(content).decode("ascii"),
                "mime_type": mime_type,
            },
            max_response_bytes=3_145_728,
        )

    def export_file(
        self,
        *,
        tenant_id: str,
        workspace: str,
        path: str,
        mime_type: str,
    ) -> dict[str, Any]:
        if not path.startswith("/workspace") or "\x00" in path or len(path) > 500:
            raise ValueError("Sandbox export path must remain inside /workspace")
        if mime_type not in {
            "application/json",
            "application/pdf",
            "image/jpeg",
            "image/png",
            "text/csv",
            "text/plain",
            "video/mp4",
        }:
            raise ValueError("Sandbox export type is unsupported")
        return self._request(
            "POST",
            tenant_id=tenant_id,
            workspace=workspace,
            operation="export",
            payload={"path": path, "mime_type": mime_type},
            max_response_bytes=12_000_000,
        )

    def generate_image(
        self,
        *,
        tenant_id: str,
        prompt: str,
    ) -> dict[str, Any]:
        normalized = " ".join(prompt.split())
        if not 3 <= len(normalized) <= 2_048:
            raise ValueError("Image prompt must contain 3 to 2,048 characters")
        return self._request(
            "POST",
            tenant_id=tenant_id,
            workspace="managed-media",
            operation="managed-image",
            payload={"prompt": normalized},
            max_response_bytes=12_500_000,
        )

    def _request(
        self,
        method: str,
        *,
        tenant_id: str,
        workspace: str,
        operation: str,
        payload: dict[str, Any] | None,
        max_response_bytes: int = 1_048_576,
    ) -> dict[str, Any]:
        if not WORKSPACE_RE.fullmatch(workspace):
            raise ValueError("Sandbox workspace identifier is invalid")
        if operation == "managed-image":
            suffix = "/v1/media/image"
        else:
            suffix = f"/v1/sandboxes/{quote(workspace, safe='')}"
            if operation:
                suffix += f"/{operation}"
        headers = {
            "Authorization": f"Bearer {self._settings.effective_sandbox_service_token}",
            "X-Bumpa-Tenant-ID": tenant_id,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            with httpx.Client(
                timeout=httpx.Timeout(40.0),
                follow_redirects=False,
                trust_env=False,
                transport=self._transport,
            ) as client:
                with client.stream(
                    method,
                    f"{self._base_url}{suffix}",
                    headers=headers,
                    json=payload,
                ) as response:
                    raw = _read_bounded(response, max_response_bytes)
                    if response.status_code in {401, 403}:
                        raise SandboxProviderError("Sandbox authorization failed")
                    if response.status_code == 429:
                        raise SandboxProviderError("Sandbox capacity is temporarily limited")
                    if response.status_code >= 500:
                        raise SandboxProviderError("Sandbox is temporarily unavailable")
                    if not response.is_success:
                        raise SandboxProviderError("Sandbox rejected the operation")
        except SandboxProviderError:
            raise
        except httpx.HTTPError as exc:
            raise SandboxProviderError("Sandbox is temporarily unavailable") from exc
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SandboxProviderError("Sandbox returned invalid data") from exc
        if not isinstance(decoded, dict):
            raise SandboxProviderError("Sandbox returned invalid data")
        return decoded

    @staticmethod
    def _validated_base_url(value: str) -> str:
        parsed = urlsplit(value)
        if not (
            parsed.scheme == "https"
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.path.rstrip("/") in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        ):
            raise ValueError("Sandbox Worker URL is invalid")
        return value.rstrip("/")


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise SandboxProviderError("Sandbox response exceeded its safety limit")
        chunks.append(chunk)
    return b"".join(chunks)
