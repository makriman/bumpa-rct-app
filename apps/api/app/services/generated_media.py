from __future__ import annotations

import base64
import binascii
import hashlib
import os
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
            user_id=context.user_id,
            conversation_id=context.conversation_id,
            channel=context.channel,
            media_type="image",
            mime_type=mime_type,
            storage_path=relative_path.as_posix(),
            byte_size=len(content),
            checksum_sha256=hashlib.sha256(content).hexdigest(),
            status="pending",
        )
        db.add(media)
        db.add(
            OperationalAgentEvent(
                tenant_id=tenant_id,
                user_id=context.user_id,
                conversation_id=context.conversation_id,
                channel=context.channel,
                event_type="managed_media",
                status="succeeded",
                media_type="image",
                tool_name="generate_image",
                grounding_flags=["managed_provider"],
            )
        )
        db.commit()
    except Exception:
        destination.unlink(missing_ok=True)
        raise
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
