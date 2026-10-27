FROM node:26.12.1-bookworm-slim@sha256:28147155d47dc31abe0a46e0d12b59b2fb3d0a2dcc5dc65fce76a074180b16fe AS node-runtime
FROM ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv-runtime
FROM nousresearch/hermes-agent@sha256:fd822bae0a61bf023450737edbeaeea5fc6460f17cb4ee240a7e67b306ed63fb AS dependencies

USER root
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=uv-runtime /uv /usr/local/bin/uv
COPY --from=uv-runtime /uv /uvx /opt/hermes/tools/uv-0.13.5-linux-x64/
# The immutable upstream release currently contains fixable high/critical OS,
# Python, npm and uv findings. Apply the security overlay in the derived image;
# publication still consumes and records the exact resulting image digest.
# Resolve the security overlay independently of upstream's exact vulnerable pins.
# Prune the upstream web/TUI
# workspaces: this image runs the authenticated Python profile gateway and the
# agent-browser runtime, not the dormant Hermes dashboard or its build tools.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
RUN uv --no-config pip install --python /opt/hermes/.venv/bin/python --no-cache --upgrade \
        --exclude-newer 2026-10-22T21:15:00Z \
        aiohttp==3.14.5 \
        certifi==2026.10.19 \
        cryptography==50.0.2 \
        oauthlib==4.0.0 \
        opentelemetry-api==1.45.1 \
        opentelemetry-sdk==1.45.1 \
        opentelemetry-exporter-otlp-proto-http==1.45.1 \
        pillow==13.0.0 \
    && uv --no-config pip install --python /opt/hermes/.venv/bin/python --no-cache \
        --exclude-newer 2026-10-22T20:40:13Z \
        faster-whisper==1.2.1 \
        numpy==2.5.4 \
        piper-tts==1.8.0 \
    && /opt/hermes/tools/python-3.14.9+20261021-linux-x64/bin/python -c 'import importlib.util, subprocess, sys; subprocess.run([sys.executable, "-m", "pip", "uninstall", "--yes", "pip"], check=True) if importlib.util.find_spec("pip") else None' \
    && rm -rf /opt/hermes/tools/python-3.14.9+20261021-linux-x64/lib/python3.14/ensurepip

