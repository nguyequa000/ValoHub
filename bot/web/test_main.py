"""LAN binding and local-only control surface tests."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from bot.replay import api, p2_lobby
from bot.web import __main__ as entrypoint
from bot.web import app as web_app


class AdvertiseHostTests(unittest.TestCase):
    def test_wildcard_uses_non_public_lan_addresses(self):
        for address, expected in (
            ("100.64.1.7", "100.64.1.7"),
            ("192.168.1.8", "192.168.1.8"),
            ("8.8.8.8", "127.0.0.1"),
            ("127.0.0.1", "127.0.0.1"),
        ):
            with self.subTest(address=address), patch.dict(os.environ, {"HOST": "0.0.0.0"}), patch(
                "bot.replay.p2_lobby.socket.socket"
            ) as socket_mock:
                socket_mock.return_value.__enter__.return_value.getsockname.return_value = (address, 8000)
                self.assertEqual(p2_lobby._advertise_host(), expected)

    def test_explicit_host_is_advertised_without_socket_probe(self):
        with patch.dict(os.environ, {"HOST": "192.168.2.3"}), patch(
            "bot.replay.p2_lobby.socket.socket"
        ) as socket_mock:
            self.assertEqual(p2_lobby._advertise_host(), "192.168.2.3")
            socket_mock.assert_not_called()

    def test_ipv6_wildcard_is_rejected_before_binding(self):
        for host in ("::", "::0", "0:0:0:0:0:0:0:0"):
            with self.subTest(host=host), patch.dict(os.environ, {"HOST": host}), patch.object(
                entrypoint.web, "run_app"
            ) as run_app:
                with self.assertRaisesRegex(ValueError, "HOST=:: is not supported"):
                    entrypoint.main()
                run_app.assert_not_called()

    def test_all_unspecified_addresses_resolve_to_reachable_advertisement(self):
        for host in ("::", "::0", "0:0:0:0:0:0:0:0", "0.0.0.0"):
            with self.subTest(host=host), patch.dict(os.environ, {"HOST": host}), patch(
                "bot.replay.p2_lobby.socket.socket"
            ) as socket_mock:
                socket_mock.return_value.__enter__.return_value.getsockname.return_value = ("192.168.2.3", 8000)
                self.assertEqual(p2_lobby._advertise_host(), "192.168.2.3")

    def test_unspecified_probe_address_is_not_advertised(self):
        with patch.dict(os.environ, {"HOST": "::0"}), patch("bot.replay.p2_lobby.socket.socket") as socket_mock:
            socket_mock.return_value.__enter__.return_value.getsockname.return_value = ("0.0.0.0", 8000)
            self.assertEqual(p2_lobby._advertise_host(), "127.0.0.1")

    def test_explicit_hostnames_and_real_ips_boot_and_advertise(self):
        for host in ("localhost", "example.test", "192.168.2.3", "::1"):
            with self.subTest(host=host), patch.dict(os.environ, {"HOST": host}), patch.object(
                entrypoint, "create_app", return_value=web.Application()
            ), patch.object(entrypoint.web, "run_app") as run_app, patch(
                "bot.replay.p2_lobby.socket.socket"
            ) as socket_mock:
                entrypoint.main()
                self.assertEqual(run_app.call_args.kwargs["host"], host)
                self.assertEqual(p2_lobby._advertise_host(), host)
                socket_mock.assert_not_called()


class RemoteGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.app = web.Application()
        entrypoint._add_remote_guard(self.app, "0.0.0.0")
        self.guard = self.app.middlewares[-1]
        self.handler = AsyncMock(return_value=web.Response(text="ok"))

    async def call(self, remote, method, path, lobby="0"):
        with patch.dict(os.environ, {"VALO_LOBBY": lobby}):
            return await self.guard(SimpleNamespace(remote=remote, method=method, path=path), self.handler)

    async def test_non_loopback_mutations_denied_even_with_lobby_off(self):
        paths = (
            ("POST", "/api/replays/import-path"),
            ("PUT", "/api/replays/upload"),
            ("POST", "/api/watcher/toggle"),
            ("POST", "/api/replays/demo/review"),
            ("POST", "/api/replays/demo/identity/refresh"),
            ("POST", "/api/lobby"),
        )
        for method, path in paths:
            with self.subTest(method=method, path=path):
                with self.assertRaises(web.HTTPForbidden) as caught:
                    await self.call("192.168.1.15", method, path)
                self.assertEqual(caught.exception.status, 403)
        self.handler.assert_not_awaited()

    async def test_loopback_allowed_and_unknown_peer_denied(self):
        for remote in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            with self.subTest(remote=remote):
                response = await self.call(remote, "POST", "/api/replays/import-path")
                self.assertEqual(response.status, 200)
        for remote in (None, "not-an-address"):
            with self.subTest(remote=remote), self.assertRaises(web.HTTPForbidden):
                await self.call(remote, "POST", "/api/replays/import-path")

    async def test_only_enabled_lobby_posts_are_public(self):
        for path in ("/api/lobby", "/api/lobby/ABCDEFGH/join", "/api/lobby/ABCDEFGH/leave"):
            with self.subTest(path=path):
                self.assertEqual((await self.call("192.168.1.15", "POST", path, "1")).status, 200)
                with self.assertRaises(web.HTTPForbidden):
                    await self.call("192.168.1.15", "POST", path, "0")
        for method, path in (("PUT", "/api/lobby"), ("POST", "/api/lobby/ABCDEFGH/delete")):
            with self.subTest(method=method, path=path), self.assertRaises(web.HTTPForbidden):
                await self.call("192.168.1.15", method, path, "1")

    async def test_remote_status_is_denied_but_safe_get_remains(self):
        with self.assertRaises(web.HTTPForbidden):
            await self.call("100.69.1.55", "GET", "/api/watcher/status")
        self.assertEqual((await self.call("127.0.0.1", "GET", "/api/watcher/status")).status, 200)
        self.assertEqual((await self.call("100.69.1.55", "GET", "/api/replays")).status, 200)

    async def test_loopback_bind_skips_guard(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                app = web.Application()
                entrypoint._add_remote_guard(app, host)
                self.assertEqual(len(app.middlewares), 0)


class DemosExposureTests(unittest.IsolatedAsyncioTestCase):
    async def test_demos_paths_only_for_loopback_peers(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            demo = Path(tmp) / "sample.vrf"
            demo.write_bytes(b"synthetic")  # No Riot folders accessed.
            with patch.object(api, "demos_dir", return_value=Path(tmp)):
                for remote in ("100.69.1.93", "::ffff:192.168.1.15", None, "invalid"):
                    with self.subTest(remote=remote):
                        response = await api.api_demos(SimpleNamespace(remote=remote))
                        self.assertEqual(json.loads(response.text), {
                            "demos": [{"name": "sample.vrf", "size": 9}]})
                for remote in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                    with self.subTest(remote=remote):
                        response = await api.api_demos(SimpleNamespace(remote=remote))
                        self.assertEqual(json.loads(response.text), {
                            "demos": [{"name": "sample.vrf", "path": str(demo), "size": 9}]})


class ExportsServingTests(unittest.IsolatedAsyncioTestCase):
    async def test_exports_dir_is_lazy_and_served_when_present(self):
        """A boot must not recreate exports/; reports are served once written."""
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            exports = Path(tmp) / "exports"
            with patch.object(web_app, "EXPORTS_DIR", exports):
                app = web_app.create_app()
                self.assertFalse(exports.exists())
                client = TestClient(TestServer(app))
                await client.start_server()
                try:
                    self.assertEqual((await client.get("/static/exports/heat.png")).status, 404)
                    exports.mkdir()
                    (exports / "heat.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")
                    response = await client.get("/static/exports/heat.png")
                    self.assertEqual(response.status, 200)
                    self.assertEqual(await response.read(), b"\x89PNG\r\n\x1a\nfake")
                    self.assertEqual(
                        (await client.get("/static/exports/%2e%2e/test_main.py")).status, 404)
                finally:
                    await client.close()


if __name__ == "__main__":
    unittest.main()
