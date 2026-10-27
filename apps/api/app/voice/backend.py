from __future__ import annotations

from dataclasses import dataclass
from threading import Event, Lock
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.time import utcnow
from app.db.models import (
    AgentMessage,
    Conversation,
    HermesProfile,
    PhoneIdentity,
    Tenant,
    TenantMembership,
    User,
)
from app.db.session import SessionLocal, set_security_context
from app.providers.hermes import endpoint_for
from app.providers.hermes_media import HermesMediaClient, HermesMediaError
from app.services.chat import handle_chat
from app.voice.media import PlaybackState


class CallerDenied(RuntimeError):
    pass


@dataclass(frozen=True)
class Caller:
    tenant_id: str
    user_id: str
    phone: str


@dataclass(frozen=True)
class PlaybackReceipt:
    message_id: str
    conversation_id: str
    call_key: str
    playback_id: str
    generation: int


@dataclass(frozen=True)
class SpokenReply:
    audio: bytes
    playback: PlaybackReceipt | None = None


def authorize(phone: str) -> Caller:
    # Global phone lookup is the only privileged query. All business/profile and
    # conversation access below runs with the tenant's RLS context.
    with SessionLocal() as db:
        set_security_context(db, privileged=True)
        identity = db.scalar(select(PhoneIdentity).where(PhoneIdentity.phone_e164 == phone))
        if not identity or identity.status != "approved" or identity.opt_out:
            raise CallerDenied("Caller is not enabled")
        caller = Caller(identity.tenant_id, identity.user_id, phone)
        db.rollback()
        set_security_context(db, tenant_id=caller.tenant_id)
        _check(db, caller)
        return caller


def _check(db: Session, caller: Caller) -> tuple[Tenant, User, HermesProfile]:
    tenant = db.get(Tenant, caller.tenant_id)
    user = db.get(User, caller.user_id)
    identity = db.scalar(
        select(PhoneIdentity).where(
            PhoneIdentity.tenant_id == caller.tenant_id,
            PhoneIdentity.user_id == caller.user_id,
            PhoneIdentity.phone_e164 == caller.phone,
            PhoneIdentity.status == "approved",
            PhoneIdentity.opt_out.is_(False),
        )
    )
    member = db.scalar(
        select(TenantMembership.id).where(
            TenantMembership.tenant_id == caller.tenant_id,
            TenantMembership.user_id == caller.user_id,
            TenantMembership.status == "active",
        )
    )
    profile = db.scalar(
        select(HermesProfile).where(
            HermesProfile.tenant_id == caller.tenant_id,
            HermesProfile.status == "active",
            HermesProfile.provider == "hermes",
        )
    )
    if (
        not identity
        or not member
        or not tenant
        or tenant.status != "active"
        or not user
        or user.status != "active"
        or not profile
    ):
        raise CallerDenied("Caller is not enabled")
    return tenant, user, profile


