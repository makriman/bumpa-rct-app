from __future__ import annotations

import base64
import hashlib
from io import StringIO

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.cli.rotate_field_keys import APPLY_CONFIRMATION
from app.cli.rotate_field_keys import main as rotation_main
from app.core.config import Settings
from app.core.crypto import (
    FieldCipher,
    InvalidFieldCiphertextError,
    UnknownFieldEncryptionKeyError,
)
from app.db.base import Base
from app.db.models import BumpaConnection, HermesProfile, McpConnection, Tenant
from app.services.field_key_rotation import rotate_provider_credentials

CURRENT_SECRET = "current-field-key-material-that-is-long-enough"
OLD_SECRET = "previous-field-key-material-that-is-long-enough"


def _legacy_v1(plaintext: str, secret: str) -> str:
    nonce = b"legacy-nonce"
    key = hashlib.sha256(secret.encode()).digest()
    encrypted = AESGCM(key).encrypt(nonce, plaintext.encode(), b"bumpabestie:v1")
    return "v1." + base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")


def _frozen_v1_only_decrypt(envelope: str, secret: str) -> str:
    """Exact predecessor decoder contract, intentionally independent of FieldCipher."""

    version, encoded = envelope.split(".", 1)
    if version != "v1":
        raise ValueError("Unsupported ciphertext version")
    raw = base64.urlsafe_b64decode(encoded.encode())
    key = hashlib.sha256(secret.encode()).digest()
    return AESGCM(key).decrypt(raw[:12], raw[12:], b"bumpabestie:v1").decode()


def _production_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "app_env": "production",
        "jwt_secret": "j" * 40,
        "otp_secret": "o" * 40,
        "field_encryption_key": CURRENT_SECRET,
        "field_encryption_key_id": "key-2026-07",
        "field_encryption_old_keys": {"key-2026-01": OLD_SECRET},
        "research_pseudonym_key": "p" * 40,
        "onboarding_integrity_key": "i" * 40,
        "expose_local_otp": False,
        "seed_demo_data": False,
        "whatsapp_backend": "disabled",
        "bumpa_backend": "disabled",
        "agent_backend": "disabled",
    }
    values.update(overrides)
    return Settings(**values)


def test_v2_envelope_identifies_and_authenticates_its_key() -> None:
    cipher = FieldCipher(CURRENT_SECRET, key_id="current", old_keys={"alias": CURRENT_SECRET})
    envelope = cipher.encrypt("provider-secret")

    assert envelope.startswith("v2.current.")
    decrypted = cipher.decrypt_with_metadata(envelope)
    assert decrypted.plaintext == "provider-secret"
    assert decrypted.version == "v2"
    assert decrypted.key_id == "current"
    assert decrypted.needs_reencryption is False

    with pytest.raises(UnknownFieldEncryptionKeyError, match="not configured"):
        FieldCipher(CURRENT_SECRET, key_id="current").decrypt(
            envelope.replace("v2.current.", "v2.unknown.", 1)
        )
    with pytest.raises(InvalidFieldCiphertextError, match="authentication failed"):
        cipher.decrypt(envelope.replace("v2.current.", "v2.alias.", 1))


def test_staged_v1_writer_preserves_rollback_until_v2_is_enabled() -> None:
    staged = FieldCipher(CURRENT_SECRET, key_id="current", write_version="v1")
    envelope = staged.encrypt("provider-secret")

    assert envelope.startswith("v1.")
    # Exercise the frozen predecessor decoder rather than the new dual reader.
    assert _frozen_v1_only_decrypt(envelope, CURRENT_SECRET) == "provider-secret"
    assert staged.decrypt_with_metadata(envelope).needs_reencryption is False

    steady_state = FieldCipher(CURRENT_SECRET, key_id="current", write_version="v2")
    assert steady_state.decrypt_with_metadata(envelope).needs_reencryption is True
    v2_envelope = steady_state.encrypt("provider-secret")
    assert v2_envelope.startswith("v2.current.")
    with pytest.raises(ValueError, match="Unsupported ciphertext version"):
        _frozen_v1_only_decrypt(v2_envelope, CURRENT_SECRET)


