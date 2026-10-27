FROM nousresearch/hermes-agent@sha256:fd822bae0a61bf023450737edbeaeea5fc6460f17cb4ee240a7e67b306ed63fb
USER root
LABEL com.bumpa.canary="hermes-context-20261027"
COPY infra/hermes/apply_context_overlay.py /canary/apply_context_overlay.py
COPY tests/hermes-canary/canary.py /canary/canary.py
RUN /opt/hermes/.venv/bin/python /canary/apply_context_overlay.py /opt/hermes/agent/turn_context.py /opt/hermes/gateway/platforms/api_server_runs.py /opt/hermes/agent/system_prompt.py /opt/hermes/agent/agent_init.py /opt/hermes/gateway/run_bootstrap.py
ENTRYPOINT ["/opt/hermes/.venv/bin/python", "/canary/canary.py"]
