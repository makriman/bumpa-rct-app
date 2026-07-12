from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class FieldCipher:
    """Versioned authenticated encryption for provider credentials."""

    def __init__(self, secret: str) -> None:
        self._key = hashlib.sha256(secret.encode()).digest()

    def encrypt(self, plaintext: str) -> str:
        nonce = os.urandom(12)
        encrypted = AESGCM(self._key).encrypt(nonce, plaintext.encode(), b"bumpabestie:v1")
        return "v1." + base64.urlsafe_b64encode(nonce + encrypted).decode()

    def decrypt(self, envelope: str) -> str:
        version, encoded = envelope.split(".", 1)
        if version != "v1":
            raise ValueError("Unsupported ciphertext version")
        raw = base64.urlsafe_b64decode(encoded.encode())
        return AESGCM(self._key).decrypt(raw[:12], raw[12:], b"bumpabestie:v1").decode()


def secret_hash(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def secure_equals(left: str, right: str) -> bool:
    return hmac.compare_digest(left, right)
