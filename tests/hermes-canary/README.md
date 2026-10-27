# Local Hermes compatibility canary

This harness uses the exact cached upstream Hermes digest and the repository's
hash-guarded context overlay. It imports and executes the real patched functions,
then starts the real authenticated API adapter and real `AIAgent` conversation
loop. A synthetic OpenAI-compatible endpoint captures model messages and returns
a fixed reply on container loopback. No provider credentials are needed.

After reviewers freeze the source, run from the repository root:

```sh
python3 tests/hermes-canary/run.py --full
```

The local Docker Desktop context is explicit. Canary containers have no external
network, published ports, production mounts, Docker socket, or real credentials.
They have memory, CPU and PID limits, dropped capabilities, and their own labels
and names. Only those disposable containers are removed. Images remain available
for review; this script never prunes or changes any unrelated Docker objects.

The minimal image applies the overlay to the exact upstream pin. `--full` also
builds the unchanged release `infra/hermes/Dockerfile` target `gateway-runtime`
from a small source snapshot and runs the same harness against that image. The
minimal overlay build has RUN networking disabled. The unchanged release build
can fetch its public pinned dependencies if needed, while reusing cached model
and dependency layers. Both runtime tests always have external networking disabled.
The full-image test mounts only the synthetic harness and public overlay source
read-only; application or production configuration is never mounted.

Evidence, exact argv, build/run logs, image identities, and source hashes are
written under ignored `artifacts/`. `result.json` is produced only after all
requested canaries pass and a final check confirms source did not change.

Coverage includes string and multipart user content, stale `api_content`
sidecars, typed internal notices, untrusted retrieved/plugin context, clean
canonical history in fresh runs, empty canonical history without affinity,
unmanaged native-memory disablement, polling to completion, and idempotent replay
without another model call. Both initial admission and replay must advertise
`X-Bumpa-Context-Contract: 1`.

The startup canary creates valid due cron jobs in five synthetic profile stores.
It verifies that the app-managed image starts only a stoppable status heartbeat,
does not resolve or start the native scheduler/delivery housekeeping, and
preserves the complete native evidence file tree byte-for-byte.

This verifies the synthetic Chat Completions route and local gateway boundary.
It does not establish live Claude, audio, provider availability, production
configuration, rollout correctness, or external tool behavior.

The separate offline speech gate runs the actual bundled Piper and Whisper
models through WAV and OGG/Opus, the installed control helpers, and decoder
readiness checks:

```sh
python3 tests/hermes-canary/run_speech.py --output-stem speech-final
```

It uses fixed synthetic English text and no external network. Its timing on this
Mac under amd64 emulation must not be used as a droplet capacity benchmark.
The guarded compatibility patch in `infra/hermes/patch_speech_compat.py` changes
only the removed PyAV keyword in the exact reviewed faster-whisper decoder.

The API build harness is prepared separately. Run only after the coordinator
explicitly freezes the API sources and checked-in contract:

```sh
python3 tests/hermes-canary/run_api.py --source-frozen
```

It builds the unchanged API Dockerfile from a source-only snapshot, verifies
source hashes in the image and the non-root user/contract label, generates the
OpenAPI contract, and exercises ASGI liveness without external dependencies.
