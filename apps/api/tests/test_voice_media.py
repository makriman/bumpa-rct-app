from __future__ import annotations

import asyncio
import io
import math
import wave
from array import array
from threading import Event
from types import SimpleNamespace

import av
import pytest
from aiortc import RTCConfiguration, RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import MediaStreamError

from app.voice.media import FRAME_BYTES, PlaybackTrack, Utterances, decode_speech, wav_bytes
from app.voice.session import CallSession
from tests.test_whatsapp_calling import event, settings


def tone():
    samples = array("h", (int(8000 * math.sin(i * math.tau * 440 / 16_000)) for i in range(16_000)))
    return wav_bytes(samples.tobytes())


class Vad:
    def is_speech(self, pcm, rate):
        assert rate == 16_000
        return pcm[0] == 1


def test_vad_debounce_silence_boundary_and_maximum_turn():
    detector = Utterances(300, 2, Vad())
    voice, quiet = b"\x01" * FRAME_BYTES, b"\x00" * FRAME_BYTES
    assert detector.feed(voice) == (False, None)
    assert detector.feed(voice) == (False, None)
    assert detector.feed(voice) == (True, None)
    for _ in range(14):
        assert detector.feed(quiet) == (False, None)
    onset, content = detector.feed(quiet)
    assert not onset and content
    with wave.open(io.BytesIO(content), "rb") as audio:
        assert audio.getframerate() == 16_000
        assert audio.getnframes() == 18 * 320
    assert not detector.active
    for _ in range(99):
        _, content = detector.feed(voice)
        assert content is None
    assert detector.feed(voice)[1] is not None


def test_playback_resamples_paces_and_discards_interrupted_generation():
    async def check():
        output = PlaybackTrack()
        pcm = decode_speech(tone())
        assert len(pcm) == 48_000 * 2
        output.enqueue(pcm, 0)
        first = await output.recv()
        assert first.sample_rate == 48_000
        assert first.samples == 960
        assert any(bytes(first.planes[0]))
        output.interrupt()
        output.enqueue(pcm, 0)
        assert not output.frames
        silence = await output.recv()
        assert silence.pts == first.pts + 960
        assert not any(bytes(silence.planes[0]))
        output.stop()

    asyncio.run(check())


def test_real_webrtc_offer_answer_carries_assistant_audio(monkeypatch):
    from app.voice import session as session_module

    class EnergyVad:
        def is_speech(self, pcm, rate):
            return max(abs(sample) for sample in array("h", pcm)) > 500

    monkeypatch.setattr(
        session_module,
        "Utterances",
        lambda silence, maximum: Utterances(silence, maximum, EnergyVad()),
    )

    async def check():
        caller = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        microphone = PlaybackTrack()
        caller.addTrack(microphone)
        received_track = asyncio.get_running_loop().create_future()

        @caller.on("track")
        def track_received(track):
            received_track.set_result(track)

        await caller.setLocalDescription(await caller.createOffer())
        actions = []

        async def signal(call_id, action, sdp=None):
            actions.append(action)
            if action == "pre_accept":
                assert sdp != caller.localDescription.sdp
                await caller.setRemoteDescription(RTCSessionDescription(sdp=sdp, type="answer"))

        heard = []
        heard_event = asyncio.Event()
        loop = asyncio.get_running_loop()

        def reply(wav):
            heard.append(wav)
            loop.call_soon_threadsafe(heard_event.set)
            return tone()

        assistant = SimpleNamespace(
            closed=Event(), speak=lambda text: tone(), respond=reply, finish=lambda: None
        )
        finished = []
        session = CallSession(
            event(session={"sdp_type": "offer", "sdp": caller.localDescription.sdp}),
            assistant,
            settings(),
            SimpleNamespace(action=signal),
            lambda: finished.append(True),
        )
        try:
            await session.start()
            track = await asyncio.wait_for(received_track, 5)
            async with asyncio.timeout(10):
                while True:
                    frame = await track.recv()
                    assert isinstance(frame, av.AudioFrame)
                    if any(bytes(frame.planes[0])):
                        break
            assert actions[:2] == ["pre_accept", "accept"]
            assert caller.connectionState == "connected"
            assert session.pc.connectionState == "connected"
            microphone.enqueue(decode_speech(tone()), 0)
            await asyncio.wait_for(heard_event.wait(), 5)
            with wave.open(io.BytesIO(heard[0]), "rb") as captured:
                assert captured.getframerate() == 16_000
                assert captured.getnframes() >= 16_000
                assert any(captured.readframes(captured.getnframes()))
        finally:
            await session.close(notify=True)
            if session.worker:
                await session.worker
            await caller.close()
        assert assistant.closed.is_set()
        assert actions[-1] == "terminate"
        assert finished == [True]

    asyncio.run(check())