class VoiceAssistant:
    def __init__(self, caller: Caller, settings: Settings, call_key: str) -> None:
        self.caller = caller
        self.settings = settings
        self.call_key = call_key
        self.conversation_id: str | None = None
        self.turn_number = 0
        self.closed = Event()
        self._playbacks: dict[str, PlaybackReceipt] = {}
        self._playbacks_lock = Lock()

    def speak(self, text: str) -> bytes:
        if self.closed.is_set():
            return b""
        with SessionLocal() as db:
            set_security_context(db, tenant_id=self.caller.tenant_id)
            _, _, profile = _check(db, self.caller)
            return HermesMediaClient(self.settings).synthesize(
                endpoint_for(profile, self.settings),
                text=text,
                language="en",
            )

    def respond(self, wav: bytes) -> bytes:
        """Preserve the byte-only adapter without claiming playback completion."""
        return self.respond_with_playback(wav, generation=0).audio

    def respond_with_playback(self, wav: bytes, *, generation: int) -> SpokenReply:
        if self.closed.is_set():
            return SpokenReply(b"")
        with SessionLocal() as db:
            set_security_context(db, tenant_id=self.caller.tenant_id)
            _, _, profile = _check(db, self.caller)
            try:
                transcript = HermesMediaClient(self.settings).transcribe(
                    endpoint_for(profile, self.settings),
                    content=wav,
                    mime_type="audio/wav",
                )
            except HermesMediaError:
                # A failed/no-speech utterance must not become a model request
                # or tear down an otherwise healthy call. speak() rechecks access.
                transcript = None
        if self.closed.is_set():
            return SpokenReply(b"")
        if transcript is None:
            return SpokenReply(
                self.speak("I couldn't process that audio. Please repeat your request.")
            )
        language = transcript.get("language_code")
        probability = transcript.get("language_probability")
        # This is language-detection confidence, not a guarantee of transcript
        # accuracy. Do not use uncertain/unsupported speech as instructions.
        if (language is not None and language != "en") or (
            isinstance(probability, int | float) and probability < 0.6
        ):
            return SpokenReply(
                self.speak(
                    "I couldn't understand that reliably. Please repeat in English. "
                    "I haven't acted on that request."
                )
            )
        # New transaction rechecks authorization after transcription. A call has
        # its own conversation so simultaneous text chats cannot corrupt sessions.
        with SessionLocal() as db:
            set_security_context(db, tenant_id=self.caller.tenant_id)
            tenant, user, _ = _check(db, self.caller)
            self.turn_number += 1
            conversation, incoming, outgoing, _ = handle_chat(
                db,
                tenant=tenant,
                user=user,
                message=transcript["text"],
                channel="whatsapp",
                conversation_id=self.conversation_id,
                external_message_id=f"voice:{self.call_key}:{self.turn_number}",
                media_metadata={
                    "type": "voice_call",
                    "turn": self.turn_number,
                    "phone_identity_id": db.scalar(
                        select(PhoneIdentity.id).where(
                            PhoneIdentity.phone_e164 == self.caller.phone,
                            PhoneIdentity.tenant_id == tenant.id,
                            PhoneIdentity.user_id == user.id,
                        )
                    ),
                },
                settings=self.settings,
            )
            self.conversation_id = conversation.id
            answer = outgoing.content
            receipt = PlaybackReceipt(
                outgoing.id, conversation.id, self.call_key, uuid4().hex, generation
            )
            outgoing.media_metadata = {
                **(outgoing.media_metadata or {}),
                "type": "voice_call",
                "turn": self.turn_number,
                "phone_identity_id": incoming.media_metadata.get("phone_identity_id"),
                "voice_call_key": self.call_key,
                "voice_playback_id": receipt.playback_id,
                "voice_playback_generation": generation,
                "voice_playback_state": "generated",
                "voice_playback_updated_at": utcnow().isoformat(),
            }
            db.commit()
            with self._playbacks_lock:
                self._playbacks[receipt.playback_id] = receipt
        if self.closed.is_set():
            self.record_playback(receipt, "interrupted")
            return SpokenReply(b"", receipt)
        try:
            audio = self.speak(answer)
        except Exception:
            self.record_playback(receipt, "interrupted")
            raise
        if self.closed.is_set() or not audio:
            self.record_playback(receipt, "interrupted")
            audio = b""
        return SpokenReply(audio, receipt)

    def record_playback(self, receipt: PlaybackReceipt, state: PlaybackState) -> bool:
        """Settle exactly one generated reply; terminal receipts never regress.

        These are operational receipts, so a revoked caller's unfinished output
        can still be marked interrupted. New model/speech work rechecks access.
        No callback can select another account, alias, call, or generation.
        """
        if receipt.call_key != self.call_key or state not in {"playing", "played", "interrupted"}:
            return False
        with SessionLocal() as db:
            set_security_context(db, tenant_id=self.caller.tenant_id)
            row = db.scalar(
                select(AgentMessage)
                .where(
                    AgentMessage.id == receipt.message_id,
                    AgentMessage.tenant_id == self.caller.tenant_id,
                    AgentMessage.user_id == self.caller.user_id,
                    AgentMessage.conversation_id == receipt.conversation_id,
                    AgentMessage.channel == "whatsapp",
                    AgentMessage.direction == "outbound",
                )
                .with_for_update()
            )
            if row is None:
                return False
            metadata = row.media_metadata or {}
            if (
                metadata.get("type") != "voice_call"
                or metadata.get("voice_call_key") != receipt.call_key
                or metadata.get("voice_playback_id") != receipt.playback_id
                or metadata.get("voice_playback_generation") != receipt.generation
            ):
                return False
            current = metadata.get("voice_playback_state")
            allowed = {
                "generated": {"playing", "interrupted"},
                "playing": {"played", "interrupted"},
            }
            if current != state and state not in allowed.get(str(current), set()):
                return False
            if current != state:
                row.media_metadata = {
                    **metadata,
                    "voice_playback_state": state,
                    "voice_playback_updated_at": utcnow().isoformat(),
                    "voice_playback_evidence": "local_webrtc_track",
                }
                db.commit()
        if state in {"played", "interrupted"}:
            with self._playbacks_lock:
                self._playbacks.pop(receipt.playback_id, None)
        return True

    def finish(self) -> None:
        with self._playbacks_lock:
            pending = list(self._playbacks.values())
        for receipt in pending:
            self.record_playback(receipt, "interrupted")
        if self.conversation_id is None:
            return
        with SessionLocal() as db:
            set_security_context(db, tenant_id=self.caller.tenant_id)
            conversation = db.scalar(
                select(Conversation).where(
                    Conversation.id == self.conversation_id,
                    Conversation.tenant_id == self.caller.tenant_id,
                    Conversation.user_id == self.caller.user_id,
                )
            )
            if conversation:
                conversation.status = "closed"
                db.commit()
