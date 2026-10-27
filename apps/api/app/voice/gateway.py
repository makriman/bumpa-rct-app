from __future__ import annotations

import asyncio
import hashlib
import logging
from time import time
from typing import Any

from app.core.config import Settings
from app.voice.backend import CallerDenied, VoiceAssistant, authorize
from app.voice.session import CallSession
from app.voice.signaling import IncomingCall, MetaCalls

logger = logging.getLogger("bumpabestie.voice")


class CallGateway:
    """One process owns peers; Redis tombstones prevent replay across restarts.

    Run exactly one replica/worker. A duplicate after a restart is terminated,
    never resurrected with a second peer or a second assistant execution.
    """

    def __init__(self, settings: Settings, redis: Any, signaling: MetaCalls | None = None) -> None:
        self.settings = settings
        self.redis = redis
        self.signaling = signaling or MetaCalls(settings)
        self.sessions: dict[str, CallSession] = {}
        self.setups: dict[str, asyncio.Task[None]] = {}
        self.lock = asyncio.Lock()
        self.stopping = False

    async def handle(self, incoming: IncomingCall) -> None:
        event = incoming.call
        key = hashlib.sha256(f"{incoming.phone_number_id}:{event.id}".encode()).hexdigest()
        redis_key = f"bumpabestie:voice:seen:{key}"
        if event.event == "terminate":
            # Record hangup first: an out-of-order connect must never answer.
            await self.redis.set(redis_key, "ended", ex=86_400)
            session = self.sessions.get(key)
            if session:
                await session.close()
            return
        if abs(time() - event.timestamp) > 90:
            return
        async with self.lock:
            if self.stopping:
                raise RuntimeError("Voice gateway is stopping")
            pending = self.setups.get(key)
            if pending is None:
                if key in self.sessions:
                    return
                claimed = await self.redis.set(redis_key, "claimed", nx=True, ex=86_400)
                if not claimed:
                    # It is terminal, or belonged to a previous process. There is
                    # no local media state to resume; fail closed.
                    await self._end_remote(event.id, "terminate")
                    return
                phone = "+" + event.from_.lstrip("+")
                if len(self.sessions) >= self.settings.whatsapp_call_max_concurrent or any(
                    s.assistant.caller.phone == phone for s in self.sessions.values()
                ):
                    await self._end_remote(event.id, "reject")
                    return
                try:
                    caller = await asyncio.to_thread(authorize, phone)
                except CallerDenied:
                    await self._end_remote(event.id, "reject")
                    return
                # Authorization yields; a terminate webhook may have arrived.
                if await self.redis.get(redis_key) not in {"claimed", b"claimed"}:
                    return

                def finished() -> None:
                    self.sessions.pop(key, None)

                session = CallSession(
                    event,
                    VoiceAssistant(caller, self.settings, key),
                    self.settings,
                    self.signaling,
                    on_finished=finished,
                )
                self.sessions[key] = session
                pending = asyncio.create_task(self._start(key, session))
                self.setups[key] = pending
        # A client disconnect must not orphan a partially accepted peer.
        await asyncio.shield(pending)

    async def _start(self, key: str, session: CallSession) -> None:
        try:
            await session.start()
        except Exception:
            logger.warning("voice_call_setup_failed")
            await session.close(notify=True)
        finally:
            self.setups.pop(key, None)

    async def _end_remote(self, call_id: str, action: str) -> None:
        # Let the request fail (and Meta retry) if reject/terminate failed.
        await self.signaling.action(call_id, action)

    async def shutdown(self) -> None:
        self.stopping = True
        await asyncio.gather(
            *(session.close(notify=True) for session in list(self.sessions.values()))
        )
        if self.setups:
            await asyncio.gather(*list(self.setups.values()), return_exceptions=True)
        workers = [session.worker for session in self.sessions.values() if session.worker]
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
