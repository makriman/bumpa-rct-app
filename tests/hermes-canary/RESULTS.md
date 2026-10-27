# Current built-image compatibility verification

The reconciled API, Hermes, and assembled local compatibility gates passed.
This is technical image evidence, not deployment or whole-release approval.

| Image | Verified digest |
| --- | --- |
| API | `sha256:4c79559c7783a926bd62df7ca642be8e4b9a35ff336e2f056ad83ea2fef131d1` |
| Hermes | `sha256:f51a19ae5fd7fca93f5727296992e77e0b549566a2853999dd80512e8444681c` |
| Upstream Hermes | `nousresearch/hermes-agent@sha256:fd822bae0a61bf023450737edbeaeea5fc6460f17cb4ee240a7e67b306ed63fb` |

Commands:

```sh
python3 tests/hermes-canary/run.py --full
python3 tests/hermes-canary/run_speech.py --output-stem speech-final
python3 tests/hermes-canary/run_api.py --source-frozen
python3 tests/hermes-canary/run_assembled.py
```

The API gate checked all 134 packaged source/CLI/migration/script files, compiled
their Python, verified its non-root user and v1 label, generated the exact current
OpenAPI contract, exercised ASGI liveness with production schema routes hidden,
and checked canonical context/default call concurrency1. Actual installed versions
include FastAPI0.139.0, Pydantic2.13.4, and SQLAlchemy2.0.51; all installed runtime
distributions are recorded separately.

The Hermes Dockerfile retains the production Brotli and package identity fixes.
Actual image checks verified hermes-agent0.22.5 and brotlicffi1.2.1.0. Its five
source overlays, native memory disablement, heartbeat-only startup, and guarded
speech compatibility fix remain applied. Real gateway tests checked canonical
and empty context, no native affinity, string/multipart notice isolation,
idempotency and `X-Bumpa-Context-Contract: 1`. Five valid due-job stores were
preserved byte-for-byte; no native cron/delivery housekeeping started.

The full image passed WAV and OGG/Opus transcription and installed Piper/Whisper
control helpers with production VAD. Decoder readiness passed real tiny-WAV
decoding, cache reuse, injected incompatibility refusal and recovery.

The assembled test ran the actual built API HermesClient over real HTTP to the
actual built Hermes gateway. Two turns completed: canonical multipart input and
empty history. Both observed contract header1. Actual gateway/model wire evidence
confirmed no native selectors and internal notices only in system context. The
model endpoint was synthetic and loopback-only inside Hermes. The two unchanged
images used an owned internal-only Docker bridge, no host ports, no bind mounts,
bounded resources, and a tmpfs override for Hermes’ inherited data volume.

All current API/Hermes source hashes were checked before and after the assembled
run. Cleanup confirmed zero owned containers/networks; labelled images remain.
No production credentials/data, SSH, deployment, publication, application database
or real provider service was used. Earlier pre-reconciliation proofs remain
separate under `artifacts/historical-pre-production-reconcile/`.

Evidence:

- [Combined exact-source/image proof](artifacts/final-built-image-verification.json)
- [API source and runtime result](artifacts/api-result.json)
- [API installed dependency versions](artifacts/api-installed-versions.json)
- [Assembled result](artifacts/assembled-result.json)
- [Actual gateway/model wire capture](artifacts/assembled-server-evidence.json)
- [Hermes source and runtime result](artifacts/result.json)
- [Speech/control/readiness result](artifacts/speech-final-result.json)
- Exact argv in `artifacts/*build.log`, `*run.log`, and assembled lifecycle logs.

Fixture attribution uses the exact upstream MIT notice; all five Python fixtures
retain their reviewed hashes. Installed speech notices were extracted read-only
with exact paths/hashes for the parent licensing audit.

These synthetic Mac amd64-emulation checks do not establish droplet capacity,
real-time call latency, live Claude/provider availability, application DB/Redis
integration, production secret/profile entrypoint boot, model/voice commercial
rights, deployment success, or completion of other release gates.
