"""Join the two immutable verified images on an owned external-network-free bridge."""
from __future__ import annotations

import json
import subprocess

from run import ARTIFACTS, HERE, LABEL, ROOT, cleanup_owned_container, command, inspect_image, sha


NETWORK = "bumpa-assembled-context-canary"
SERVER = "bumpa-assembled-hermes"
CLIENT = "bumpa-assembled-api"


def main():
    api = json.loads((ARTIFACTS / "api-result.json").read_text())
    hermes = json.loads((ARTIFACTS / "result.json").read_text())
    for proof in [api, hermes]:
        assert all(sha(ROOT / path) == value for path,value in proof["source_hashes"].items()), "Image proof is historical; fresh source freeze/build required"
    api_image, hermes_image = api["image"], hermes["release_image"]
    assert inspect_image(api_image, "assembled-api-image-inspect")["Id"] == api_image
    assert inspect_image(hermes_image, "assembled-hermes-image-inspect")["Id"] == hermes_image
    command(["network", "create", "--internal", "--label", LABEL, NETWORK], "assembled-network-create")
    try:
        network_info = json.loads(command(["network", "inspect", NETWORK], "assembled-network-inspect").split("\n", 1)[1])[0]
        assert network_info["Internal"] is True
        for name, image, script, executable, alias, memory in [
            (SERVER, hermes_image, "assembled_hermes_server.py", "/opt/hermes/.venv/bin/python", "assembled-hermes", "2g"),
            (CLIENT, api_image, "assembled_api_client.py", "/app/.venv/bin/python", "assembled-api", "1g"),
        ]:
            ephemeral = ["--tmpfs", "/opt/data:rw,nosuid,nodev,size=67108864"] if name == SERVER else []
            command(["create", "--pull", "never", "--platform", "linux/amd64", "--name", name, "--label", LABEL, "--network", NETWORK, "--network-alias", alias, "--memory", memory, "--cpus", "2", "--pids-limit", "256", "--cap-drop", "ALL", "--security-opt", "no-new-privileges", *ephemeral, "--entrypoint", executable, image, "/tmp/assembled.py"], name + "-create")
            command(["cp", str(HERE / script), name + ":/tmp/assembled.py"], name + "-copy")
        command(["start", SERVER], "assembled-server-start")
        output = command(["start", "--attach", CLIENT], "assembled-client-run", timeout=225)
        state = json.loads(command(["inspect", CLIENT], "assembled-client-inspect").split("\n", 1)[1])[0]
        assert state["State"]["ExitCode"] == 0, state["State"]
        assert not state["HostConfig"].get("PortBindings")
        server_state = json.loads(command(["inspect", SERVER], "assembled-server-inspect").split("\n", 1)[1])[0]
        assert not server_state["HostConfig"].get("PortBindings")
        assert not state["Mounts"]
        assert all(mount["Type"] == "tmpfs" for mount in server_state["Mounts"])
        assert not state["HostConfig"].get("Binds") and not server_state["HostConfig"].get("Binds")
        command(["cp", SERVER + ":/tmp/assembled-server-evidence.json", str(ARTIFACTS / "assembled-server-evidence.json")], "assembled-server-evidence-copy")
        evidence = json.loads((ARTIFACTS / "assembled-server-evidence.json").read_text())
        lines = [line.removeprefix("ASSEMBLED_CLIENT_RESULT=") for line in output.splitlines() if line.startswith("ASSEMBLED_CLIENT_RESULT=")]
        assert len(lines) == 1
        result = json.loads(lines[0])
        assert result["passed"] and len(evidence["admissions"]) == len(evidence["model_requests"]) == 2
        assert result["actual_hermes_client_sha256"] == api["source_hashes"]["apps/api/app/providers/hermes.py"]
        for admission in evidence["admissions"]:
            assert admission["status"] == 202 and admission["context_contract"] == "1"
            assert admission["native_affinity_header"] is None
            assert "session_id" not in admission["payload"]
        assert evidence["admissions"][1]["payload"]["conversation_history"] == []
        for index, request in enumerate(evidence["model_requests"]):
            messages = request["messages"]
            dialogue = [row for row in messages if row["role"] in {"user", "assistant"}]
            assert "ASSEMBLED_SYNTHETIC_INTERNAL_NOTICE" not in str(dialogue)
            assert "ASSEMBLED_SYNTHETIC_INTERNAL_NOTICE" in str([row for row in messages if row["role"] == "system"])
            assert ("ASSEMBLED_CANONICAL_VISIBLE_FACT" in str(dialogue)) == (index == 0)
        result.update(api_image=api_image, hermes_image=hermes_image, internal_network_verified=True, no_host_ports=True, canonical_empty_notice_wire_checks="passed", harness_sha256={name:sha(HERE/name) for name in ["run_assembled.py", "assembled_api_client.py", "assembled_hermes_server.py"]})
        for proof in [api, hermes]:
            assert all(sha(ROOT / path) == value for path,value in proof["source_hashes"].items()), "Source changed during assembled image verification"
        result["source_unchanged_during_assembled_canary"] = True
        (ARTIFACTS / "assembled-result.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
        print(json.dumps({key:value for key,value in result.items() if key != "prepared_payloads"}, indent=2, sort_keys=True))
    finally:
        for name in [CLIENT, SERVER]:
            cleanup_owned_container(name, name)
        found = subprocess.run(["docker", "--context", "desktop-linux", "network", "inspect", NETWORK, "--format", '{{ index .Labels "com.bumpa.canary" }}'], capture_output=True, text=True)
        assert found.returncode == 0 and found.stdout.strip() == LABEL.split("=",1)[1]
        command(["network", "rm", NETWORK], "assembled-network-cleanup")


if __name__ == "__main__":
    main()
