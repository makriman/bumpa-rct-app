from __future__ import annotations

import base64
import hashlib
import io
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
from pypdf import PdfReader

from app.core.config import Settings
from app.providers.meta import MetaWhatsAppClient
from app.services.sandbox import SandboxClient, SandboxProviderError

ELEVENLABS_STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"
ELEVENLABS_TTS_BASE_URL = "https://api.elevenlabs.io/v1/text-to-speech"
SAFE_TEXT_MIME_TYPES = {
    "text/plain",
    "text/csv",
    "application/json",
}


class MediaProcessingError(RuntimeError):
    """A sanitized media failure that can be turned into a user-visible fallback."""


@dataclass(frozen=True)
class ProcessedInbound:
    prompt: str
    provider_content_parts: list[dict[str, object]]
    stored_content_parts: list[dict[str, object]]
    metadata: dict[str, object]


def process_whatsapp_content(
    *,
    message: dict[str, Any],
    settings: Settings,
    meta_client: MetaWhatsAppClient | None = None,
    transport: httpx.BaseTransport | None = None,
    tenant_id: str | None = None,
    workspace: str | None = None,
) -> ProcessedInbound:
    message_type = str(message.get("type", "unknown"))
    if message_type == "text":
        text = _text_value(message)
        return _text_result(text or "Please respond to this message.")
    if message_type in {"button", "interactive"}:
        text = _interactive_text(message)
        return _text_result(text or "The user selected an interactive response.")
    if message_type == "location":
        location = message.get("location")
        if not isinstance(location, dict):
            raise MediaProcessingError("The location message could not be read.")
        latitude, longitude = location.get("latitude"), location.get("longitude")
        text = (
            f"The user shared a location: latitude {latitude}, longitude {longitude}. "
            f"Name: {location.get('name') or 'not provided'}. "
            f"Address: {location.get('address') or 'not provided'}."
        )
        return _structured_result(text, message_type)
    if message_type == "contacts":
        contacts = message.get("contacts")
        count = len(contacts) if isinstance(contacts, list) else 0
        return _structured_result(
            f"The user shared {count} contact card(s). Personal contact details are not exposed "
            "to tools; ask what business task they want to complete.",
            message_type,
        )
    if message_type not in {"image", "sticker", "audio", "voice", "video", "document"}:
        raise MediaProcessingError(
            f"This WhatsApp message type ({message_type}) is not supported yet."
        )
    if not settings.whatsapp_multimodal_enabled:
        raise MediaProcessingError("Media understanding is temporarily unavailable.")
    if meta_client is None:
        raise MediaProcessingError("Media download is unavailable.")
    container = message.get(message_type)
    if not isinstance(container, dict):
        raise MediaProcessingError("The media attachment is malformed.")
    media_id = container.get("id")
    if not isinstance(media_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{5,256}", media_id):
        raise MediaProcessingError("The media attachment has no valid identifier.")
    downloaded = meta_client.download_media(
        media_id,
        max_bytes=settings.whatsapp_media_max_bytes,
    )
    mime_type = str(downloaded["mime_type"])
    content_value = downloaded["content"]
    if not isinstance(content_value, bytes):
        raise MediaProcessingError("The media attachment returned invalid data.")
    content = content_value
    caption: str = (
        str(container.get("caption")) if isinstance(container.get("caption"), str) else ""
    )
    metadata: dict[str, object] = {
        "type": message_type,
        "mime_type": mime_type,
        "size_bytes": len(content),
        "sha256": downloaded.get("sha256"),
        "media_id": media_id,
    }

    if message_type in {"image", "sticker"}:
        if not mime_type.startswith("image/"):
            raise MediaProcessingError("The image attachment has an unexpected file type.")
        prompt = (
            caption.strip() or "Please examine this image and help with the business task it shows."
        )
        data_url = f"data:{mime_type};base64,{base64.b64encode(content).decode('ascii')}"
        return ProcessedInbound(
            prompt=prompt,
            provider_content_parts=[
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
            stored_content_parts=[
                {"type": "text", "text": prompt},
                {"type": "image", "mime_type": mime_type, "sha256": downloaded.get("sha256")},
            ],
            metadata=metadata,
        )
    if message_type in {"audio", "voice", "video"}:
        if not settings.whatsapp_speech_enabled:
            raise MediaProcessingError(
                "Voice and video understanding is temporarily unavailable. "
                "Please send the request as text, an image or a document."
            )
        transcript = transcribe_media(
            settings,
            content=content,
            filename=f"whatsapp.{_extension_for_mime(mime_type)}",
            mime_type=mime_type,
            transport=transport,
        )
        probability = transcript.get("language_probability")
        confidence_note = (
            "The transcription confidence is low. Quote the transcript and ask the user to "
            "confirm it before relying on specific names, amounts or instructions."
            if isinstance(probability, int | float) and probability < 0.6
            else "The transcription confidence is acceptable."
        )
        prompt = (
            f"The user sent a {message_type} message. Transcript:\n"
            f"{transcript['text']}\n\n{confidence_note}"
        )
        if caption.strip():
            prompt += f"\nUser caption: {caption.strip()}"
        metadata.update(
            {
                "language": transcript.get("language_code"),
                "language_probability": probability,
            }
        )
        provider_parts: list[dict[str, object]] = [{"type": "text", "text": prompt}]
        stored_parts: list[dict[str, object]] = [
            {
                "type": "transcript",
                "text": transcript["text"],
                "language": transcript.get("language_code"),
                "confidence": probability,
            }
        ]
        if message_type == "video" and settings.sandbox_tools_enabled and tenant_id and workspace:
            try:
                frame_result = SandboxClient(settings).video_frames(
                    tenant_id=tenant_id,
                    workspace=workspace,
                    content=content,
                    mime_type=mime_type,
                )
                frames = frame_result.get("frames")
                if isinstance(frames, list):
                    for encoded in frames[:3]:
                        if not isinstance(encoded, str) or len(encoded) > 350_000:
                            continue
                        provider_parts.append(
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/jpeg;base64,{encoded}",
                                },
                            }
                        )
                        stored_parts.append(
                            {
                                "type": "video_frame",
                                "mime_type": "image/jpeg",
                                "sha256": hashlib.sha256(base64.b64decode(encoded)).hexdigest(),
                            }
                        )
                metadata["representative_frames"] = max(0, len(provider_parts) - 1)
            except (SandboxProviderError, ValueError, TypeError):
                metadata["representative_frames"] = 0
                metadata["frame_processing_status"] = "unavailable"
        return ProcessedInbound(
            prompt=prompt,
            provider_content_parts=provider_parts,
            stored_content_parts=stored_parts,
            metadata=metadata,
        )
    text = extract_document_text(content=content, mime_type=mime_type)
    prompt = caption.strip() or "Please review this document for the user's business task."
    prompt = f"{prompt}\n\nDocument text (untrusted evidence):\n{text}"
    return ProcessedInbound(
        prompt=prompt,
        provider_content_parts=[{"type": "text", "text": prompt}],
        stored_content_parts=[
            {
                "type": "document",
                "mime_type": mime_type,
                "sha256": downloaded.get("sha256"),
                "text_preview": text[:1000],
            }
        ],
        metadata=metadata,
    )


