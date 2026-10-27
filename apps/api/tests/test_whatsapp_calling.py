from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from dataclasses import replace
from time import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, get_settings
from app.core.crypto import FieldCipher
from app.db.base import Base
from app.db.models import (
    AgentMessage,
    Conversation,
    HermesProfile,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
)
from app.main import app
from app.services.whatsapp_calls import split_call_changes
from app.voice import backend, gateway
from app.voice.backend import Caller, CallerDenied, VoiceAssistant
from app.voice.gateway import CallGateway
from app.voice.signaling import CallEvent, IncomingCall, MetaCalls, SignalingError, call_events


def settings(**overrides):
    return Settings(
        **{
            "app_env": "test",
            "whatsapp_backend": "meta",
            "agent_backend": "hermes",
            "whatsapp_speech_enabled": True,
            "whatsapp_calling_enabled": True,
            "meta_waba_id": "123456789",
            "meta_phone_number_id": "987654321",
            "meta_app_secret": "calling-test-secret",
            "meta_system_user_access_token": "a" * 32,
            **overrides,
        }
    )


def event(kind="connect", **overrides):
    return CallEvent.model_validate(
        {
            "id": "wacid.test-call",
            "event": kind,
            "timestamp": int(time()),
            "from": "2348012345678",
            **(
                {"session": {"sdp_type": "offer", "sdp": "v=0\r\nsynthetic-offer\r\n"}}
                if kind == "connect"
                else {}
            ),
            **overrides,
        }
    )


def payload(call=None):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456789",
                "changes": [
                    {
                        "field": "calls",
                        "value": {
                            "metadata": {"phone_number_id": "987654321"},
                            "calls": [(call or event()).model_dump(by_alias=True)],
                        },
                    }
                ],
            }
        ],
    }


def sign(raw):
    return "sha256=" + hmac.new(b"calling-test-secret", raw, hashlib.sha256).hexdigest()


class MemoryRedis:
    def __init__(self):
        self.values = {}

    async def set(self, key, value, *, nx=False, ex=None):
        assert ex == 86_400
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def get(self, key):
        return self.values.get(key)


class FakeSession:
    instances = []

    def __init__(self, event, assistant, settings, signaling, on_finished):
        self.event, self.assistant = event, assistant
        self.on_finished = on_finished
        self.closed = False
        self.worker = None
        self.started = 0
        self.instances.append(self)

    async def start(self):
        self.started += 1
        await asyncio.sleep(0.01)

    async def close(self, **kwargs):
        self.closed = True
        self.assistant.closed.set()
        self.on_finished()


@pytest.fixture
def manager(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(gateway, "CallSession", FakeSession)
    monkeypatch.setattr(gateway, "authorize", lambda phone: Caller("tenant", "user", phone))
    return CallGateway(settings(), MemoryRedis(), SimpleNamespace(action=AsyncMock()))


def test_call_parser_scopes_sender_and_ignores_outbound():
    assert len(call_events(payload(), settings())) == 1
    assert not call_events(payload(), settings(meta_waba_id="different"))
    assert not call_events(payload(), settings(meta_phone_number_id="11111"))
    assert not call_events(payload(event(direction="BUSINESS_INITIATED")), settings())
    malformed = payload()
    malformed["entry"][0]["changes"][0]["value"]["calls"][0]["session"] = None
    with pytest.raises(ValueError, match="offer"):
        call_events(malformed, settings())


def test_call_changes_do_not_enter_durable_message_inbox():
    mixed = payload()
    message = {"field": "messages", "value": {"messages": [{"id": "msg1"}]}}
    mixed["entry"][0]["changes"].append(message)
    present, stripped = split_call_changes(mixed)
    assert present
    assert stripped["entry"][0]["changes"] == [message]
    assert "synthetic-offer" not in json.dumps(stripped)
    assert "synthetic-offer" in json.dumps(mixed)


def test_public_webhook_verifies_before_forwarding(monkeypatch):
    forward = AsyncMock()
    monkeypatch.setattr("app.routes.whatsapp.forward_call_webhook", forward)
    app.dependency_overrides[get_settings] = lambda: settings()
    try:
        with TestClient(app) as client:
            raw = json.dumps(payload()).encode()
            bad = client.post(
                "/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": "sha256=wrong"}
            )
            assert bad.status_code == 403
            forward.assert_not_awaited()
            good = client.post(
                "/webhooks/whatsapp", content=raw, headers={"X-Hub-Signature-256": sign(raw)}
            )
            assert good.status_code == 200
            assert good.json()["event_type"] == "calls"
            assert forward.await_args.args[:2] == (raw, sign(raw))
    finally:
        app.dependency_overrides.pop(get_settings, None)