def test_old_key_ring_reads_v2_and_legacy_v1_without_weak_fallback() -> None:
    old_envelope = FieldCipher(OLD_SECRET, key_id="old").encrypt("old-provider-secret")
    legacy_envelope = _legacy_v1("legacy-provider-secret", OLD_SECRET)
    cipher = FieldCipher(CURRENT_SECRET, key_id="current", old_keys={"old": OLD_SECRET})

    old = cipher.decrypt_with_metadata(old_envelope)
    assert (old.plaintext, old.version, old.key_id, old.needs_reencryption) == (
        "old-provider-secret",
        "v2",
        "old",
        True,
    )
    legacy = cipher.decrypt_with_metadata(legacy_envelope)
    assert (legacy.plaintext, legacy.version, legacy.key_id, legacy.needs_reencryption) == (
        "legacy-provider-secret",
        "v1",
        "old",
        True,
    )

    without_old_key = FieldCipher(CURRENT_SECRET, key_id="current")
    with pytest.raises(UnknownFieldEncryptionKeyError):
        without_old_key.decrypt(old_envelope)
    with pytest.raises(InvalidFieldCiphertextError, match="authentication failed"):
        without_old_key.decrypt(legacy_envelope)


@pytest.mark.parametrize(
    "envelope",
    ["", "v2", "v2.current", "v2.current.not-base64!", "v3.payload", "v1.eA=="],
)
def test_malformed_ciphertext_fails_closed(envelope: str) -> None:
    with pytest.raises(InvalidFieldCiphertextError):
        FieldCipher(CURRENT_SECRET, key_id="current").decrypt(envelope)


def test_field_key_ring_configuration_is_typed_and_production_safe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY", CURRENT_SECRET)
    monkeypatch.setenv("FIELD_ENCRYPTION_KEY_ID", "key-2026-07")
    monkeypatch.setenv("FIELD_ENCRYPTION_WRITE_VERSION", "v1")
    monkeypatch.setenv("FIELD_ENCRYPTION_OLD_KEYS", f'{{"key-2026-01":"{OLD_SECRET}"}}')
    configured = Settings(_env_file=None)
    assert configured.field_encryption_key_id == "key-2026-07"
    assert configured.field_encryption_write_version == "v1"
    assert configured.field_encryption_old_keys == {"key-2026-01": OLD_SECRET}
    assert FieldCipher.from_settings(configured).current_key_id == "key-2026-07"
    assert FieldCipher.from_settings(configured).write_version == "v1"

    local_v2 = Settings(field_encryption_write_version="v2", _env_file=None)
    assert local_v2.is_local
    assert FieldCipher.from_settings(local_v2).write_version == "v2"

    with pytest.raises(ValidationError, match="must not appear"):
        _production_settings(
            field_encryption_old_keys={"key-2026-07": OLD_SECRET},
        )
    with pytest.raises(ValidationError, match="too short or use placeholders"):
        _production_settings(field_encryption_old_keys={"key-2026-01": "short"})
    with pytest.raises(ValidationError, match="At most 16"):
        _production_settings(
            field_encryption_old_keys={f"old-{index}": OLD_SECRET for index in range(17)}
        )
    with pytest.raises(ValidationError, match="first dual-reader soak"):
        _production_settings(field_encryption_write_version="v2")

    secret_canary = "must-never-appear-in-validation-errors"
    with pytest.raises(ValidationError) as invalid_ring:
        _production_settings(field_encryption_old_keys={"invalid.key": secret_canary})
    assert secret_canary not in str(invalid_ring.value)


