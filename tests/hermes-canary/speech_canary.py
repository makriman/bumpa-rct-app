"""Offline fixed-phrase Piper -> PCM WAV -> faster-whisper smoke test.

Run only in the labelled full Hermes image with --network none and resource
limits. Timing reflects this local Mac's amd64 emulation, never droplet capacity.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import wave
from unittest.mock import patch


PHRASE = "The shop opens at nine in the morning."
PIPER = Path("/opt/hermes/models/piper/en_GB-alba-medium.onnx")
WHISPER = Path("/opt/hermes/models/faster-whisper-base")


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def distance(left: list[str], right: list[str]) -> int:
    row = list(range(len(right) + 1))
    for index, token in enumerate(left, 1):
        current = [index]
        for other, value in enumerate(right, 1):
            current.append(min(current[-1] + 1, row[other] + 1, row[other - 1] + (token != value)))
        row = current
    return row[-1]


def main() -> None:
    from piper import PiperVoice
    from faster_whisper import WhisperModel

    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="bumpa-offline-speech-canary-") as directory:
        wav_path = Path(directory) / "fixed-phrase.wav"
        tts_start = time.monotonic()
        voice = PiperVoice.load(PIPER, use_cuda=False)
        with wave.open(str(wav_path), "wb") as output:
            voice.synthesize_wav(PHRASE, output)
        tts_seconds = time.monotonic() - tts_start
        with wave.open(str(wav_path), "rb") as audio:
            metadata = {
                "channels": audio.getnchannels(), "sample_width_bytes": audio.getsampwidth(),
                "sample_rate_hz": audio.getframerate(), "frames": audio.getnframes(),
                "duration_seconds": audio.getnframes() / audio.getframerate(),
            }
            assert metadata["channels"] == 1
            assert metadata["sample_width_bytes"] == 2
            assert 1 < metadata["duration_seconds"] < 15
            assert any(audio.readframes(audio.getnframes())), "Piper produced only silence"
        print("SPEECH_PROGRESS=offline_piper_pcm_wav_passed", flush=True)
        stt_start = time.monotonic()
        model = WhisperModel(
            str(WHISPER), device="cpu", compute_type="int8", cpu_threads=2,
            num_workers=1, local_files_only=True,
        )
        mode = os.environ.get("SPEECH_INPUT_MODE", "file")
        audio_input = str(wav_path)
        if mode == "decoded-pcm-array":
            import numpy

            decoded = subprocess.run(
                ["ffmpeg", "-v", "error", "-i", str(wav_path), "-ac", "1", "-ar", "16000", "-f", "f32le", "pipe:1"],
                check=True, capture_output=True, timeout=30,
            )
            audio_input = numpy.frombuffer(decoded.stdout, dtype=numpy.float32)
        inputs = {"wav": audio_input}
        if mode == "file":
            ogg_path = Path(directory) / "fixed-phrase.ogg"
            subprocess.run(
                ["ffmpeg", "-v", "error", "-y", "-i", str(wav_path), "-c:a", "libopus", "-b:a", "48k", "-application", "voip", str(ogg_path)],
                check=True, capture_output=True, timeout=30,
            )
            inputs["ogg_opus"] = str(ogg_path)
        transcripts = {}
        rates = {}
        for audio_format, value in inputs.items():
            segments, info = model.transcribe(
                value, language="en", beam_size=1, best_of=1,
                vad_filter=False, condition_on_previous_text=False, temperature=0.0,
            )
            transcript = " ".join(segment.text.strip() for segment in segments).strip()
            expected, actual = words(PHRASE), words(transcript)
            error_rate = distance(expected, actual) / len(expected)
            assert transcript, "Whisper returned empty text"
            assert error_rate <= 0.25, {"format": audio_format, "expected": PHRASE, "actual": transcript, "word_error_rate": error_rate}
            transcripts[audio_format] = transcript
            rates[audio_format] = error_rate
        stt_seconds = time.monotonic() - stt_start
        control_result = None
        if mode == "file":
            spec = importlib.util.spec_from_file_location("synthetic_control_speech", "/usr/local/bin/bumpabestie-hermes-control.py")
            control = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = control
            spec.loader.exec_module(control)
            assert control._local_media_ready(), "installed control helper reports local media unavailable"
            probe = control._speech_decoder_compatible
            assert probe.cache_info().misses == 1
            assert control._local_media_ready()
            assert probe.cache_info().hits >= 1
            try:
                probe.cache_clear()
                with patch("faster_whisper.audio.decode_audio", side_effect=TypeError("synthetic incompatible decoder")):
                    assert not control._local_media_ready(), "readiness accepted an incompatible actual decoder"
            finally:
                probe.cache_clear()
            assert control._local_media_ready(), "readiness did not recover after the synthetic fault was removed"
            ogg_bytes = control._synthesize_local(PHRASE)
            actual_control_ogg = Path(directory) / "actual-control-reply.ogg"
            actual_control_ogg.write_bytes(ogg_bytes)
            control_transcript = control._transcribe_local(actual_control_ogg)
            assert distance(words(PHRASE), words(control_transcript["text"])) / len(words(PHRASE)) <= 0.25, control_transcript
            control_result = {"passed": True, "ogg_opus_bytes": len(ogg_bytes), "transcription": control_transcript, "actual_control_source_sha256": hashlib.sha256(Path(spec.origin).read_bytes()).hexdigest(), "health_actual_decoder_abi_probe": "passed", "health_probe_cached": True, "health_rejects_incompatible_decoder": True}
        result = {
            "passed": True, "phrase": PHRASE, "transcripts": transcripts,
            "normalized_word_error_rates": rates, "audio": metadata,
            "wav_sha256": hashlib.sha256(wav_path.read_bytes()).hexdigest(),
            "versions": {name: importlib.metadata.version(name) for name in ["piper-tts", "faster-whisper", "ctranslate2", "av"]},
            "decoder_sha256": hashlib.sha256(importlib.metadata.distribution("faster-whisper").locate_file("faster_whisper/audio.py").read_bytes()).hexdigest(),
            "models": {"piper": str(PIPER), "whisper": str(WHISPER), "whisper_local_files_only": True},
            "input_mode": mode,
            "native_file_decoder_verified": mode == "file",
            "production_control_helpers": control_result,
            "local_emulation_seconds": {"piper_load_and_synthesis": round(tts_seconds, 3), "whisper_load_and_transcription": round(stt_seconds, 3), "pipeline_total": round(time.monotonic() - started, 3)},
            "limits": "Local Mac amd64 emulation smoke test; NOT droplet capacity or real-time latency evidence.",
            "cloud_or_provider_calls": False,
        }
        print("SPEECH_RESULT=" + json.dumps(result, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
