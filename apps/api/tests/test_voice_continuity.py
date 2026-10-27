from __future__ import annotations

import asyncio
from dataclasses import replace
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.db.models import AgentMessage, Conversation
from app.providers.hermes import HermesResult
from app.voice import backend
from app.voice.backend import SpokenReply, VoiceAssistant
from app.voice.media import PlaybackTrack
from app.voice.session import CallSession
from tests.test_whatsapp_calling import caller_db as caller_db
from tests.test_whatsapp_calling import event, settings


@pytest.fixture
def voice(caller_db, monkeypatch):
    factory, _, users = caller_db
    calls = []

    def respond(self, endpoint, *, history=None, **kwargs):
        calls.append({**kwargs, "history": history})
        return HermesResult(
            content=f"Generated answer {len(calls)}.",
            input_tokens=1,
            output_tokens=1,
            total_tokens=2,
            latency_ms=1,
            session_id=None,
            session_key=None,
        )

    monkeypatch.setattr("app.services.chat.HermesClient.respond", respond)
    monkeypatch.setattr(
        backend.HermesMediaClient, "transcribe", lambda *args, **kwargs: {"text": "How are sales?"}
    )
    monkeypatch.setattr(
        backend.HermesMediaClient, "synthesize", lambda *args, **kwargs: b"synthetic-audio"
    )
    monkeypatch.setattr("app.voice.session.decode_speech", lambda audio: b"\x01" * (1920 * 2))
    assistant = VoiceAssistant(
        backend.authorize(users[0].primary_phone_e164), settings(), "bounded-call-one"
    )
    return factory, assistant, calls


def playback_state(factory, receipt):
    with factory() as db:
        return db.get(AgentMessage, receipt.message_id).media_metadata["voice_playback_state"]


def call_session(assistant):
    async def signal(*args):
        return None

    return CallSession(event(), assistant, settings(), SimpleNamespace(action=signal), lambda: None)


def test_generated_reply_is_not_a_played_transcript(voice):
    factory, assistant, _ = voice
    reply = assistant.respond_with_playback(b"wav", generation=0)
    assert reply.audio == b"synthetic-audio"
    assert reply.playback is not None
    assert playback_state(factory, reply.playback) == "generated"
    # The legacy byte API remains usable without claiming playback completion.
    assert assistant.respond(b"wav") == b"synthetic-audio"
    assistant.finish()
    assert playback_state(factory, reply.playback) == "interrupted"


@pytest.mark.parametrize("drop", ["before_start", "stale_generation", "speaking"])
def test_unstarted_audio_is_interrupted_without_becoming_played(voice, drop):
    factory, assistant, _ = voice

    async def check():
        session = call_session(assistant)
        reply = assistant.respond_with_playback(b"wav", generation=0)
        assert reply.playback is not None
        try:
            if drop == "stale_generation":
                session.output.interrupt()
            if drop == "speaking":
                session.speaking = True
            await session._play(reply.audio, 0, reply.playback)
            if drop == "before_start":
                session.output.interrupt()
            await session._drain_playback_updates()
            assert playback_state(factory, reply.playback) == "interrupted"
            assert not session.output.frames
        finally:
            await session.close()
            assistant.finish()

    asyncio.run(check())


def test_midplay_barge_in_is_durable_before_next_model_turn(voice):
    factory, assistant, model_calls = voice

    async def check():
        session = call_session(assistant)
        session.media_accepted.set()
        reply = assistant.respond_with_playback(b"wav", generation=0)
        assert reply.playback is not None
        try:
            await session._play(reply.audio, 0, reply.playback)
            await session.output.recv()
            await session._drain_playback_updates()
            assert playback_state(factory, reply.playback) == "playing"
            session.output.interrupt()
            await session._drain_playback_updates()
            assert playback_state(factory, reply.playback) == "interrupted"
            assert not assistant.record_playback(reply.playback, "played")
            assert playback_state(factory, reply.playback) == "interrupted"
            assistant.respond_with_playback(b"next utterance", generation=1)
            assert not any(
                "Generated answer 1." in row["content"] for row in model_calls[-1]["history"]
            )
        finally:
            await session.close()
            assistant.finish()

    asyncio.run(check())


def test_whole_track_consumption_completes_only_its_exact_reply(voice):
    factory, assistant, model_calls = voice

    async def check():
        session = call_session(assistant)
        session.media_accepted.set()
        reply = assistant.respond_with_playback(b"wav", generation=0)
        assert reply.playback is not None
        try:
            await session._play(reply.audio, 0, reply.playback)
            await session.output.recv()
            await session._drain_playback_updates()
            assert playback_state(factory, reply.playback) == "playing"
            await session.output.recv()
            await session._drain_playback_updates()
            assert playback_state(factory, reply.playback) == "played"
            await session.output.recv()  # Silence cannot create or change a receipt.
            assert not assistant.record_playback(reply.playback, "interrupted")
            assistant.respond_with_playback(b"next utterance", generation=1)
            assert any(
                "Generated answer 1." in row["content"] for row in model_calls[-1]["history"]
            )
        finally:
            await session.close()
            assistant.finish()
        assert playback_state(factory, reply.playback) == "played"

    asyncio.run(check())


