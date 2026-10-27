"""Stable routing namespace derived from verified transport metadata, never model text."""

import hashlib


def receiver_scope(lane: str, sender: tuple[str, str] | None) -> str:
    waba, phone = sender or ("mock", "mock")
    return hashlib.sha256(f"v1\0{lane}\0{waba}\0{phone}".encode()).hexdigest()
