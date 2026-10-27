"""Verify a built candidate API without live dependencies or provider calls."""
from __future__ import annotations

import asyncio
import ast
import hashlib
import json
from pathlib import Path
import os
from unittest.mock import patch


async def main() -> None:
    manifest = globals().get("CANARY_MANIFEST") or json.loads(Path("/canary/api-source-hashes.json").read_text())
    checked = 0
    for relative, expected in manifest.items():
        if relative.startswith("apps/api/") and relative != "apps/api/Dockerfile":
            target = Path("/app") / relative.removeprefix("apps/api/")
        elif relative.startswith("scripts/"):
            target = Path("/usr/local/bin") / Path(relative).stem.replace("_", "-")
        else:
            continue
        assert hashlib.sha256(target.read_bytes()).hexdigest() == expected, str(target)
        checked += 1
        if target.suffix == ".py":
            compile(target.read_text(), str(target), "exec")

    from app.core.config import Settings
    import httpx

    # The developer CLI's default output path assumes repository depth, which
    # is intentionally absent from the runtime image. Read its reviewed inert
    # settings and execute only its pure normalizer; import the actual ASGI app.
    tree = ast.parse(Path("/app/app/openapi_contract.py").read_text())
    contract_settings = ast.literal_eval(next(
        node.value for node in tree.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
        and node.target.id == "_CONTRACT_SETTINGS"
    ))
    environment = {key.upper(): str(value).lower() if isinstance(value, bool) else str(value) for key, value in contract_settings.items()}
    settings = Settings.model_validate(contract_settings)
    with patch.dict(os.environ, environment, clear=True), patch.dict(Settings.model_config, {"env_file": None}):
        from app.main import create_app
    app = create_app(settings_config=settings)
    assert settings.hermes_context_mode == "canonical_runs"
    assert settings.whatsapp_call_max_concurrent == 1, "call concurrency must remain conservative until production capacity is measured"
    assert settings.agent_backend == settings.bumpa_backend == settings.whatsapp_backend == "disabled"
    normalizer = next(node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "_without_examples")
    namespace = {}
    exec("from __future__ import annotations\n" + ast.unparse(normalizer), namespace)
    contract = json.dumps(namespace["_without_examples"](app.openapi()), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    expected_contract = globals().get("CANARY_CONTRACT") or Path("/canary/openapi.json").read_text()
    assert contract == expected_contract, "built API OpenAPI differs from frozen checked-in contract"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://synthetic.api") as client:
        live = await client.get("/health/live")
        assert live.status_code == 200 and live.json() == {"status": "ok", "service": "api"}, live.text
        assert live.headers.get("X-Content-Type-Options") == "nosniff"
        assert (await client.get("/openapi.json")).status_code == 404
        assert (await client.get("/docs")).status_code == 404
    print("API_CANARY_RESULT=" + json.dumps({
        "passed": True, "actual_image_source_hashes_checked": checked,
        "openapi_sha256": hashlib.sha256(contract.encode()).hexdigest(),
        "canonical_context_mode_default": "canonical_runs",
        "default_call_max_concurrent": settings.whatsapp_call_max_concurrent,
        "in_memory_asgi_liveness": "passed", "production_schema_routes_hidden": "passed",
        "effective_user_id": os.getuid(), "providers": "disabled", "external_network": "runner requires --network none",
        "limitations": "No database/Redis service, migrations, provider calls, production secrets, or deployment exercised.",
    }, sort_keys=True), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