def test_late_old_generation_or_another_call_cannot_update_new_reply(voice):
    factory, assistant, _ = voice
    first = assistant.respond_with_playback(b"wav", generation=0).playback
    assert first is not None
    assert assistant.record_playback(first, "interrupted")
    second = assistant.respond_with_playback(b"wav", generation=1).playback
    assert second is not None
    assert assistant.record_playback(second, "playing")
    assert not assistant.record_playback(first, "played")
    assert not assistant.record_playback(replace(first, message_id=second.message_id), "played")
    assert not assistant.record_playback(replace(second, generation=0), "played")
    other_call = VoiceAssistant(assistant.caller, settings(), "another-alias-call")
    assert not other_call.record_playback(second, "played")
    assert playback_state(factory, first) == "interrupted"
    assert playback_state(factory, second) == "playing"
    assert assistant.record_playback(second, "played")
    assistant.finish()


def test_next_model_waits_for_the_previous_interruption_receipt(voice, monkeypatch):
    factory, assistant, _ = voice

    async def check():
        session = call_session(assistant)
        session.media_accepted.set()
        reply = assistant.respond_with_playback(b"wav", generation=0)
        assert reply.playback is not None
        entered, release = Event(), Event()
        resumed = asyncio.Event()
        loop = asyncio.get_running_loop()
        record = assistant.record_playback

        def record_after_release(receipt, state):
            if state == "interrupted":
                entered.set()
                assert release.wait(2)
            return record(receipt, state)

        def next_reply(*args, **kwargs):
            assert playback_state(factory, reply.playback) == "interrupted"
            loop.call_soon_threadsafe(resumed.set)
            return SpokenReply(b"")

        monkeypatch.setattr(assistant, "speak", lambda text: b"")
        monkeypatch.setattr(assistant, "record_playback", record_after_release)
        monkeypatch.setattr(assistant, "respond_with_playback", next_reply)
        try:
            await session._play(reply.audio, 0, reply.playback)
            await session.output.recv()
            await session._drain_playback_updates()
            session.output.interrupt()
            session.worker = asyncio.create_task(session._work())
            session.turns.put_nowait((session.output.generation, b"next utterance"))
            assert await asyncio.to_thread(entered.wait, 2)
            assert not resumed.is_set()
            release.set()
            await asyncio.wait_for(resumed.wait(), 2)
        finally:
            release.set()
            await session.close()
            if session.worker is not None:
                await session.worker

    asyncio.run(check())


def test_hangup_interrupts_pending_playback_and_closes_conversation(voice, monkeypatch):
    factory, assistant, _ = voice

    async def check():
        session = call_session(assistant)
        session.media_accepted.set()
        reply = assistant.respond_with_playback(b"wav", generation=0)
        assert reply.playback is not None
        monkeypatch.setattr(assistant, "speak", lambda text: b"")
        monkeypatch.setattr(assistant, "respond_with_playback", lambda *args, **kwargs: reply)
        queued = asyncio.Event()
        play = session._play

        async def queue_playback(*args):
            await play(*args)
            if len(args) == 3 and args[2] is not None:
                queued.set()

        monkeypatch.setattr(session, "_play", queue_playback)
        session.worker = asyncio.create_task(session._work())
        session.turns.put_nowait((0, b"wav"))
        try:
            await asyncio.wait_for(queued.wait(), 2)
            await session.output.recv()
            await session._drain_playback_updates()
            assert playback_state(factory, reply.playback) == "playing"
            await session.close()
            await session.worker
            assert playback_state(factory, reply.playback) == "interrupted"
            with factory() as db:
                assert db.get(Conversation, reply.playback.conversation_id).status == "closed"
        finally:
            await session.close()
            await session.worker

    asyncio.run(check())


def test_synthesis_failure_retains_generated_text_as_interrupted(voice, monkeypatch):
    factory, assistant, _ = voice

    def fail(*args, **kwargs):
        raise backend.HermesMediaError("synthetic speech unavailable")

    monkeypatch.setattr(backend.HermesMediaClient, "synthesize", fail)
    with pytest.raises(backend.HermesMediaError):
        assistant.respond_with_playback(b"wav", generation=0)
    with factory() as db:
        row = db.scalar(select(AgentMessage).where(AgentMessage.direction == "outbound"))
        assert row.media_metadata["voice_playback_state"] == "interrupted"


def test_playback_track_receipts_follow_audio_segments_and_accept_gate():
    async def check():
        accepted = asyncio.Event()
        track = PlaybackTrack(send_enabled=accepted)
        states = []
        track.enqueue(b"\x01" * (1920 * 2), 0, on_state=states.append)
        pending = asyncio.create_task(track.recv())
        await asyncio.sleep(0)
        assert states == []
        assert not pending.done()
        accepted.set()
        await pending
        assert states == ["playing"]
        track.stop()
        assert states == ["playing", "interrupted"]
        track.enqueue(b"\x01" * 1920, 0, on_state=states.append)
        assert states == ["playing", "interrupted", "interrupted"]

    asyncio.run(check())
