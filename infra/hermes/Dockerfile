FROM nousresearch/hermes-agent@sha256:9c841866021c54c4596849f6135717e8a4d52ba510b7f52c50aef1de1a283973 AS runtime

USER root
# The immutable upstream release currently contains fixable high/critical OS,
# Python, npm and uv findings. Apply the security overlay in the derived image;
# publication still consumes and records the exact resulting image digest. The
# four Python packages are intentionally upgraded without dependency resolution
# because upstream pins the vulnerable cryptography build exactly. The runtime
# contract boots an authenticated profile gateway after this overlay.
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get upgrade --yes --no-install-recommends \
    && rm -rf /var/lib/apt/lists/* \
    && uv pip install --python /opt/hermes/.venv/bin/python --no-cache --no-deps --upgrade \
        cryptography==48.0.1 \
        python-multipart==0.0.30 \
        starlette==1.3.1 \
        tornado==6.5.6 \
    && /opt/hermes/.venv/bin/python -c 'import importlib.metadata as m; expected={"cryptography":"48.0.1","python-multipart":"0.0.30","starlette":"1.3.1","tornado":"6.5.6"}; assert all(m.version(name) == version for name, version in expected.items())' \
    && npm install --global --ignore-scripts --no-audit --no-fund npm@12.0.1 \
    && test "$(npm --version)" = "12.0.1" \
    && rm -f /usr/local/bin/uv /usr/local/bin/uvx
COPY --chmod=0555 infra/hermes/entrypoint.sh /usr/local/bin/bumpabestie-hermes-entrypoint
COPY --chmod=0555 infra/hermes/import-profiles.sh /usr/local/bin/bumpabestie-hermes-import
COPY --chmod=0444 infra/hermes/control_server.py /usr/local/bin/bumpabestie-hermes-control.py
COPY --chmod=0555 infra/hermes/control-run /etc/s6-overlay/s6-rc.d/bumpabestie-hermes-control/run
COPY --chmod=0444 infra/hermes/control-type /etc/s6-overlay/s6-rc.d/bumpabestie-hermes-control/type
COPY --chmod=0444 infra/hermes/control-user /etc/s6-overlay/s6-rc.d/user/contents.d/bumpabestie-hermes-control

ENTRYPOINT ["/usr/local/bin/bumpabestie-hermes-entrypoint"]