def test_hangup_during_preaccept_never_accepts():
    async def check():
        caller = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        caller.addTrack(PlaybackTrack())
        await caller.setLocalDescription(await caller.createOffer())
        entered, release = asyncio.Event(), asyncio.Event()
        actions = []

        async def signal(call_id, action, sdp=None):
            actions.append(action)
            if action == "pre_accept":
                entered.set()
                await release.wait()

        assistant = SimpleNamespace(closed=Event())
        session = CallSession(
            event(session={"sdp_type": "offer", "sdp": caller.localDescription.sdp}),
            assistant,
            settings(),
            SimpleNamespace(action=signal),
            lambda: None,
        )
        task = asyncio.create_task(session.start())
        try:
            await asyncio.wait_for(entered.wait(), 5)
            await session.close()
            release.set()
            await task
            assert "accept" not in actions
            assert session.worker is None
            assert session.pc.connectionState == "closed"
        finally:
            release.set()
            await session.close()
            await caller.close()
            await task

    asyncio.run(check())


@pytest.mark.parametrize("accept_fails", [False, True])
def test_call_sends_no_media_until_accept_succeeds(accept_fails):
    async def check():
        caller = RTCPeerConnection(RTCConfiguration(iceServers=[]))
        caller.addTrack(PlaybackTrack())
        await caller.setLocalDescription(await caller.createOffer())
        assistant = SimpleNamespace(closed=Event(), speak=lambda text: b"", finish=lambda: None)

        async def signal(call_id, action, sdp=None):
            if action in {"pre_accept", "accept"}:
                assert not session.media_accepted.is_set()
                assert not pending.done()
            if action == "accept" and accept_fails:
                raise RuntimeError("Synthetic acceptance failure")

        session = CallSession(
            event(session={"sdp_type": "offer", "sdp": caller.localDescription.sdp}),
            assistant,
            settings(),
            SimpleNamespace(action=signal),
            lambda: None,
        )
        pending = asyncio.create_task(session.output.recv())
        await asyncio.sleep(0)
        assert not pending.done()
        try:
            if accept_fails:
                with pytest.raises(RuntimeError, match="Synthetic acceptance failure"):
                    await session.start()
                with pytest.raises(MediaStreamError):
                    await pending
            else:
                await session.start()
                assert session.accepted
                assert (await asyncio.wait_for(pending, 1)).samples == 960
        finally:
            await session.close()
            if session.worker:
                await session.worker
            await caller.close()

    asyncio.run(check())


def test_invalid_sdp_closes_peer_and_releases_capacity():
    async def check():
        actions, finished = [], []

        async def signal(call_id, action, sdp=None):
            actions.append(action)

        assistant = SimpleNamespace(closed=Event())
        session = CallSession(
            event(),
            assistant,
            settings(),
            SimpleNamespace(action=signal),
            lambda: finished.append(True),
        )
        with pytest.raises((ValueError, AssertionError)):
            await session.start()
        assert session.closed
        assert session.pc.connectionState == "closed"
        assert finished == [True]
        assert actions == ["terminate"]

    asyncio.run(check())


def test_hangup_drains_provider_before_releasing_capacity():
    async def check():
        entered, release = Event(), Event()
        finished = []

        def speak(text):
            entered.set()
            assert release.wait(timeout=5)
            return tone()

        async def signal(*args):
            return None

        assistant = SimpleNamespace(closed=Event(), speak=speak, finish=lambda: None)
        session = CallSession(
            event(),
            assistant,
            settings(),
            SimpleNamespace(action=signal),
            lambda: finished.append(True),
        )
        session.worker = asyncio.create_task(session._work())
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            await session.close()
            assert assistant.closed.is_set()
            assert not finished
            assert not session.worker.done()
        finally:
            release.set()
            await session.worker
        assert finished == [True]
        assert not session.output.frames

    asyncio.run(check())
