from __future__ import annotations

import base64
import io
import json
from typing import Any

import httpx
import pytest
from pypdf import PdfWriter

from app.core.config import Settings
from app.services.media import (
    MediaProcessingError,
    extract_document_text,
    process_whatsapp_content,
    synthesize_voice_note,
    transcribe_media,
)
from app.services.sandbox import SandboxClient, SandboxProviderError


def _settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "field_encryption_key": "media-sandbox-test-key",
        "whatsapp_multimodal_enabled": True,
        "whatsapp_speech_enabled": True,
        "elevenlabs_api_key": "elevenlabs-fixture-key-with-safe-length",
        "elevenlabs_tts_voice_id": "voice_fixture",
        "sandbox_worker_url": "https://sandbox.example.com",
        "sandbox_service_token": ("sandbox-fixture-token-with-at-least-thirty-two-characters"),  # noqa: S106
        "sandbox_tools_enabled": True,
    }
    values.update(overrides)
    return Settings(**values)


class _MediaClient:
    def __init__(self, mime_type: str, content: bytes) -> None:
        self.mime_type = mime_type
        self.content = content

    def download_media(self, _media_id: str, *, max_bytes: int) -> dict[str, object]:
        assert len(self.content) <= max_bytes
        return {
            "mime_type": self.mime_type,
            "content": self.content,
            "sha256": "fixture-sha",
        }


def test_whatsapp_structured_document_video_and_speech_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    assert (
        process_whatsapp_content(
            message={"type": "button", "button": {"text": "Confirm"}},
            settings=settings,
        ).prompt
        == "Confirm"
    )
    assert (
        "latitude 6.5"
        in process_whatsapp_content(
            message={
                "type": "location",
                "location": {"latitude": 6.5, "longitude": 3.3, "name": "Shop"},
            },
            settings=settings,
        ).prompt
    )
    assert (
        "2 contact card"
        in process_whatsapp_content(
            message={"type": "contacts", "contacts": [{}, {}]},
            settings=settings,
        ).prompt
    )
    document = process_whatsapp_content(
        message={
            "type": "document",
            "document": {"id": "document-12345", "caption": "Review the cashflow"},
        },
        settings=settings,
        meta_client=_MediaClient("text/csv", b"month,sales\nJuly,25000\n"),  # type: ignore[arg-type]
    )
    assert "untrusted evidence" in document.prompt
    assert "July,25000" in document.prompt

    monkeypatch.setattr(
        "app.services.media.SandboxClient.video_frames",
        lambda _client, **_kwargs: {"frames": [base64.b64encode(b"frame-one").decode("ascii")]},
    )
    video = process_whatsapp_content(
        message={"type": "video", "video": {"id": "video-12345", "caption": "Check display"}},
        settings=settings,
        meta_client=_MediaClient("video/mp4", b"video"),  # type: ignore[arg-type]
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(
                200,
                json={
                    "text": "This is the new shelf display",
                    "language_code": "eng",
                    "language_probability": 0.9,
                },
            )
        ),
        tenant_id="tenant-123456",
        workspace="conversation-1",
    )
    assert video.metadata["representative_frames"] == 1
    assert any(part["type"] == "image_url" for part in video.provider_content_parts)

    speech_disabled = _settings(whatsapp_speech_enabled=False)
    with pytest.raises(MediaProcessingError, match="temporarily unavailable"):
        process_whatsapp_content(
            message={"type": "voice", "voice": {"id": "voice-12345"}},
            settings=speech_disabled,
            meta_client=_MediaClient("audio/ogg", b"voice"),  # type: ignore[arg-type]
        )

    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    blank_pdf = io.BytesIO()
    writer.write(blank_pdf)
    monkeypatch.setattr(
        "app.services.media.SandboxClient.document_pages",
        lambda _client, **_kwargs: {"pages": [base64.b64encode(b"scanned-page").decode("ascii")]},
    )
    scanned = process_whatsapp_content(
        message={"type": "document", "document": {"id": "scanned-pdf-12345"}},
        settings=settings,
        meta_client=_MediaClient("application/pdf", blank_pdf.getvalue()),  # type: ignore[arg-type]
        tenant_id="tenant-123456",
        workspace="conversation-1",
    )
    assert scanned.metadata["document_extraction"] == "vision_ocr"
    assert scanned.metadata["document_page_count"] == 1
    assert any(part["type"] == "image_url" for part in scanned.provider_content_parts)