FROM dependencies AS models
RUN install -d -m 0755 /opt/hermes/models/faster-whisper-base \
    && curl --fail --silent --show-error --location --retry 5 --retry-all-errors \
        --retry-delay 2 --retry-max-time 180 \
        'https://huggingface.co/Systran/faster-whisper-base/resolve/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66/config.json?download=true' \
        --output /opt/hermes/models/faster-whisper-base/config.json \
    && curl --fail --silent --show-error --location --retry 5 --retry-all-errors \
        --retry-delay 2 --retry-max-time 300 \
        'https://huggingface.co/Systran/faster-whisper-base/resolve/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66/model.bin?download=true' \
        --output /opt/hermes/models/faster-whisper-base/model.bin \
    && curl --fail --silent --show-error --location --retry 5 --retry-all-errors \
        --retry-delay 2 --retry-max-time 180 \
        'https://huggingface.co/Systran/faster-whisper-base/resolve/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66/tokenizer.json?download=true' \
        --output /opt/hermes/models/faster-whisper-base/tokenizer.json \
    && curl --fail --silent --show-error --location --retry 5 --retry-all-errors \
        --retry-delay 2 --retry-max-time 180 \
        'https://huggingface.co/Systran/faster-whisper-base/resolve/ebe41f70d5b6dfa9166e2c581c45c9c0cfc57b66/vocabulary.txt?download=true' \
        --output /opt/hermes/models/faster-whisper-base/vocabulary.txt \
    && install -d -m 0755 /opt/hermes/models/piper \
    && curl --fail --silent --show-error --location --retry 5 --retry-all-errors \
        --retry-delay 2 --retry-max-time 180 \
        'https://huggingface.co/rhasspy/piper-voices/resolve/0d907f158acc877ddeebcbf827659ee13bea8bcd/en/en_GB/alba/medium/en_GB-alba-medium.onnx?download=true' \
        --output /opt/hermes/models/piper/en_GB-alba-medium.onnx \
    && curl --fail --silent --show-error --location --retry 5 --retry-all-errors \
        --retry-delay 2 --retry-max-time 180 \
        'https://huggingface.co/rhasspy/piper-voices/resolve/0d907f158acc877ddeebcbf827659ee13bea8bcd/en/en_GB/alba/medium/en_GB-alba-medium.onnx.json?download=true' \
        --output /opt/hermes/models/piper/en_GB-alba-medium.onnx.json \
    && printf '%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n%s  %s\n' \
        '56a6d8110d311f19c8f0471e562832c7527f146b567275bfca59fcf7c184da9a' \
        '/opt/hermes/models/faster-whisper-base/config.json' \
        'd01c3014881c9c6f3133c182f3d2887eb6ca1c789a7538c5c007196857a0a6a9' \
        '/opt/hermes/models/faster-whisper-base/model.bin' \
        'fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab' \
        '/opt/hermes/models/faster-whisper-base/tokenizer.json' \
        '34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913' \
        '/opt/hermes/models/faster-whisper-base/vocabulary.txt' \
        '401369c4a81d09fdd86c32c5c864440811dbdcc66466cde2d64f7133a66ad03b' \
        '/opt/hermes/models/piper/en_GB-alba-medium.onnx' \
        'aa965a2f02ecced632c2694e1fc72bbff6d65f265fab567ca945918c73dd89f4' \
        '/opt/hermes/models/piper/en_GB-alba-medium.onnx.json' \
        | sha256sum --check --strict \
    && /opt/hermes/.venv/bin/python -c 'import importlib.metadata as m; expected={"aiohttp":"3.14.5","certifi":"2026.10.19","cryptography":"50.0.2","oauthlib":"4.0.0","opentelemetry-api":"1.45.1","pillow":"13.0.0"}; actual={name:m.version(name) for name in expected}; assert actual == expected, actual' \
    && /opt/hermes/.venv/bin/python -c 'from aiohttp import web; from gateway.platforms.api_server import check_api_server_requirements; assert check_api_server_requirements()' \
    && /opt/hermes/.venv/bin/python -c 'import faster_whisper, piper; from pathlib import Path; assert Path("/opt/hermes/models/faster-whisper-base/model.bin").is_file(); assert Path("/opt/hermes/models/piper/en_GB-alba-medium.onnx").is_file()'

FROM dependencies AS brotli-builder
ADD --checksum=sha256:98d35d11a78dc453aebb7d10c3748f3f73ff78c8267c0dc697b8631f67bf7e8f \
    https://github.com/google/brotli/archive/refs/tags/v1.2.1.tar.gz /tmp/brotli.tar.gz
COPY infra/brotli/build-package.sh /tmp/build-brotli.sh
RUN sh /tmp/build-brotli.sh /tmp/brotli.tar.gz

FROM models AS runtime
RUN uv --no-config pip install --python /opt/hermes/.venv/bin/python --no-cache brotlicffi==1.2.1.0
# Upstream bootstrap metadata says 0.0.0, while its immutable CI stamp records
# 0.22.5. Verify provenance and the DNS-rebinding fix before correcting identity.
COPY infra/hermes/reconcile_package_version.py /tmp/reconcile_package_version.py
RUN PYTHONPATH=/opt/hermes /opt/hermes/.venv/bin/python /tmp/reconcile_package_version.py \
    && rm /tmp/reconcile_package_version.py


COPY --from=brotli-builder /out/libbrotli1_1.2.1-0bumpa1_amd64.deb /tmp/brotli.deb
RUN dpkg -i /tmp/brotli.deb && rm /tmp/brotli.deb \
    && /opt/hermes/.venv/bin/python -c 'import ctypes; assert ctypes.CDLL("libbrotlidec.so.1").BrotliDecoderVersion() == 0x1002001'
RUN /opt/hermes/.venv/bin/python -c 'import sys; assert sys.version_info[:3] == (3, 14, 9)' \
    && /opt/hermes/.venv/bin/python -c 'from aiohttp import web; import faster_whisper, piper; from gateway.platforms.api_server import check_api_server_requirements; assert check_api_server_requirements()'
