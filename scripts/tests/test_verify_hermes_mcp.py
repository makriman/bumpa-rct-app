from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "scripts" / "verify_hermes_mcp.py"
SPEC = importlib.util.spec_from_file_location("verify_hermes_mcp", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def private_url(
    *,
    scheme: str = "http",
    host: str = "api",
    port: int = 8000,
    path: str = "/internal/mcp/",
    suffix: str = "",
) -> str:
    return f"{scheme}://{host}:{port}{path}{suffix}"


class VerifyHermesMcpTests(unittest.TestCase):
    def test_legacy_and_v2_transports_authenticate_and_accept_their_stream_shapes(
        self,
    ) -> None:
        class Session:
            def __init__(self, read: object, write: object) -> None:
                assert (read, write) == ("read", "write")

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def initialize(self):
                pass

            async def list_tools(self):
                return SimpleNamespace(
                    tools=[SimpleNamespace(name=name) for name in MODULE.REQUIRED_TOOLS]
                )

        with tempfile.TemporaryDirectory() as temporary:
            profile = Path(temporary)
            (profile / ".env").write_text("API_SERVER_KEY=" + "p" * 48 + "\n")
            (profile / ".env").chmod(0o600)
            for legacy in (True, False):

                @asynccontextmanager
                async def transport(url, **kwargs):
                    assert url == private_url()
                    if legacy:
                        assert kwargs["headers"] == {
                            "Authorization": "Bearer " + "p" * 48
                        }
                    else:
                        assert kwargs["http_client"] == "authenticated-client"
                    yield ("read", "write", None) if legacy else ("read", "write")

                @asynccontextmanager
                async def client(**kwargs):
                    assert kwargs["headers"] == {"Authorization": "Bearer " + "p" * 48}
                    yield "authenticated-client"

                http = SimpleNamespace(
                    **{
                        "streamablehttp_client"
                        if legacy
                        else "streamable_http_client": transport
                    }
                )
                with (
                    self.subTest(legacy=legacy),
                    patch.dict(
                        "sys.modules",
                        {
                            "mcp": SimpleNamespace(ClientSession=Session),
                            "mcp.client": SimpleNamespace(streamable_http=http),
                            "httpx2": SimpleNamespace(AsyncClient=client),
                        },
                    ),
                ):
                    self.assertEqual(
                        asyncio.run(MODULE.verify_profile(profile, private_url())), 19
                    )

    def test_profile_verification_supports_both_mcp_transport_generations(self) -> None:
        for modern in (False, True):
            with self.subTest(modern=modern):
                self._verify_transport_generation(modern)

    def _verify_transport_generation(self, modern: bool) -> None:
        events: list[str] = []
        credential = "synthetic-profile-key-for-transport-tests"
        modules = {
            name: ModuleType(name)
            for name in (
                "mcp",
                "mcp.client",
                "mcp.client.streamable_http",
                "httpx2",
            )
        }

        class HttpClient:
            def __init__(inner, **kwargs: object) -> None:
                self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {credential}"})
                self.assertIs(kwargs["follow_redirects"], False)

            async def __aenter__(inner) -> object:
                events.append("http-open")
                return inner

            async def __aexit__(inner, *_args: object) -> None:
                events.append("http-close")

        @asynccontextmanager
        async def transport(url: str, **kwargs: object):
            self.assertEqual(url, private_url())
            if modern:
                self.assertIsInstance(kwargs["http_client"], HttpClient)
            else:
                self.assertEqual(kwargs["headers"], {"Authorization": f"Bearer {credential}"})
                self.assertEqual(kwargs["sse_read_timeout"], 20)
            events.append("transport-open")
            yield ("read", "write", "session", "modern-extra")
            events.append("transport-close")

        class Session:
            def __init__(inner, read: str, write: str) -> None:
                self.assertEqual((read, write), ("read", "write"))

            async def __aenter__(inner) -> object:
                events.append("session-open")
                return inner

            async def __aexit__(inner, *_args: object) -> None:
                events.append("session-close")

            async def initialize(inner) -> None:
                events.append("initialize")

            async def list_tools(inner) -> object:
                return SimpleNamespace(
                    tools=[SimpleNamespace(name=name) for name in MODULE.REQUIRED_TOOLS]
                )

        modules["mcp"].ClientSession = Session
        modules["mcp"].client = modules["mcp.client"]
        modules["mcp.client"].streamable_http = modules["mcp.client.streamable_http"]
        setattr(
            modules["mcp.client.streamable_http"],
            "streamable_http_client" if modern else "streamablehttp_client",
            transport,
        )
        modules["httpx2"].AsyncClient = HttpClient
        with (
            patch.dict(sys.modules, modules),
            patch.object(MODULE, "load_profile_key", return_value=credential),
        ):
            self.assertEqual(
                asyncio.run(MODULE.verify_profile(Path("synthetic"), private_url())),
                19,
            )
        expected = [
            "transport-open",
            "session-open",
            "initialize",
            "session-close",
            "transport-close",
        ]
        if modern:
            expected = ["http-open", *expected, "http-close"]
        self.assertEqual(events, expected)

    def test_private_mcp_url_is_exact(self) -> None:
        expected = private_url()
        self.assertEqual(MODULE.validate_private_mcp_url(expected), expected)
        for rejected in (
            private_url(scheme="https", host="api.bumpabestie.com", port=443),
            private_url(path="/internal/mcp"),
            private_url(host="postgres"),
            private_url(suffix="?tenant=other"),
            private_url(host="user:secret@api"),
        ):
            with self.subTest(rejected=rejected):
                with self.assertRaises(MODULE.VerificationError):
                    MODULE.validate_private_mcp_url(rejected)

    def test_profile_discovery_and_key_parsing_are_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            profiles = []
            for index in range(2):
                profile = root / f"tenant_store_{index}"
                profile.mkdir()
                environment = profile / ".env"
                environment.write_text(
                    f"API_SERVER_ENABLED=true\nAPI_SERVER_KEY={'a' * 47}{index}\n",
                    encoding="utf-8",
                )
                os.chmod(environment, 0o600)
                profiles.append(profile)
            self.assertEqual(MODULE.discover_profiles(root, 2), profiles)
            self.assertEqual(MODULE.load_profile_key(profiles[0]), "a" * 47 + "0")
            with self.assertRaises(MODULE.VerificationError):
                MODULE.discover_profiles(root, 1)
            os.chmod(profiles[0] / ".env", 0o644)
            with self.assertRaises(MODULE.VerificationError):
                MODULE.load_profile_key(profiles[0])

    def test_required_surface_contains_business_research_and_sandbox_tools(
        self,
    ) -> None:
        self.assertEqual(len(MODULE.REQUIRED_TOOLS), 19)
        self.assertTrue(
            {
                "get_business_overview",
                "calculate_exactly",
                "research_web",
                "sandbox_exec",
                "generate_image",
                "queue_sandbox_file_for_delivery",
                "prepare_connector_action",
            }.issubset(MODULE.REQUIRED_TOOLS)
        )


if __name__ == "__main__":
    unittest.main()
