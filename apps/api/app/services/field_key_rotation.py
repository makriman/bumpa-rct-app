from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.crypto import DecryptedField, FieldCipher
from app.db.models import BumpaConnection, HermesProfile, McpConnection
from app.db.session import set_security_context

StoreName = Literal["bumpa_connections", "hermes_profiles", "mcp_connections"]


@dataclass
class CredentialStoreRotation:
    scanned: int = 0
    current: int = 0
    legacy_v1: int = 0
    old_key: int = 0
    would_rotate: int = 0
    rotated: int = 0

    def public_dict(self) -> dict[str, int]:
        return {
            "scanned": self.scanned,
            "current": self.current,
            "legacy_v1": self.legacy_v1,
            "old_key": self.old_key,
            "would_rotate": self.would_rotate,
            "rotated": self.rotated,
        }


@dataclass
class ProviderCredentialRotation:
    applied: bool
    current_key_id: str
    write_version: str
    stores: dict[StoreName, CredentialStoreRotation] = field(
        default_factory=lambda: {
            "bumpa_connections": CredentialStoreRotation(),
            "hermes_profiles": CredentialStoreRotation(),
            "mcp_connections": CredentialStoreRotation(),
        }
    )

    def public_dict(self) -> dict[str, object]:
        return {
            "applied": self.applied,
            "current_key_id": self.current_key_id,
            "write_version": self.write_version,
            "totals": {
                name: sum(getattr(store, name) for store in self.stores.values())
                for name in (
                    "scanned",
                    "current",
                    "legacy_v1",
                    "old_key",
                    "would_rotate",
                    "rotated",
                )
            },
            "stores": {name: store.public_dict() for name, store in self.stores.items()},
        }


@dataclass(frozen=True)
class _PendingCredential:
    record: BumpaConnection | HermesProfile | McpConnection
    attribute: Literal["encrypted_api_key", "encrypted_credentials"]
    plaintext: str
    store: StoreName


def rotate_provider_credentials(
    db: Session,
    cipher: FieldCipher,
    *,
    apply: bool = False,
) -> ProviderCredentialRotation:
    """Validate and optionally re-encrypt every durable provider credential.

    The function owns its transaction. All ciphertext is authenticated before
    any ORM record is mutated, and selected rows are locked on PostgreSQL. A
    decrypt, flush, or commit failure rolls the transaction back. Re-running
    after success is a no-op because current v2 envelopes are left untouched.
    """

    result = ProviderCredentialRotation(
        applied=apply,
        current_key_id=cipher.current_key_id,
        write_version=cipher.write_version,
    )
    pending: list[_PendingCredential] = []
    try:
        set_security_context(db, privileged=True)
        bumpa_connections = db.scalars(
            select(BumpaConnection).order_by(BumpaConnection.id).with_for_update()
        ).all()
        for bumpa_connection in bumpa_connections:
            _inspect(
                result,
                pending,
                cipher,
                store="bumpa_connections",
                record=bumpa_connection,
                attribute="encrypted_api_key",
                envelope=bumpa_connection.encrypted_api_key,
            )

        hermes_profiles = db.scalars(
            select(HermesProfile).order_by(HermesProfile.id).with_for_update()
        ).all()
        for profile in hermes_profiles:
            _inspect(
                result,
                pending,
                cipher,
                store="hermes_profiles",
                record=profile,
                attribute="encrypted_api_key",
                envelope=profile.encrypted_api_key,
            )

        mcp_connections = db.scalars(
            select(McpConnection).order_by(McpConnection.id).with_for_update()
        ).all()
        for mcp_connection in mcp_connections:
            if mcp_connection.encrypted_credentials is None:
                continue
            _inspect(
                result,
                pending,
                cipher,
                store="mcp_connections",
                record=mcp_connection,
                attribute="encrypted_credentials",
                envelope=mcp_connection.encrypted_credentials,
            )

        if not apply:
            db.rollback()
            return result

        for credential in pending:
            setattr(credential.record, credential.attribute, cipher.encrypt(credential.plaintext))
            result.stores[credential.store].rotated += 1
        db.flush()
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise


def _inspect(
    result: ProviderCredentialRotation,
    pending: list[_PendingCredential],
    cipher: FieldCipher,
    *,
    store: StoreName,
    record: BumpaConnection | HermesProfile | McpConnection,
    attribute: Literal["encrypted_api_key", "encrypted_credentials"],
    envelope: str,
) -> None:
    decrypted = cipher.decrypt_with_metadata(envelope)
    store_result = result.stores[store]
    store_result.scanned += 1
    _classify(store_result, decrypted)
    if decrypted.needs_reencryption:
        store_result.would_rotate += 1
        pending.append(
            _PendingCredential(
                record=record,
                attribute=attribute,
                plaintext=decrypted.plaintext,
                store=store,
            )
        )


def _classify(result: CredentialStoreRotation, decrypted: DecryptedField) -> None:
    if decrypted.version == "v1":
        result.legacy_v1 += 1
    elif decrypted.needs_reencryption:
        result.old_key += 1
    else:
        result.current += 1
