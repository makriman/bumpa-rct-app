"""Bounded, paced PCM playback and utterance detection for the WebRTC gateway."""

from __future__ import annotations

import asyncio
import io
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from time import monotonic
from typing import Any, Literal

import av
import webrtcvad  # type: ignore[import-untyped]
from aiortc import AudioStreamTrack
from aiortc.mediastreams import MediaStreamError

RATE = 16_000
FRAME_SAMPLES = 320
FRAME_BYTES = FRAME_SAMPLES * 2
PlaybackState = Literal["playing", "played", "interrupted"]


@dataclass
class _Playback:
    remaining_frames: int
    on_state: Callable[[PlaybackState], None] | None
    started: bool = False


def wav_bytes(pcm: bytes) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(pcm)
    return output.getvalue()


def decode_speech(content: bytes) -> bytes:
    """Decode local Piper output; cap playback at 60 seconds to bound memory."""
    resampler = av.AudioResampler(format="s16", layout="mono", rate=48_000)
    result = bytearray()
    with av.open(io.BytesIO(content), mode="r") as container:
        for frame in container.decode(audio=0):
            for converted in resampler.resample(frame):
                result.extend(bytes(converted.planes[0])[: converted.samples * 2])
                if len(result) > 48_000 * 2 * 60:
                    raise ValueError("Spoken answer exceeds playback limit")
        for converted in resampler.resample(None):
            result.extend(bytes(converted.planes[0])[: converted.samples * 2])
    if len(result) > 48_000 * 2 * 60:
        raise ValueError("Spoken answer exceeds playback limit")
    return bytes(result)


class PlaybackTrack(AudioStreamTrack):
    """Always send paced 20ms audio, including silence between assistant turns."""

    def __init__(self, *, send_enabled: asyncio.Event | None = None) -> None:
        super().__init__()
        self.frames: deque[bytes] = deque()
        self._playbacks: deque[_Playback] = deque()
        self.generation = 0
        self._pts = 0
        self._next = monotonic()
        self._send_enabled = send_enabled

    def interrupt(self) -> int:
        self.generation += 1
        self.frames.clear()
        pending, self._playbacks = self._playbacks, deque()
        for playback in pending:
            if playback.on_state is not None:
                playback.on_state("interrupted")
        return self.generation

    def enqueue(
        self,
        pcm: bytes,
        generation: int,
        *,
        on_state: Callable[[PlaybackState], None] | None = None,
    ) -> None:
        if not pcm or generation != self.generation or self.readyState != "live":
            if on_state is not None:
                on_state("interrupted")
            return
        chunks = [pcm[index : index + 1920] for index in range(0, len(pcm), 1920)]
        self.frames.extend(chunks)
        self._playbacks.append(_Playback(len(chunks), on_state))

    def stop(self) -> None:
        if self.readyState == "live":
            self.interrupt()
        super().stop()

    async def recv(self) -> av.AudioFrame:
        if self._send_enabled is not None:
            await self._send_enabled.wait()
        if self.readyState != "live":
            raise MediaStreamError
        await asyncio.sleep(max(0, self._next - monotonic()))
        if self.readyState != "live":
            raise MediaStreamError
        self._next = max(self._next + 0.02, monotonic())
        pcm = self.frames.popleft() if self.frames else b""
        frame = av.AudioFrame(format="s16", layout="mono", samples=960)
        frame.planes[0].update(pcm.ljust(1920, b"\x00"))
        frame.sample_rate = 48_000
        frame.pts = self._pts
        frame.time_base = Fraction(1, 48_000)
        self._pts += 960
        if pcm and self._playbacks:
            playback = self._playbacks[0]
            if not playback.started:
                playback.started = True
                if playback.on_state is not None:
                    playback.on_state("playing")
            playback.remaining_frames -= 1
            if playback.remaining_frames == 0:
                self._playbacks.popleft()
                if playback.on_state is not None:
                    # This proves PCM consumption by the local WebRTC track,
                    # not reception, audibility, or human understanding.
                    playback.on_state("played")
        return frame


class Utterances:
    """VAD with pre-roll, onset debounce, silence endpointing and a hard turn cap."""

    def __init__(self, silence_ms: int, max_seconds: int, vad: Any = None) -> None:
        self.vad = vad or webrtcvad.Vad(2)
        self.silence_frames = silence_ms // 20
        self.max_frames = max_seconds * 50
        self.pre: deque[bytes] = deque(maxlen=10)
        self.frames: list[bytes] = []
        self.voiced = 0
        self.silent = 0
        self.active = False

    def feed(self, pcm: bytes) -> tuple[bool, bytes | None]:
        voiced = self.vad.is_speech(pcm, RATE)
        onset = False
        if not self.active:
            self.pre.append(pcm)
            self.voiced = self.voiced + 1 if voiced else 0
            if self.voiced >= 3:
                self.active = True
                onset = True
                self.frames = list(self.pre)
                self.pre.clear()
        else:
            self.frames.append(pcm)
        if not self.active:
            return False, None
        self.silent = 0 if voiced else self.silent + 1
        if self.silent >= self.silence_frames or len(self.frames) >= self.max_frames:
            result = wav_bytes(b"".join(self.frames))
            self.frames = []
            self.active = False
            self.voiced = self.silent = 0
            return onset, result
        return onset, None
