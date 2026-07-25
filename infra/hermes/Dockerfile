FROM nousresearch/hermes-agent@sha256:9c841866021c54c4596849f6135717e8a4d52ba510b7f52c50aef1de1a283973 AS runtime

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
    && /opt/hermes/.venv/bin/python -c 'import importlib.metadata as m; expected={"cryptography":"48.0.1","httplib2":"0.32.0","mcp":"1.28.1","pillow":"12.3.0","pyasn1":"0.6.4","python-multipart":"0.0.30","starlette":"1.3.1","tornado":"6.5.6"}; assert all(m.version(name) == version for name, version in expected.items())' \
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
