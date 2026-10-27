FROM bumpa-hermes-context-release-canary:20261027
USER root
LABEL com.bumpa.canary="hermes-context-20261027"
COPY infra/hermes/patch_speech_compat.py /tmp/patch_speech_compat.py
RUN /opt/hermes/.venv/bin/python /tmp/patch_speech_compat.py && rm /tmp/patch_speech_compat.py
