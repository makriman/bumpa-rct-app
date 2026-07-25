from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.ids import new_id
from app.db.models import GeneratedAgentMedia, OperationalAgentEvent
from app.services.agent_actions import decode_action_context_token
from app.services.sandbox import SandboxClient, SandboxProviderError

MAX_GENERATED_MEDIA_BYTES = 8_388_608


class GeneratedMediaError(RuntimeError):
    """A safe managed-media failure."""


def generate_and_queue_image(
    db: Session,
    settings: Settings,
    *,
    tenant_id: str,
    prompt: str,
    action_context_token: str,
) -> dict[str, Any]:
    if not settings.managed_image_generation_enabled:
        raise GeneratedMediaError("Managed image generation is currently disabled.")
    context = decode_action_context_token(
        settings,
        action_context_token,
        expected_tenant_id=tenant_id,
    )
    try:
        result = SandboxClient(settings).generate_image(
            tenant_id=tenant_id,
            prompt=prompt,
        )
    except (SandboxProviderError, ValueError) as exc:
        raise GeneratedMediaError("Managed image generation is temporarily unavailable.") from exc
    encoded = result.get("image_base64")
    if not isinstance(encoded, str) or len(encoded) > 12_000_000:
        raise GeneratedMediaError("Managed image generation returned invalid media.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GeneratedMediaError("Managed image generation returned invalid media.") from exc
    mime_type, extension = _image_type(content)
    if not 0 < len(content) <= MAX_GENERATED_MEDIA_BYTES:
        raise GeneratedMediaError("Managed image generation exceeded its delivery limit.")

    media = _store_media(
        db,
        settings,
        tenant_id=tenant_id,
        user_id=context.user_id,
        conversation_id=context.conversation_id,
        agent_message_id=context.initiating_message_id,
        channel=context.channel,
        media_type="image",
        mime_type=mime_type,
        filename=f"bumpa-bestie-image.{extension}",
        extension=extension,
        content=content,
        tool_name="generate_image",
        grounding_flags=["managed_provider"],
    )
    return {
        "media_id": media.id,
        "media_type": "image",
        "mime_type": media.mime_type,
        "byte_size": media.byte_size,
        "delivery_status": "queued",
        "effect": (
            "The generated image is queued for delivery in the initiating conversation. "
            "Do not include base64 data in the response."
        ),
    }


def queue_sandbox_file(
    db: Session,
    settings: Settings,
    *,
    tenant_id: str,
    workspace: str,
    path: str,
    filename: str,
    mime_type: str,
    action_context_token: str,
) -> dict[str, Any]:
    if not settings.sandbox_tools_enabled:
        raise GeneratedMediaError("Isolated file delivery is currently disabled.")
    context = decode_action_context_token(
        settings,
        action_context_token,
        expected_tenant_id=tenant_id,
    )
    safe_filename = re.sub(r"[^A-Za-z0-9._-]", "_", filename.strip())[:200]
    if not safe_filename or safe_filename in {".", ".."}:
        raise GeneratedMediaError("The outbound filename is invalid.")
    try:
        result = SandboxClient(settings).export_file(
            tenant_id=tenant_id,
            workspace=workspace,
            path=path,
            mime_type=mime_type,
        )
    except (SandboxProviderError, ValueError) as exc:
        raise GeneratedMediaError(
            "The isolated file could not be prepared for safe delivery."
        ) from exc
    encoded = result.get("content_base64")
    returned_mime = result.get("mime_type")
    if not isinstance(encoded, str) or len(encoded) > 12_000_000 or returned_mime != mime_type:
        raise GeneratedMediaError("The isolated file export returned invalid media.")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise GeneratedMediaError("The isolated file export returned invalid media.") from exc
    media_type, extension = _delivery_type(content, mime_type)
    if not 0 < len(content) <= MAX_GENERATED_MEDIA_BYTES:
        raise GeneratedMediaError("The isolated file exceeds its delivery limit.")
    stem = safe_filename.rsplit(".", 1)[0].rstrip(".") or "bumpa-bestie-file"
    delivery_filename = f"{stem}.{extension}"[:200]
    media = _store_media(
        db,
        settings,
        tenant_id=tenant_id,
        user_id=context.user_id,
        conversation_id=context.conversation_id,
        agent_message_id=context.initiating_message_id,
        channel=context.channel,
        media_type=media_type,
        mime_type=mime_type,
        filename=delivery_filename,
        extension=extension,
        content=content,
        tool_name="queue_sandbox_file",
        grounding_flags=["tenant_sandbox"],
    )
    return {
        "media_id": media.id,
        "media_type": media.media_type,
        "mime_type": media.mime_type,
        "filename": media.filename,
        "byte_size": media.byte_size,
        "delivery_status": "queued",
        "effect": (
            "The sanitized file is queued for delivery only in the initiating conversation. "
            "Do not include base64 data in the response."
        ),
    }


def generated_media_path(settings: Settings, media: GeneratedAgentMedia) -> Path:
    root = settings.artifact_root.resolve()
    candidate = (root / media.storage_path).resolve()
    if root not in candidate.parents:
        raise GeneratedMediaError("Generated media storage path is invalid.")
    return candidate


def _image_type(content: bytes) -> tuple[str, str]:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png", "png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg", "jpg"
    raise GeneratedMediaError("Managed image generation returned an unsupported image.")


def _delivery_type(content: bytes, mime_type: str) -> tuple[str, str]:
    if mime_type == "image/png" and content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "png"
    if mime_type == "image/jpeg" and content.startswith(b"\xff\xd8\xff"):
        return "image", "jpg"
    if mime_type == "video/mp4" and len(content) >= 12 and content[4:8] == b"ftyp":
        return "video", "mp4"
    if mime_type == "application/pdf" and content.startswith(b"%PDF-"):
        return "document", "pdf"
    if mime_type in {"text/plain", "text/csv", "application/json"}:
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise GeneratedMediaError("The outbound document is not valid UTF-8.") from exc
        if "\x00" in decoded:
            raise GeneratedMediaError("The outbound document contains invalid binary data.")
        if mime_type == "application/json":
            try:
                json.loads(decoded)
            except json.JSONDecodeError as exc:
                raise GeneratedMediaError("The outbound JSON document is invalid.") from exc
        extension = {
            "application/json": "json",
            "text/csv": "csv",
            "text/plain": "txt",
        }[mime_type]
        return "document", extension
    raise GeneratedMediaError("The isolated file export returned an unsupported media type.")


def _store_media(
    db: Session,
    settings: Settings,
    *,
    tenant_id: str,
    user_id: str,
    conversation_id: str,
    agent_message_id: str | None,
    channel: str,
    media_type: str,
    mime_type: str,
    filename: str,
    extension: str,
    content: bytes,
    tool_name: str,
    grounding_flags: list[str],
) -> GeneratedAgentMedia:
    media_id = new_id()
    tenant_directory = hashlib.sha256(tenant_id.encode()).hexdigest()[:24]
    relative_path = Path("generated-media") / tenant_directory / f"{media_id}.{extension}"
    destination = settings.artifact_root.resolve() / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
        media = GeneratedAgentMedia(
            id=media_id,
            tenant_id=tenant_id,
            user_id=user_id,
            conversation_id=conversation_id,
            agent_message_id=agent_message_id,
            channel=channel,
            media_type=media_type,
            mime_type=mime_type,
            filename=filename,
            storage_path=relative_path.as_posix(),
            byte_size=len(content),
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            status="pending",
        )
        db.add(media)
        db.add(
            OperationalAgentEvent(
                tenant_id=tenant_id,
                user_id=user_id,
                conversation_id=conversation_id,
                channel=channel,
                event_type="managed_media",
                status="succeeded",
                media_type=media_type,
                tool_name=tool_name,
                grounding_flags=grounding_flags,
            )
        )
        db.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return media