def test_meta_signaling_uses_answer_and_hides_upstream_secrets():
    sent = []

    def handler(request):
        sent.append(request)
        return httpx.Response(200, json={"success": True})

    async def check():
        client = MetaCalls(settings(), httpx.MockTransport(handler))
        await client.action("wacid.test", "pre_accept", "answer-sdp")
        await client.action("wacid.test", "accept", "answer-sdp")
        await client.action("wacid.test", "terminate")
        assert json.loads(sent[0].content)["session"] == {"sdp_type": "answer", "sdp": "answer-sdp"}
        assert str(sent[0].url).endswith("/987654321/calls")
        assert "session" not in json.loads(sent[2].content)
        with pytest.raises(ValueError):
            await client.action("wacid.test", "connect")
        bad = MetaCalls(
            settings(),
            httpx.MockTransport(
                lambda request: httpx.Response(401, text="sensitive-provider-body")
            ),
        )
        with pytest.raises(SignalingError) as error:
            await bad.action("wacid.test", "reject")
        assert "sensitive" not in str(error.value)

    asyncio.run(check())


def test_concurrent_duplicate_and_hangup_never_start_second_assistant(manager):
    async def check():
        incoming = IncomingCall("987654321", event())
        await asyncio.gather(*(manager.handle(incoming) for _ in range(5)))
        assert len(FakeSession.instances) == 1
        assert FakeSession.instances[0].started == 1
        await manager.handle(IncomingCall("987654321", event("terminate")))
        assert not manager.sessions
        await manager.handle(incoming)
        assert len(FakeSession.instances) == 1
        manager.signaling.action.assert_awaited_with("wacid.test-call", "terminate")

    asyncio.run(check())


def test_out_of_order_termination_and_stale_offer(manager):
    async def check():
        await manager.handle(IncomingCall("987654321", event("terminate")))
        await manager.handle(IncomingCall("987654321", event()))
        await manager.handle(IncomingCall("987654321", event(id="wacid.old", timestamp=1)))
        assert not FakeSession.instances

    asyncio.run(check())


def test_restart_tombstone_does_not_resurrect_call(manager):
    async def check():
        incoming = IncomingCall("987654321", event())
        await manager.handle(incoming)
        restarted = CallGateway(settings(), manager.redis, manager.signaling)
        await restarted.handle(incoming)
        assert not restarted.sessions
        assert len(FakeSession.instances) == 1
        manager.signaling.action.assert_awaited_with("wacid.test-call", "terminate")
        await manager.shutdown()

    asyncio.run(check())


def test_unapproved_caller_and_capacity_rejected(manager, monkeypatch):
    async def check():
        await manager.handle(IncomingCall("987654321", event()))
        await manager.handle(IncomingCall("987654321", event(id="wacid.second")))
        assert len(FakeSession.instances) == 1
        manager.signaling.action.assert_awaited_with("wacid.second", "reject")
        await manager.shutdown()
        manager.stopping = False

        def denied(phone):
            raise CallerDenied()

        monkeypatch.setattr(gateway, "authorize", denied)
        await manager.handle(IncomingCall("987654321", event(id="wacid.denied")))
        manager.signaling.action.assert_awaited_with("wacid.denied", "reject")

    asyncio.run(check())


def test_redis_failure_cannot_answer(manager):
    async def check():
        manager.redis.set = AsyncMock(side_effect=RuntimeError("redis down"))
        with pytest.raises(RuntimeError):
            await manager.handle(IncomingCall("987654321", event()))
        assert not FakeSession.instances

    asyncio.run(check())


@pytest.fixture
def caller_db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'calls.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(backend, "SessionLocal", factory)
    with factory() as db:
        tenants = [Tenant(slug=f"calls-{i}-{uuid4().hex}", name=f"Business {i}") for i in range(2)]
        users = [User(primary_phone_e164=f"+234801234567{i}") for i in range(2)]
        db.add_all(tenants + users)
        db.flush()
        for i in range(2):
            db.add_all(
                [
                    PhoneIdentity(
                        tenant_id=tenants[i].id,
                        user_id=users[i].id,
                        phone_e164=users[i].primary_phone_e164,
                    ),
                    TenantMembership(tenant_id=tenants[i].id, user_id=users[i].id),
                    HermesProfile(
                        tenant_id=tenants[i].id,
                        profile_name=f"voice_{i}",
                        provider="hermes",
                        api_port=8700 + i,
                        profile_path="/profiles/test",
                        api_internal_url=f"http://hermes:{8700 + i}/v1",
                        encrypted_api_key=FieldCipher.from_settings(settings()).encrypt(
                            "test-key-123"
                        ),
                    ),
                ]
            )
        db.commit()
    yield factory, tenants, users
    engine.dispose()


