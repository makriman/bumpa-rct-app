from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import av
from aiortc import (
    RTCConfiguration,
    RTCIceServer,
    RTCPeerConnection,
    RTCRtpSender,
    RTCSessionDescription,
)
from aiortc.mediastreams import MediaStreamError

from app.core.config import Settings
from app.voice.backend import PlaybackReceipt, SpokenReply, VoiceAssistant
from app.voice.media import FRAME_BYTES, PlaybackState, PlaybackTrack, Utterances, decode_speech
from app.voice.signaling import CallEvent, MetaCalls

logger = logging.getLogger("bumpabestie.voice")


class CallSession:
    def __init__(
        self,
        event: CallEvent,
        assistant: VoiceAssistant,
        settings: Settings,
        signaling: MetaCalls,
        on_finished: Callable[[], None],
    ) -> None:
        self.event = event
        self.assistant = assistant
        self.settings = settings
        self.signaling = signaling
        self.on_finished = on_finished
        urls = [url.strip() for url in settings.whatsapp_call_ice_urls.split(",") if url.strip()]
        servers = (
            [
                RTCIceServer(
                    urls=urls,
                    username=settings.whatsapp_call_turn_username or None,
                    credential=settings.whatsapp_call_turn_credential or None,
                )
            ]
            if urls
            else []
        )
        self.pc = RTCPeerConnection(RTCConfiguration(iceServers=servers))
        self.media_accepted = asyncio.Event()
        self.output = PlaybackTrack(send_enabled=self.media_accepted)
        self.pc.addTrack(self.output)
        self.turns: asyncio.Queue[tuple[int, bytes] | None] = asyncio.Queue(maxsize=1)
        self.playback_updates: asyncio.Queue[tuple[PlaybackReceipt, PlaybackState] | None] = (
            asyncio.Queue()
        )
        self.playback_worker: asyncio.Task[None] | None = None
        self.closed = False
        self.worker: asyncio.Task[None] | None = None
        self.tasks: set[asyncio.Task[Any]] = set()
        self.receiving = False
        self.accepted = False
        self.speaking = False
        self.pc.on("track", self._on_track)
        self.pc.on("connectionstatechange", self._connection_changed)

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _on_track(self, track: Any) -> None:
        if track.kind == "audio" and not self.receiving:
            self.receiving = True
            self._spawn(self._receive(track))

    async def _connection_changed(self) -> None:
        if self.pc.connectionState in {"failed", "closed"} and not self.closed:
            await self.close(notify=True)

    async def start(self) -> None:
        assert self.event.session is not None
        try:
            async with asyncio.timeout(22):
                await self.pc.setRemoteDescription(
                    RTCSessionDescription(
                        sdp=self.event.session.sdp,
                        type="offer",
                    )
                )
                transceivers = self.pc.getTransceivers()
                if len(transceivers) != 1 or transceivers[0].kind != "audio":
                    raise ValueError("Only one audio track is supported")
                codecs = [
                    codec
                    for codec in RTCRtpSender.getCapabilities("audio").codecs
                    if codec.mimeType.lower() == "audio/opus"
                ]
                transceivers[0].setCodecPreferences(codecs)
                await self.pc.setLocalDescription(await self.pc.createAnswer())
                if transceivers[0].currentDirection != "sendrecv":
                    raise ValueError("Bidirectional audio is required")
                assert self.pc.localDescription is not None
                answer = self.pc.localDescription.sdp
                if self.closed:
                    return
                await self.signaling.action(self.event.id, "pre_accept", answer)
                if self.closed:
                    return
                await self.signaling.action(self.event.id, "accept", answer)
                if self.closed:
                    await self.signaling.action(self.event.id, "terminate")
                    return
                self.accepted = True
                self.media_accepted.set()
                self.worker = asyncio.create_task(self._work())
                self._spawn(self._expire())
        except BaseException:
            await self.close(notify=True)
            raise

    async def _expire(self) -> None:
        await asyncio.sleep(self.settings.whatsapp_call_max_seconds)
        await self.close(notify=True)

    async def _receive(self, track: Any) -> None:
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16_000)
        detector = Utterances(
            self.settings.whatsapp_call_silence_ms, self.settings.whatsapp_call_turn_max_seconds
        )
        pending = bytearray()
        try:
            while not self.closed:
                frame = await asyncio.wait_for(track.recv(), timeout=30)
                for converted in resampler.resample(frame):
                    pending.extend(bytes(converted.planes[0])[: converted.samples * 2])
                while len(pending) >= FRAME_BYTES:
                    pcm = bytes(pending[:FRAME_BYTES])
                    del pending[:FRAME_BYTES]
                    onset, utterance = detector.feed(pcm)
                    self.speaking = detector.active
                    if onset:
                        self.output.interrupt()
                    if utterance and self.accepted:
                        if self.turns.full():
                            self.turns.get_nowait()
                        self.turns.put_nowait((self.output.generation, utterance))
        except asyncio.CancelledError:
            raise
        except (MediaStreamError, TimeoutError):
            await self.close(notify=True)
        except Exception:
            logger.warning("voice_receive_failed")
            await self.close(notify=True)

    def _playback_changed(self, receipt: PlaybackReceipt, state: PlaybackState) -> None:
        self.playback_updates.put_nowait((receipt, state))
        if self.playback_worker is None:
            # Receipt writes are ordered and survive close() cancelling media
            # tasks. Never perform database I/O in AudioStreamTrack.recv().
            self.playback_worker = asyncio.create_task(self._record_playback_updates())

    async def _record_playback_updates(self) -> None:
        while True:
            update = await self.playback_updates.get()
            try:
                if update is None:
                    return
                receipt, state = update
                await asyncio.to_thread(self.assistant.record_playback, receipt, state)
            except Exception:
                logger.warning("voice_playback_receipt_failed")
                self._spawn(self.close(notify=True))
            finally:
                self.playback_updates.task_done()

    async def _drain_playback_updates(self) -> None:
        await self.playback_updates.join()

    async def _finish_playback_updates(self) -> None:
        await self._drain_playback_updates()
        if self.playback_worker is not None:
            self.playback_updates.put_nowait(None)
            await self.playback_worker
            self.playback_worker = None

    async def _play(
        self, content: bytes, generation: int, receipt: PlaybackReceipt | None = None
    ) -> None:
        def on_state(state: PlaybackState) -> None:
            if receipt is not None:
                self._playback_changed(receipt, state)

        if not content or self.closed or self.speaking or generation != self.output.generation:
            on_state("interrupted")
            return
        if receipt is not None and receipt.generation != generation:
            on_state("interrupted")
            return
        try:
            pcm = await asyncio.to_thread(decode_speech, content)
        except BaseException:
            on_state("interrupted")
            raise
        if self.closed or self.speaking:
            on_state("interrupted")
            return
        self.output.enqueue(pcm, generation, on_state=on_state if receipt is not None else None)

    async def _work(self) -> None:
        # Do not cancel this task on hangup: synchronous Hermes calls cannot be
        # cancelled safely. Keep the capacity slot until they drain, and discard
        # their audio. This also prevents unbounded abandoned worker threads.
        try:
            generation = 0
            greeting = await asyncio.to_thread(
                self.assistant.speak,
                "Hi, this is Bestie, your AI business assistant. "
                "I'll save a text transcript in your account. How can I help?",
            )
            await self._play(greeting, generation)
            while not self.closed:
                turn = await self.turns.get()
                if turn is None:
                    break
                generation, wav = turn
                if generation != self.output.generation:
                    continue
                # A barge-in queues interruption before the new utterance. Its
                # durable receipt must be visible to the next model request.
                await self._drain_playback_updates()
                if self.closed:
                    break
                respond_with_playback = getattr(self.assistant, "respond_with_playback", None)
                if callable(respond_with_playback):
                    reply = await asyncio.to_thread(
                        respond_with_playback, wav, generation=generation
                    )
                else:
                    # Byte-only test/provider adapters retain their old contract.
                    reply = SpokenReply(await asyncio.to_thread(self.assistant.respond, wav))
                await self._play(reply.audio, generation, reply.playback)
        except Exception:
            logger.warning("voice_assistant_failed")
            await self.close(notify=True)
        finally:
            try:
                self.output.interrupt()
                await self._finish_playback_updates()
                await asyncio.to_thread(self.assistant.finish)
            except Exception:
                logger.warning("voice_transcript_close_failed")
            self.on_finished()

    async def close(self, *, notify: bool = False) -> None:
        if self.closed:
            return
        self.closed = True
        self.assistant.closed.set()
        self.output.interrupt()
        self.output.stop()
        self.media_accepted.set()  # Unblock a pending recv so it observes the stopped track.
        for task in tuple(self.tasks):
            if task is not asyncio.current_task():
                task.cancel()
        if self.turns.full():
            self.turns.get_nowait()
        self.turns.put_nowait(None)
        await self.pc.close()
        await self._drain_playback_updates()
        if self.worker is None:
            await self._finish_playback_updates()
            self.on_finished()
        if notify:
            try:
                await self.signaling.action(self.event.id, "terminate")
            except Exception:
                logger.warning("voice_hangup_signal_failed")