def test_safe_document_and_voice_generation_boundaries() -> None:
    assert extract_document_text(content=b'{"sales": 25}', mime_type="application/json") == (
        '{"sales": 25}'
    )
    with pytest.raises(MediaProcessingError, match="UTF-8"):
        extract_document_text(content=b"\xff", mime_type="text/plain")
    with pytest.raises(MediaProcessingError, match="not yet supported"):
        extract_document_text(content=b"macro", mime_type="application/vnd.ms-excel")
    with pytest.raises(MediaProcessingError, match="No readable"):
        extract_document_text(content=b"\x00 \n", mime_type="text/plain")

    voice = synthesize_voice_note(
        _settings(),
        text="Your sales increased this week.",
        transport=httpx.MockTransport(
            lambda request: (
                httpx.Response(200, content=b"ogg-opus")
                if request.url.path.endswith("/voice_fixture")
                else httpx.Response(404)
            )
        ),
    )
    assert voice["content"] == b"ogg-opus"
    with pytest.raises(MediaProcessingError, match="rate limited"):
        synthesize_voice_note(
            _settings(),
            text="Retry",
            transport=httpx.MockTransport(lambda _request: httpx.Response(429)),
        )
    with pytest.raises(MediaProcessingError, match="not configured"):
        synthesize_voice_note(
            _settings(whatsapp_speech_enabled=False),
            text="Fallback",
        )


@pytest.mark.parametrize(
    ("status_code", "match"),
    (
        (500, "temporarily unavailable"),
        (400, "reliable voice note"),
    ),
)
def test_voice_generation_rejects_unreliable_provider_results(
    status_code: int,
    match: str,
) -> None:
    with pytest.raises(MediaProcessingError, match=match):
        synthesize_voice_note(
            _settings(),
            text="Please read this answer.",
            transport=httpx.MockTransport(lambda _request: httpx.Response(status_code)),
        )
    with pytest.raises(MediaProcessingError, match="not configured"):
        synthesize_voice_note(
            _settings(elevenlabs_tts_voice_id="bad!"),
            text="Please read this answer.",
        )
    with pytest.raises(MediaProcessingError, match="no text"):
        synthesize_voice_note(_settings(), text=" ")
    with pytest.raises(MediaProcessingError, match="empty response"):
        synthesize_voice_note(
            _settings(),
            text="Please read this answer.",
            transport=httpx.MockTransport(lambda _request: httpx.Response(200, content=b"")),
        )


@pytest.mark.parametrize(
    ("response", "match"),
    (
        (httpx.Response(429), "rate limited"),
        (httpx.Response(503), "temporarily unavailable"),
        (httpx.Response(400), "could not be transcribed"),
        (httpx.Response(200, text="not-json"), "invalid data"),
        (httpx.Response(200, json={}), "No speech"),
    ),
)
def test_transcription_failures_always_return_a_useful_fallback(
    response: httpx.Response,
    match: str,
) -> None:
    with pytest.raises(MediaProcessingError, match=match):
        transcribe_media(
            _settings(),
            content=b"audio-fixture",
            mime_type="audio/ogg",
            filename="note.ogg",
            transport=httpx.MockTransport(lambda _request: response),
        )