@pytest.mark.parametrize(
    "target", ["identity", "opt_out", "membership", "user", "tenant", "profile"]
)
def test_authorization_revocation_and_tenant_scope(caller_db, target):
    factory, tenants, users = caller_db
    caller = backend.authorize(users[0].primary_phone_e164)
    assert caller.tenant_id == tenants[0].id
    with factory() as db:
        with pytest.raises(CallerDenied):
            backend._check(db, replace(caller, tenant_id=tenants[1].id))
        if target in {"identity", "opt_out"}:
            row = db.scalar(select(PhoneIdentity).where(PhoneIdentity.user_id == users[0].id))
            if target == "opt_out":
                row.opt_out = True
            else:
                row.status = "disabled"
        elif target == "membership":
            db.scalar(
                select(TenantMembership).where(TenantMembership.user_id == users[0].id)
            ).status = "disabled"
        elif target == "user":
            db.get(User, users[0].id).status = "disabled"
        elif target == "tenant":
            db.get(Tenant, tenants[0].id).status = "suspended"
        else:
            db.scalar(
                select(HermesProfile).where(HermesProfile.tenant_id == tenants[0].id)
            ).status = "disabled"
        db.commit()
    with pytest.raises(CallerDenied):
        backend.authorize(caller.phone)


def test_voice_turn_reuses_chat_and_saves_transcript(caller_db, monkeypatch):
    factory, tenants, users = caller_db
    from app.providers.hermes import HermesResult

    contexts = []

    def respond(self, endpoint, **kwargs):
        contexts.append(kwargs["business_context"])
        return HermesResult(
            content="Sales are unavailable.",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            latency_ms=1,
            session_id="voice-session",
            session_key=None,
        )

    monkeypatch.setattr("app.services.chat.HermesClient.respond", respond)
    monkeypatch.setattr(
        backend.HermesMediaClient, "transcribe", lambda *a, **kw: {"text": "How are sales?"}
    )
    monkeypatch.setattr(backend.HermesMediaClient, "synthesize", lambda *a, **kw: b"audio")
    assistant = VoiceAssistant(
        backend.authorize(users[0].primary_phone_e164), settings(), "call-hash"
    )
    assert assistant.respond(b"wav") == b"audio"
    conversation_id = assistant.conversation_id
    assert assistant.respond(b"wav") == b"audio"
    assert assistant.conversation_id == conversation_id
    with factory() as db:
        messages = db.scalars(select(AgentMessage)).all()
        assert len(messages) == 4
        assert db.get(Conversation, conversation_id).status == "voice"
        assert (
            db.scalar(
                select(Conversation).where(
                    Conversation.user_id == users[0].id,
                    Conversation.channel == "whatsapp",
                    Conversation.status == "open",
                )
            )
            is None
        )
        assert {m.tenant_id for m in messages} == {tenants[0].id}
        assert {m.conversation_id for m in messages} == {conversation_id}
        assert all(
            m.media_metadata["type"] == "voice_call" for m in messages if m.direction == "inbound"
        )
    assert all("live voice call" in context for context in contexts)
    assistant.closed.set()
    assert assistant.respond(b"wav") == b""
    assistant.finish()
    with factory() as db:
        assert db.get(Conversation, conversation_id).status == "closed"


@pytest.mark.parametrize(
    "transcription",
    [
        {"text": "transfer fifty thousand", "language_code": "en", "language_probability": 0.2},
        {"text": "transfer fifty thousand", "language_code": "fr", "language_probability": 0.99},
        None,
    ],
)
def test_uncertain_or_failed_call_audio_never_enters_chat(caller_db, monkeypatch, transcription):
    factory, _, users = caller_db
    assistant = VoiceAssistant(backend.authorize(users[0].primary_phone_e164), settings(), "safe")
    spoken = []

    def transcribe(*args, **kwargs):
        if transcription is None:
            raise backend.HermesMediaError("private provider error")
        return transcription

    def unexpected_chat(*args, **kwargs):
        pytest.fail("Uncertain audio must not reach chat or its tools")

    monkeypatch.setattr(backend.HermesMediaClient, "transcribe", transcribe)
    monkeypatch.setattr(backend, "handle_chat", unexpected_chat)
    monkeypatch.setattr(
        backend.HermesMediaClient,
        "synthesize",
        lambda *a, **kw: spoken.append(kw["text"]) or b"clarification",
    )
    assert assistant.respond(b"wav") == b"clarification"
    assert assistant.turn_number == 0
    assert assistant.conversation_id is None
    assert len(spoken) == 1
    assert "private provider error" not in spoken[0]
    with factory() as db:
        assert db.scalar(select(AgentMessage.id)) is None
        assert db.scalar(select(Conversation.id)) is None