COPY infra/hermes/browser-runtime.json /tmp/browser-runtime.json
COPY infra/hermes/upgrade_browser.py /tmp/upgrade_browser.py
ADD --checksum=sha256:e6c94490f0b6b337225aeb2d2a5034858516b7017356980ba94f5176d8774a50 \
    https://storage.googleapis.com/chrome-for-testing-public/156.0.8078.47/linux64/chrome-linux64.zip \
    /opt/hermes/tools/fetch-e6c94490f0b6b337225aeb2d2a5034858516b7017356980ba94f5176d8774a50/chrome-linux64.zip
# Install through PM so discovery and future repairs use the patched browser pin.
RUN PYTHONPATH=/opt/hermes /opt/hermes/.venv/bin/python /tmp/upgrade_browser.py \
    && chmod 0644 /opt/hermes/pm/lock.json /opt/hermes/tools/facts.json \
    && rm /tmp/upgrade_browser.py /tmp/browser-runtime.json
COPY infra/hermes/package.json infra/hermes/package-lock.json /opt/hermes/
# The profile gateway needs only the root utilities and browser CLI, not the UI workspaces.
RUN find /opt/hermes -type d -name node_modules -prune -exec rm -rf '{}' + \
    && node /usr/local/lib/node_modules/npm/bin/npm-cli.js ci --prefix /opt/hermes \
        --omit=dev --ignore-scripts --no-audit --no-fund \
    && rm -rf /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx \
    && test -z "$(find /opt/hermes -type f \( \
        -path '*/node_modules/brace-expansion/package.json' -o \
        -path '*/node_modules/postcss/package.json' -o \
        -path '*/node_modules/react-router/package.json' \) -print -quit)" \
    && node --version | grep -Fx 'v26.12.1' \
    && /opt/hermes/node_modules/.bin/agent-browser --help >/dev/null \
    && /opt/hermes/.venv/bin/python -c 'from io import BytesIO; import mcp; from PIL import Image; image=Image.new("RGB", (1, 1)); image.save(BytesIO(), format="PNG")' \
    && /opt/hermes/.venv/bin/hermes --help >/dev/null \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx
COPY --chmod=0555 infra/hermes/entrypoint.sh /usr/local/bin/bumpabestie-hermes-entrypoint
COPY --chmod=0555 infra/hermes/import-profiles.sh /usr/local/bin/bumpabestie-hermes-import
COPY --chmod=0444 infra/hermes/control_server.py /usr/local/bin/bumpabestie-hermes-control.py
COPY --chmod=0444 infra/hermes/apply_context_overlay.py /tmp/apply_context_overlay.py
RUN /opt/hermes/.venv/bin/python /tmp/apply_context_overlay.py /opt/hermes/agent/turn_context.py /opt/hermes/gateway/platforms/api_server_runs.py /opt/hermes/agent/system_prompt.py /opt/hermes/agent/agent_init.py /opt/hermes/gateway/run_bootstrap.py \
    && rm /tmp/apply_context_overlay.py
COPY --chmod=0444 infra/hermes/patch_speech_compat.py /tmp/patch_speech_compat.py
RUN /opt/hermes/.venv/bin/python /tmp/patch_speech_compat.py && rm /tmp/patch_speech_compat.py
LABEL io.bumpabestie.canonical-context-contract="v1"
COPY --chmod=0555 infra/hermes/control-run /etc/s6-overlay/s6-rc.d/bumpabestie-hermes-control/run
COPY --chmod=0444 infra/hermes/control-type /etc/s6-overlay/s6-rc.d/bumpabestie-hermes-control/type
COPY --chmod=0444 infra/hermes/control-user /etc/s6-overlay/s6-rc.d/user/contents.d/bumpabestie-hermes-control

ENTRYPOINT ["/usr/local/bin/bumpabestie-hermes-entrypoint"]

FROM runtime AS gateway-runtime
RUN ln -s /opt/hermes/node_modules/.bin/agent-browser /usr/local/bin/agent-browser