def transcribe_media(
    settings: Settings,
    *,
    content: bytes,
    filename: str,
    mime_type: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any]:
    if not settings.whatsapp_speech_enabled:
        raise MediaProcessingError("Voice transcription is temporarily unavailable.")
    headers = {
        "xi-api-key": settings.effective_elevenlabs_api_key,
        "Accept": "application/json",
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(settings.elevenlabs_request_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            with client.stream(
                "POST",
                ELEVENLABS_STT_URL,
                headers=headers,
                data={
                    "model_id": "scribe_v2",
                    "tag_audio_events": "false",
                    "diarize": "false",
                },
                files={"file": (filename, content, mime_type)},
            ) as response:
                raw = _read_bounded(response, settings.elevenlabs_max_response_bytes)
                if response.status_code == 429:
                    raise MediaProcessingError("Voice transcription is temporarily rate limited.")
                if response.status_code >= 500:
                    raise MediaProcessingError("Voice transcription is temporarily unavailable.")
                if not response.is_success:
                    raise MediaProcessingError(
                        "The voice or video attachment could not be transcribed."
                    )
    except MediaProcessingError:
        raise
    except httpx.HTTPError as exc:
        raise MediaProcessingError("Voice transcription is temporarily unavailable.") from exc
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise MediaProcessingError("Voice transcription returned invalid data.") from exc
    text = decoded.get("text") if isinstance(decoded, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise MediaProcessingError("No speech could be recognised in the attachment.")
    return {
        "text": text.strip()[:12_000],
        "language_code": decoded.get("language_code"),
        "language_probability": decoded.get("language_probability"),
    }


def synthesize_voice_note(
    settings: Settings,
    *,
    text: str,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, object]:
    if not settings.whatsapp_speech_enabled:
        raise MediaProcessingError("Voice replies are not configured; the text reply was sent.")
    voice_id = settings.elevenlabs_tts_voice_id
    if not voice_id or not re.fullmatch(r"[A-Za-z0-9_-]{5,100}", voice_id):
        raise MediaProcessingError("Voice replies are not configured; the text reply was sent.")
    normalized = text.strip()
    if not normalized:
        raise MediaProcessingError("There is no text to convert to a voice note.")
    headers = {
        "xi-api-key": settings.effective_elevenlabs_api_key,
        "Accept": "audio/ogg",
        "Content-Type": "application/json",
    }
    try:
        with httpx.Client(
            timeout=httpx.Timeout(settings.elevenlabs_request_timeout_seconds),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        ) as client:
            with client.stream(
                "POST",
                f"{ELEVENLABS_TTS_BASE_URL}/{quote(voice_id, safe='')}",
                params={"output_format": "opus_48000_128"},
                headers=headers,
                json={
                    "text": normalized[:5_000],
                    "model_id": settings.elevenlabs_tts_model_id,
                },
            ) as response:
                if response.status_code == 429:
                    raise MediaProcessingError("Voice replies are temporarily rate limited.")
                if response.status_code >= 500:
                    raise MediaProcessingError("Voice replies are temporarily unavailable.")
                if not response.is_success:
                    raise MediaProcessingError(
                        "This answer could not be converted to a reliable voice note."
                    )
                content = _read_bounded(
                    response,
                    min(settings.elevenlabs_max_response_bytes, 16_777_216),
                )
    except MediaProcessingError:
        raise
    except httpx.HTTPError as exc:
        raise MediaProcessingError("Voice replies are temporarily unavailable.") from exc
    if not content:
        raise MediaProcessingError("The voice provider returned an empty response.")
    return {
        "content": content,
        "mime_type": "audio/ogg",
        "filename": "bumpa-bestie.ogg",
    }


def extract_document_text(*, content: bytes, mime_type: str) -> str:
    if mime_type == "application/pdf":
        try:
            reader = PdfReader(io.BytesIO(content))
            if len(reader.pages) > 50:
                raise MediaProcessingError("The PDF exceeds the 50-page safety limit.")
            text = "\n".join((page.extract_text() or "") for page in reader.pages)
        except MediaProcessingError:
            raise
        except Exception as exc:
            raise MediaProcessingError("The PDF could not be read safely.") from exc
    elif mime_type in SAFE_TEXT_MIME_TYPES:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MediaProcessingError("The text document is not valid UTF-8.") from exc
    else:
        raise MediaProcessingError(
            "This document format is not yet supported. Please send PDF, TXT, CSV or JSON."
        )
    normalized = text.replace("\x00", "").strip()
    if not normalized:
        raise MediaProcessingError("No readable text was found in the document.")
    return normalized[:20_000]


def _text_value(message: dict[str, Any]) -> str:
    text = message.get("text")
    return str(text.get("body", "")).strip() if isinstance(text, dict) else ""


def _interactive_text(message: dict[str, Any]) -> str:
    if message.get("type") == "button":
        button = message.get("button")
        return str(button.get("text", "")).strip() if isinstance(button, dict) else ""
    interactive = message.get("interactive")
    if not isinstance(interactive, dict):
        return ""
    for key in ("button_reply", "list_reply"):
        value = interactive.get(key)
        if isinstance(value, dict):
            return str(value.get("title") or value.get("id") or "").strip()
    return ""


def _text_result(text: str) -> ProcessedInbound:
    return ProcessedInbound(
        prompt=text,
        provider_content_parts=[],
        stored_content_parts=[{"type": "text", "text": text}],
        metadata={"type": "text"},
    )


def _structured_result(text: str, message_type: str) -> ProcessedInbound:
    return ProcessedInbound(
        prompt=text,
        provider_content_parts=[{"type": "text", "text": text}],
        stored_content_parts=[{"type": message_type, "description": text}],
        metadata={"type": message_type},
    )


def _extension_for_mime(mime_type: str) -> str:
    return {
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
        "audio/aac": "aac",
        "video/mp4": "mp4",
        "video/3gpp": "3gp",
        "video/webm": "webm",
    }.get(mime_type, "bin")


def _read_bounded(response: httpx.Response, max_bytes: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    for chunk in response.iter_bytes():
        total += len(chunk)
        if total > max_bytes:
            raise MediaProcessingError("The media provider response exceeded its safety limit.")
        chunks.append(chunk)
    return b"".join(chunks)