def _credential_database() -> tuple[Session, BumpaConnection, HermesProfile, McpConnection]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = Session(engine, expire_on_commit=False)
    tenant = Tenant(slug="rotation-test", name="Rotation Test", status="active")
    db.add(tenant)
    db.flush()
    bumpa = BumpaConnection(
        tenant_id=tenant.id,
        encrypted_api_key=_legacy_v1("bumpa-secret", OLD_SECRET),
        scope_type="business_id",
        scope_id="business-1",
        provider="bumpa",
        status="active",
    )
    hermes = HermesProfile(
        tenant_id=tenant.id,
        profile_name="rotation_profile",
        provider="hermes",
        profile_path="/data/hermes/profiles/rotation_profile",
        api_internal_url="http://hermes:8700/v1",
        api_port=8700,
        encrypted_api_key=FieldCipher(OLD_SECRET, key_id="old").encrypt("hermes-secret"),
        status="active",
    )
    mcp = McpConnection(
        tenant_id=tenant.id,
        provider="gmail",
        status="active",
        encrypted_credentials=FieldCipher(CURRENT_SECRET, key_id="current").encrypt("mcp-secret"),
        scopes=["read"],
        read_only=True,
        admin_approved=True,
    )
    db.add_all([bumpa, hermes, mcp])
    db.commit()
    return db, bumpa, hermes, mcp


def test_provider_credential_rotation_is_dry_run_first_atomic_and_idempotent() -> None:
    db, bumpa, hermes, mcp = _credential_database()
    cipher = FieldCipher(CURRENT_SECRET, key_id="current", old_keys={"old": OLD_SECRET})
    before = (bumpa.encrypted_api_key, hermes.encrypted_api_key, mcp.encrypted_credentials)
    try:
        dry_run = rotate_provider_credentials(db, cipher)
        assert dry_run.applied is False
        assert dry_run.public_dict()["totals"] == {
            "scanned": 3,
            "current": 1,
            "legacy_v1": 1,
            "old_key": 1,
            "would_rotate": 2,
            "rotated": 0,
        }
        assert (
            bumpa.encrypted_api_key,
            hermes.encrypted_api_key,
            mcp.encrypted_credentials,
        ) == before

        applied = rotate_provider_credentials(db, cipher, apply=True)
        assert applied.public_dict()["totals"] == {
            "scanned": 3,
            "current": 1,
            "legacy_v1": 1,
            "old_key": 1,
            "would_rotate": 2,
            "rotated": 2,
        }
        db.expire_all()
        assert cipher.decrypt(bumpa.encrypted_api_key) == "bumpa-secret"
        assert cipher.decrypt(hermes.encrypted_api_key) == "hermes-secret"
        assert mcp.encrypted_credentials is not None
        assert cipher.decrypt(mcp.encrypted_credentials) == "mcp-secret"
        assert bumpa.encrypted_api_key.startswith("v2.current.")
        assert hermes.encrypted_api_key.startswith("v2.current.")

        second = rotate_provider_credentials(db, cipher, apply=True)
        assert second.public_dict()["totals"] == {
            "scanned": 3,
            "current": 3,
            "legacy_v1": 0,
            "old_key": 0,
            "would_rotate": 0,
            "rotated": 0,
        }
    finally:
        db.close()


def test_unknown_key_aborts_rotation_without_changing_any_row() -> None:
    db, bumpa, hermes, mcp = _credential_database()
    unknown = FieldCipher(OLD_SECRET, key_id="unconfigured").encrypt("unknown")
    mcp.encrypted_credentials = unknown
    db.commit()
    before = (bumpa.encrypted_api_key, hermes.encrypted_api_key, mcp.encrypted_credentials)
    cipher = FieldCipher(CURRENT_SECRET, key_id="current", old_keys={"old": OLD_SECRET})
    try:
        with pytest.raises(UnknownFieldEncryptionKeyError):
            rotate_provider_credentials(db, cipher, apply=True)
        persisted = (
            db.scalar(select(BumpaConnection.encrypted_api_key)),
            db.scalar(select(HermesProfile.encrypted_api_key)),
            db.scalar(select(McpConnection.encrypted_credentials)),
        )
        assert persisted == before
    finally:
        db.close()


def test_rotation_cli_requires_explicit_apply_confirmation() -> None:
    output = StringIO()
    errors = StringIO()
    assert rotation_main(["--apply"], stdout=output, stderr=errors) == 2
    assert output.getvalue() == ""
    assert "apply_confirmation_required" in errors.getvalue()

    errors = StringIO()
    assert rotation_main(["--confirm", APPLY_CONFIRMATION], stdout=StringIO(), stderr=errors) == 2
    assert "confirmation_without_apply" in errors.getvalue()