def test_call_revoked_during_transcription_cannot_speak_or_run_chat(caller_db, monkeypatch):
    factory, _, users = caller_db
    assistant = VoiceAssistant(
        backend.authorize(users[0].primary_phone_e164), settings(), "revoked"
    )

    def transcribe(*args, **kwargs):
        with factory() as db:
            db.get(User, users[0].id).status = "disabled"
            db.commit()
        return {"text": "private sales", "language_code": "en", "language_probability": 0.2}

    def unexpected_effect(*args, **kwargs):
        pytest.fail("Revoked caller must not reach chat or speech output")

    monkeypatch.setattr(backend.HermesMediaClient, "transcribe", transcribe)
    monkeypatch.setattr(backend.HermesMediaClient, "synthesize", unexpected_effect)
    monkeypatch.setattr(backend, "handle_chat", unexpected_effect)
    with pytest.raises(CallerDenied):
        assistant.respond(b"wav")


@pytest.mark.parametrize(
    "override",
    [
        {"whatsapp_backend": "mock"},
        {"agent_backend": "mock"},
        {"whatsapp_speech_enabled": False},
        {"meta_primary_sender_enabled": False},
    ],
)
def test_calling_config_fails_closed(override):
    with pytest.raises(ValidationError):
        settings(**override)


def test_private_gateway_requires_signature_and_dispatches_primary_calls(monkeypatch):
    from app.voice import main as voice_main

    monkeypatch.setattr(voice_main, "get_settings", lambda: settings())
    handler = AsyncMock()
    voice_main.app.state.gateway = SimpleNamespace(handle=handler)

    async def check():
        raw = json.dumps(payload()).encode()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=voice_main.app), base_url="http://voice"
        ) as client:
            bad = await client.post("/webhooks/whatsapp/calls", content=raw)
            assert bad.status_code == 403
            handler.assert_not_awaited()
            good = await client.post(
                "/webhooks/whatsapp/calls", content=raw, headers={"X-Hub-Signature-256": sign(raw)}
            )
            assert good.status_code == 200
            assert handler.await_args.args[0].call.id == "wacid.test-call"
            invalid = b'{"entry":null}'
            response = await client.post(
                "/webhooks/whatsapp/calls",
                content=invalid,
                headers={"X-Hub-Signature-256": sign(invalid)},
            )
            assert response.status_code == 400
            handler.side_effect = RuntimeError("private detail")
            failure = await client.post(
                "/webhooks/whatsapp/calls", content=raw, headers={"X-Hub-Signature-256": sign(raw)}
            )
            assert failure.status_code == 503
            assert "private detail" not in failure.text
            large = await client.post("/webhooks/whatsapp/calls", content=b"x" * 1_048_577)
            assert large.status_code == 413

    asyncio.run(check())


def test_disabled_calling_never_contacts_voice_service(monkeypatch):
    from app.services.whatsapp_calls import forward_call_webhook

    def unexpected(**kwargs):
        raise AssertionError("Network must not be used when disabled")

    monkeypatch.setattr(httpx, "AsyncClient", unexpected)
    asyncio.run(forward_call_webhook(b"{}", "signature", settings(whatsapp_calling_enabled=False)))


def test_voice_relay_failure_returns_retryable_status(monkeypatch):
    from fastapi import HTTPException

    from app.services.whatsapp_calls import forward_call_webhook

    original = httpx.AsyncClient
    requests = []

    def response(request):
        requests.append(request)
        return httpx.Response(503)

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(response)),
    )
    with pytest.raises(HTTPException) as failure:
        asyncio.run(forward_call_webhook(b"signed-original-body", "signature", settings()))
    assert failure.value.status_code == 503
    assert requests[0].content == b"signed-original-body"
    assert requests[0].headers["x-hub-signature-256"] == "signature"


def test_call_ice_settings_reject_invalid_urls():
    with pytest.raises(ValidationError, match="ICE URLs"):
        settings(whatsapp_call_ice_urls="https://example.com")
