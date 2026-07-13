from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
_NONCE_BYTES = 12
_TAG_BYTES = 16
_V1_AAD = b"bumpabestie:v1"


class FieldCipherError(ValueError):
    """Base error for field-cipher configuration and ciphertext failures."""


class InvalidFieldCiphertextError(FieldCipherError):
    """Raised when an envelope is malformed, unauthenticated, or undecodable."""


class UnknownFieldEncryptionKeyError(FieldCipherError):
    """Raised when a v2 envelope identifies a key that is not configured."""


@dataclass(frozen=True)
class DecryptedField:
    plaintext: str
    version: str
    key_id: str
    needs_reencryption: bool


class FieldCipher:
    """Authenticated field encryption with explicit key identity and legacy reads.

    The steady-state writer uses ``v2.<key-id>.<payload>``. The key ID is
    authenticated as associated data and selects exactly one configured key.
    A staged deployment can temporarily keep writing ``v1.<payload>`` so its
    predecessor remains a valid rollback target. V1 reads try the current key
    followed by the bounded old-key ring; AES-GCM authentication determines the
    matching key.
    """

    def __init__(
        self,
        secret: str,
        *,
        key_id: str = "primary",
        old_keys: Mapping[str, str] | None = None,
        write_version: Literal["v1", "v2"] = "v2",
    ) -> None:
        if not isinstance(secret, str) or not secret:
            raise FieldCipherError("Current field-encryption key is invalid")
        self._validate_key_id(key_id)
        configured_old_keys = dict(old_keys or {})
        if len(configured_old_keys) > 16:
            raise FieldCipherError("Too many old field-encryption keys are configured")
        if key_id in configured_old_keys:
            raise FieldCipherError("Current field-encryption key ID is duplicated")
        if write_version not in {"v1", "v2"}:
            raise FieldCipherError("Field-encryption write version is invalid")

        keys: dict[str, bytes] = {key_id: self._derive_key(secret)}
        for old_key_id, old_secret in configured_old_keys.items():
            self._validate_key_id(old_key_id)
            if not isinstance(old_secret, str) or not old_secret:
                raise FieldCipherError("Old field-encryption key is invalid")
            keys[old_key_id] = self._derive_key(old_secret)
        self._current_key_id = key_id
        self._keys = keys
        self._write_version = write_version

    @classmethod
    def from_settings(cls, settings: object) -> FieldCipher:
        """Build from Settings while tolerating narrow test settings objects."""

        secret = getattr(settings, "field_encryption_key", None)
        key_id = getattr(settings, "field_encryption_key_id", "primary")
        old_keys = getattr(settings, "field_encryption_old_keys", {})
        raw_write_version = getattr(settings, "field_encryption_write_version", "v1")
        if (
            not isinstance(secret, str)
            or not isinstance(key_id, str)
            or raw_write_version not in {"v1", "v2"}
        ):
            raise FieldCipherError("Field-encryption settings are invalid")
        if not isinstance(old_keys, Mapping):
            raise FieldCipherError("Old field-encryption key ring is invalid")
        write_version = cast(Literal["v1", "v2"], raw_write_version)
        return cls(
            secret,
            key_id=key_id,
            old_keys=old_keys,
            write_version=write_version,
        )

    @property
    def current_key_id(self) -> str:
        return self._current_key_id

    @property
    def write_version(self) -> Literal["v1", "v2"]:
        return self._write_version

    def encrypt(self, plaintext: str) -> str:
        if not isinstance(plaintext, str):
            raise TypeError("Field plaintext must be a string")
        nonce = os.urandom(_NONCE_BYTES)
        if self._write_version == "v1":
            encrypted = AESGCM(self._keys[self._current_key_id]).encrypt(
                nonce, plaintext.encode(), _V1_AAD
            )
            payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
            return f"v1.{payload}"
        aad = self._v2_aad(self._current_key_id)
        encrypted = AESGCM(self._keys[self._current_key_id]).encrypt(nonce, plaintext.encode(), aad)
        payload = base64.urlsafe_b64encode(nonce + encrypted).decode("ascii")
        return f"v2.{self._current_key_id}.{payload}"

    def decrypt(self, envelope: str) -> str:
        return self.decrypt_with_metadata(envelope).plaintext

    def decrypt_with_metadata(self, envelope: str) -> DecryptedField:
        if not isinstance(envelope, str) or not envelope:
            raise InvalidFieldCiphertextError("Invalid ciphertext envelope")
        version = envelope.partition(".")[0]
        if version == "v1":
            return self._decrypt_v1(envelope)
        if version == "v2":
            return self._decrypt_v2(envelope)
        raise InvalidFieldCiphertextError("Unsupported ciphertext version")

    def needs_reencryption(self, envelope: str) -> bool:
        return self.decrypt_with_metadata(envelope).needs_reencryption

    def _decrypt_v1(self, envelope: str) -> DecryptedField:
        parts = envelope.split(".")
        if len(parts) != 2 or not parts[1]:
            raise InvalidFieldCiphertextError("Invalid ciphertext envelope")
        raw = self._decode_payload(parts[1])
        for key_id, key in self._keys.items():
            try:
                plaintext = AESGCM(key).decrypt(raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], _V1_AAD)
                return DecryptedField(
                    plaintext=self._decode_plaintext(plaintext),
                    version="v1",
                    key_id=key_id,
                    needs_reencryption=(
                        key_id != self._current_key_id or self._write_version != "v1"
                    ),
                )
            except InvalidTag:
                continue
        raise InvalidFieldCiphertextError("Ciphertext authentication failed")

    def _decrypt_v2(self, envelope: str) -> DecryptedField:
        parts = envelope.split(".")
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise InvalidFieldCiphertextError("Invalid ciphertext envelope")
        key_id = parts[1]
        try:
            self._validate_key_id(key_id)
        except FieldCipherError as exc:
            raise InvalidFieldCiphertextError("Invalid ciphertext key ID") from exc
        key = self._keys.get(key_id)
        if key is None:
            raise UnknownFieldEncryptionKeyError("Ciphertext key ID is not configured")
        raw = self._decode_payload(parts[2])
        try:
            plaintext = AESGCM(key).decrypt(
                raw[:_NONCE_BYTES], raw[_NONCE_BYTES:], self._v2_aad(key_id)
            )
        except InvalidTag as exc:
            raise InvalidFieldCiphertextError("Ciphertext authentication failed") from exc
        return DecryptedField(
            plaintext=self._decode_plaintext(plaintext),
            version="v2",
            key_id=key_id,
            needs_reencryption=(key_id != self._current_key_id or self._write_version != "v2"),
        )

    @staticmethod
    def _derive_key(secret: str) -> bytes:
        # This intentionally preserves the v1 derivation contract.
        return hashlib.sha256(secret.encode()).digest()

    @staticmethod
    def _validate_key_id(key_id: str) -> None:
        if not isinstance(key_id, str) or not _KEY_ID_RE.fullmatch(key_id):
            raise FieldCipherError("Field-encryption key ID is invalid")

    @staticmethod
    def _v2_aad(key_id: str) -> bytes:
        return f"bumpabestie:v2:{key_id}".encode("ascii")

    @staticmethod
    def _decode_payload(encoded: str) -> bytes:
        try:
            raw = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
        except (UnicodeEncodeError, binascii.Error, ValueError) as exc:
            raise InvalidFieldCiphertextError("Invalid ciphertext encoding") from exc
        if len(raw) < _NONCE_BYTES + _TAG_BYTES:
            raise InvalidFieldCiphertextError("Invalid ciphertext payload")
        return raw

    @staticmethod
    def _decode_plaintext(value: bytes) -> str:
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise InvalidFieldCiphertextError("Invalid ciphertext plaintext") from exc


def secret_hash(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def secure_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