def test_voice_provider_transport_errors_are_sanitized() -> None:
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private-provider-detail", request=request)

    with pytest.raises(MediaProcessingError, match="temporarily unavailable") as transcription:
        transcribe_media(
            _settings(),
            content=b"audio-fixture",
            mime_type="audio/ogg",
            filename="note.ogg",
            transport=httpx.MockTransport(unavailable),
        )
    assert "private-provider-detail" not in str(transcription.value)

    with pytest.raises(MediaProcessingError, match="temporarily unavailable") as synthesis:
        synthesize_voice_note(
            _settings(),
            text="Please read this answer.",
            transport=httpx.MockTransport(unavailable),
        )
    assert "private-provider-detail" not in str(synthesis.value)


def test_sandbox_client_routes_every_operation_and_sanitizes_failures() -> None:
    requests: list[httpx.Request] = []

    def success(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        payload = json.loads(request.content) if request.content else {}
        return httpx.Response(
            200,
            json={
                "ok": True,
                "path": request.url.path,
                "payload": payload,
            },
        )

    client = SandboxClient(_settings(), transport=httpx.MockTransport(success))
    assert client.run_code(
        tenant_id="tenant-123456",
        workspace="session-1",
        language="python",
        code="print(2 + 2)",
    )["ok"]
    assert client.exec(
        tenant_id="tenant-123456",
        workspace="session-1",
        command="pwd",
    )["ok"]
    assert client.file_operation(
        tenant_id="tenant-123456",
        workspace="session-1",
        action="write",
        path="/workspace/plan.txt",
        content="Ghana plan",
    )["ok"]
    assert client.video_frames(
        tenant_id="tenant-123456",
        workspace="session-1",
        content=b"video",
        mime_type="video/mp4",
    )["ok"]
    assert client.document_pages(
        tenant_id="tenant-123456",
        workspace="session-1",
        content=b"%PDF fixture",
        mime_type="application/pdf",
    )["ok"]
    assert client.export_file(
        tenant_id="tenant-123456",
        workspace="session-1",
        path="/workspace/plan.csv",
        mime_type="text/csv",
    )["ok"]
    assert client.export_file(
        tenant_id="tenant-123456",
        workspace="session-1",
        path="/workspace/plan.pdf",
        mime_type="application/pdf",
    )["ok"]
    assert (
        client.generate_image(
            tenant_id="tenant-123456",
            prompt="  Create   a product poster  ",
        )["path"]
        == "/v1/media/image"
    )
    assert client.destroy(tenant_id="tenant-123456", workspace="session-1")["ok"]
    assert requests[0].headers["x-bumpa-tenant-id"] == "tenant-123456"
    assert requests[0].headers["authorization"].startswith("Bearer ")

    with pytest.raises(ValueError, match="language"):
        client.run_code(
            tenant_id="tenant-123456",
            workspace="session-1",
            language="ruby",
            code="puts 1",
        )
    with pytest.raises(ValueError, match="inside"):
        client.file_operation(
            tenant_id="tenant-123456",
            workspace="session-1",
            action="read",
            path="/etc/passwd",
        )
    with pytest.raises(ValueError, match="format"):
        client.video_frames(
            tenant_id="tenant-123456",
            workspace="session-1",
            content=b"video",
            mime_type="video/avi",
        )
    with pytest.raises(ValueError, match="format"):
        client.document_pages(
            tenant_id="tenant-123456",
            workspace="session-1",
            content=b"document",
            mime_type="application/msword",
        )
    with pytest.raises(ValueError, match="type"):
        client.export_file(
            tenant_id="tenant-123456",
            workspace="session-1",
            path="/workspace/macro.xlsm",
            mime_type="application/vnd.ms-excel.sheet.macroEnabled.12",
        )
    with pytest.raises(ValueError, match="URL"):
        SandboxClient(_settings(sandbox_worker_url="http://localhost"))

    for status, message in (
        (401, "authorization"),
        (429, "capacity"),
        (503, "unavailable"),
        (400, "rejected"),
    ):
        failed = SandboxClient(
            _settings(),
            transport=httpx.MockTransport(lambda _request, value=status: httpx.Response(value)),
        )
        with pytest.raises(SandboxProviderError, match=message):
            failed.exec(
                tenant_id="tenant-123456",
                workspace="session-1",
                command="pwd",
            )
