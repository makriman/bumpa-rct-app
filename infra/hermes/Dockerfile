FROM nousresearch/hermes-agent@sha256:f12c5f859667f229a83b8ab8cc8c8351ae4846fd32963db97eeb4e890f82fbd8 AS runtime

USER root
# The immutable upstream release currently contains fixable high/critical OS,
# Python, npm and uv findings. Apply the security overlay in the derived image;
# publication still consumes and records the exact resulting image digest.
# Python packages are intentionally upgraded without dependency resolution
# because upstream pins vulnerable builds exactly. Prune the upstream web/TUI
# workspaces: this image runs the authenticated Python profile gateway and the
# agent-browser runtime, not the dormant Hermes dashboard or its build tools.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes --no-install-recommends \
    && dpkg --compare-versions \
        "$(dpkg-query --show --showformat='${Version}' libxfont2)" \
        ge '1:2.0.6-1+deb13u1' \
    && rm -rf /var/lib/apt/lists/* \
    && uv pip install --python /opt/hermes/.venv/bin/python --no-cache --no-deps --upgrade \
        cryptography==48.0.1 \
        httplib2==0.32.0 \
        mcp==1.28.1 \
        pillow==12.3.0 \
        pyasn1==0.6.4 \
        python-multipart==0.0.30 \
        starlette==1.3.1 \
        tornado==6.5.6 \
    && uv pip install --python /opt/hermes/.venv/bin/python --no-cache \
        faster-whisper==1.2.1 \
        numpy==2.4.3 \
        piper-tts==1.6.0 \
    && install -d -m 0755 /opt/hermes/models/faster-whisper-base \
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
    && /opt/hermes/.venv/bin/python -c 'import importlib.metadata as m; expected={"cryptography":"48.0.1","httplib2":"0.32.0","mcp":"1.28.1","pillow":"12.3.0","pyasn1":"0.6.4","python-multipart":"0.0.30","starlette":"1.3.1","tornado":"6.5.6"}; assert all(m.version(name) == version for name, version in expected.items())' \
    && /opt/hermes/.venv/bin/python -c 'import faster_whisper, piper; from pathlib import Path; assert Path("/opt/hermes/models/faster-whisper-base/model.bin").is_file(); assert Path("/opt/hermes/models/piper/en_GB-alba-medium.onnx").is_file()' \
    && npm prune --prefix /opt/hermes --omit=dev --workspaces=false \
        --ignore-scripts --no-audit --no-fund \
    && rm -rf \
        /opt/hermes/node_modules/@eslint/config-array/node_modules/brace-expansion \
        /opt/hermes/node_modules/@eslint/eslintrc/node_modules/brace-expansion \
        /opt/hermes/node_modules/brace-expansion \
        /opt/hermes/node_modules/eslint-plugin-react/node_modules/brace-expansion \
        /opt/hermes/node_modules/eslint/node_modules/brace-expansion \
        /opt/hermes/node_modules/js-yaml \
        /usr/local/lib/node_modules/npm \
    && rm -f /usr/local/bin/npm /usr/local/bin/npx \
    && test -z "$(find /opt/hermes -type f \( \
        -path '*/node_modules/brace-expansion/package.json' -o \
        -path '*/node_modules/js-yaml/package.json' -o \
        -path '*/node_modules/postcss/package.json' -o \
        -path '*/node_modules/react-router/package.json' \) -print -quit)" \
    && test -x /opt/hermes/node_modules/.bin/agent-browser \
    && /opt/hermes/node_modules/.bin/agent-browser --help >/dev/null \
    && /opt/hermes/.venv/bin/python -c 'from io import BytesIO; import mcp; from PIL import Image; image=Image.new("RGB", (1, 1)); image.save(BytesIO(), format="PNG")' \
    && /opt/hermes/.venv/bin/hermes --help >/dev/null \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx
COPY --chmod=0555 infra/hermes/entrypoint.sh /usr/local/bin/bumpabestie-hermes-entrypoint
COPY --chmod=0555 infra/hermes/import-profiles.sh /usr/local/bin/bumpabestie-hermes-import
COPY --chmod=0444 infra/hermes/control_server.py /usr/local/bin/bumpabestie-hermes-control.py
COPY --chmod=0555 infra/hermes/control-run /etc/s6-overlay/s6-rc.d/bumpabestie-hermes-control/run
COPY --chmod=0444 infra/hermes/control-type /etc/s6-overlay/s6-rc.d/bumpabestie-hermes-control/type
COPY --chmod=0444 infra/hermes/control-user /etc/s6-overlay/s6-rc.d/user/contents.d/bumpabestie-hermes-control

ENTRYPOINT ["/usr/local/bin/bumpabestie-hermes-entrypoint"]
