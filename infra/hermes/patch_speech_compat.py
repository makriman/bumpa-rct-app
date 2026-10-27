"""Fail-closed compatibility overlay for faster-whisper 1.2.1 + PyAV 19.0.2.

PyAV 19 removed metadata_errors from av.open. The pinned faster-whisper decoder
still supplies it, so every file transcription fails before audio is decoded.
Only that removed keyword is changed; dependencies and audio decoding stay intact.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys


EXPECTED_FASTER_WHISPER = "1.2.1"
EXPECTED_AV = "19.0.2"
EXPECTED_SHA256 = "60a1d8638f718cbf6d245aed3e5a5aa61c1f822a0b0fe9b48a7c928d47c23909"
OLD_CALL = 'av.open(input_file, mode="r", metadata_errors="ignore")'
NEW_CALL = 'av.open(input_file, mode="r")'


def patch_source(source: str, *, faster_whisper_version: str, av_version: str) -> str:
    if (faster_whisper_version, av_version) != (EXPECTED_FASTER_WHISPER, EXPECTED_AV):
        raise RuntimeError("Speech library versions differ from reviewed compatibility pair")
    if hashlib.sha256(source.encode()).hexdigest() != EXPECTED_SHA256:
        raise RuntimeError("faster-whisper decoder source differs from reviewed pin")
    if source.count(OLD_CALL) != 1:
        raise RuntimeError("Unexpected faster-whisper file-decoder call")
    patched = source.replace(OLD_CALL, NEW_CALL)
    compile(patched, "faster_whisper/audio.py", "exec")
    return patched


def main() -> None:
    path = (
        Path(sys.argv[1]) if len(sys.argv) == 2 else
        importlib.metadata.distribution("faster-whisper").locate_file("faster_whisper/audio.py")
    )
    if len(sys.argv) > 2:
        raise RuntimeError("Expected at most one installed decoder source path")
    versions = {
        "faster_whisper_version": importlib.metadata.version("faster-whisper"),
        "av_version": importlib.metadata.version("av"),
    }
    patched = patch_source(path.read_text(), **versions)
    path.write_text(patched)
    print(json.dumps({"speech_compatibility_patch": "applied", **versions, "decoder_sha256": hashlib.sha256(patched.encode()).hexdigest()}, sort_keys=True))


if __name__ == "__main__":
    main()
