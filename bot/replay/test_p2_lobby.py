"""Network-level REST + two-WebSocket lifecycle checks (aiohttp + stdlib only)."""
from __future__ import annotations

import os
import asyncio
import unittest
from unittest.mock import patch

from aiohttp import ClientSession, WSServerHandshakeError, web

from bot.replay import p2_lobby


class LobbyNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.env = patch.dict(os.environ, {"VALO_LOBBY": "1", "HOST": "127.0.0.1", "PORT": "8919"})
        self.env.start()
        self.app = web.Application()
        p2_lobby.mount(self.app)
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await self.site.start()
        self.base = f"http://127.0.0.1:{self.site._server.sockets[0].getsockname()[1]}"
        self.http = ClientSession()

    async def asyncTearDown(self):
        await self.http.close()
        await self.runner.cleanup()
        self.env.stop()

    async def create(self):
        async with self.http.post(self.base + "/api/lobby", json={"replayId": "demo", "name": "Host"}) as response:
            self.assertEqual(response.status, 201)
            return await response.json()

    async def join(self, code, name):
        async with self.http.post(self.base + f"/api/lobby/{code}/join", json={"name": name}) as response:
            self.assertEqual(response.status, 200)
            return await response.json()

    async def event(self, ws, kind):
        while True:
            message = await ws.receive_json(timeout=3)
            if message.get("type") == kind:
                return message

    async def wait_for_grace(self, member):
        for _ in range(100):
            if member.grace_task is not None:
                return member.grace_task
            await asyncio.sleep(0.01)
        self.fail("socket teardown did not arm a grace timer")

    async def test_two_clients_sync_presence_and_bad_tokens(self):
        owner = await self.create()
        code = owner["code"]
        self.assertIn("127.0.0.1:8919", owner["urls"]["guest"])
        with patch.dict(os.environ, {"HOST": "192.168.1.23", "PORT": "8919"}):
            self.assertTrue(p2_lobby._urls(self.app["p2_lobbies"][code])["guest"].startswith("http://192.168.1.23:8919/"))
        guest = await self.join(code, "Guest")
        host_log, guest_log = [], []
        async with self.http.get(self.base + f"/api/lobby/{code}") as response:
            self.assertEqual(response.status, 200)
            state = await response.json()
            self.assertEqual(state["replayId"], "demo")
            self.assertEqual(len(state["members"]), 2)
        with self.assertRaises(WSServerHandshakeError) as denied:
            await self.http.ws_connect(self.base.replace("http", "ws") + f"/ws/lobby/{code}?token=bad")
        self.assertEqual(denied.exception.status, 403)
        host_ws = await self.http.ws_connect(self.base.replace("http", "ws") + f"/ws/lobby/{code}?token={owner['token']}")
        guest_ws = await self.http.ws_connect(self.base.replace("http", "ws") + f"/ws/lobby/{code}?token={guest['token']}")
        try:
            host_log.append(await self.event(host_ws, "state"))
            guest_log.append(await self.event(guest_ws, "state"))
            host_log.append(await self.event(host_ws, "presence"))
            guest_log.append(await self.event(guest_ws, "presence"))
            host_log.append(await self.event(host_ws, "presence"))
            self.assertEqual(host_log[-1]["action"], "join")
            self.assertEqual(host_log[-1]["name"], "Guest")
            await guest_ws.send_json({"type": "state", "round": 99, "tMs": 99, "speed": 1, "paused": False})
            guest_log.append(await self.event(guest_ws, "error"))
            self.assertEqual(guest_log[-1]["error"], "host only")
            await host_ws.send_json({"type": "state", "round": 2, "tMs": 12340, "speed": 2, "paused": False})
            host_log.append(await self.event(host_ws, "state"))
            guest_log.append(await self.event(guest_ws, "state"))
            for transcript in (host_log, guest_log):
                self.assertEqual(transcript[-1]["round"], 2)
                self.assertEqual(transcript[-1]["tMs"], 12340)
                self.assertEqual(transcript[-1]["speed"], 2)
                self.assertEqual(transcript[-1]["by"], "Host")
            async with self.http.get(self.base + f"/api/lobby/{code}") as response:
                self.assertEqual((await response.json())["state"]["round"], 2)
            async with self.http.post(self.base + f"/api/lobby/{code}/leave", headers={"Authorization": "Bearer bad"}) as response:
                self.assertEqual(response.status, 403)
            async with self.http.post(self.base + f"/api/lobby/{code}/leave", headers={"Authorization": "Bearer " + guest["token"]}) as response:
                self.assertEqual(response.status, 200)
            host_log.append(await self.event(host_ws, "presence"))
            self.assertEqual(host_log[-1]["action"], "leave")
            with self.assertRaises(WSServerHandshakeError) as revoked:
                await self.http.ws_connect(self.base.replace("http", "ws") + f"/ws/lobby/{code}?token={guest['token']}")
            self.assertEqual(revoked.exception.status, 403)
            print("HOST TRANSCRIPT", host_log)
            print("GUEST TRANSCRIPT", guest_log)
        finally:
            await guest_ws.close()
            await host_ws.close()

    async def test_timer_expires_open_websocket(self):
        with patch.object(p2_lobby, "TTL_SECONDS", 0.05):
            owner = await self.create()
            code = owner["code"]
            ws = await self.http.ws_connect(self.base.replace("http", "ws") + f"/ws/lobby/{code}?token={owner['token']}")
            await asyncio.sleep(0.12)
            message = await self.event(ws, "closed")
            self.assertEqual(message["reason"], "expired")
            await ws.receive(timeout=1)
            self.assertTrue(ws.closed)
            async with self.http.get(self.base + f"/api/lobby/{code}") as response:
                self.assertEqual(response.status, 404)
            await ws.close()

    async def test_guest_rest_leave_broadcasts_once(self):
        owner = await self.create()
        guest = await self.join(owner["code"], "Guest")
        url = self.base.replace("http", "ws") + f"/ws/lobby/{owner['code']}?token="
        host_ws = await self.http.ws_connect(url + owner["token"])
        guest_ws = await self.http.ws_connect(url + guest["token"])
        try:
            await self.event(host_ws, "state")
            await self.event(host_ws, "presence")  # host joined
            await self.event(host_ws, "presence")  # guest joined
            await self.event(guest_ws, "state")
            await self.event(guest_ws, "presence")
            async with self.http.post(self.base + f"/api/lobby/{owner['code']}/leave",
                                      headers={"Authorization": "Bearer " + guest["token"]}) as response:
                self.assertEqual(response.status, 200)
            leave = await self.event(host_ws, "presence")
            self.assertEqual((leave["action"], leave["name"]), ("leave", "Guest"))
            self.assertEqual(len(leave["members"]), 1)
            await guest_ws.receive(timeout=1)  # wait for server-side close/finally
            with self.assertRaises(asyncio.TimeoutError):
                await host_ws.receive_json(timeout=0.1)
        finally:
            await guest_ws.close()
            await host_ws.close()

    async def test_rest_leave_during_grace_syncs_roster_without_second_leave(self):
        owner = await self.create()
        guest = await self.join(owner["code"], "Guest")
        url = self.base.replace("http", "ws") + f"/ws/lobby/{owner['code']}?token="
        host_ws = await self.http.ws_connect(url + owner["token"])
        guest_ws = await self.http.ws_connect(url + guest["token"])
        try:
            await self.event(host_ws, "state")
            await self.event(host_ws, "presence")  # host joined
            await self.event(host_ws, "presence")  # guest joined
            await guest_ws.close()
            departed = await self.event(host_ws, "presence")
            self.assertEqual((departed["action"], departed["name"]), ("leave", "Guest"))
            async with self.http.post(self.base + f"/api/lobby/{owner['code']}/leave",
                                      headers={"Authorization": "Bearer " + guest["token"]}) as response:
                self.assertEqual(response.status, 200)
            roster = await self.event(host_ws, "presence")
            self.assertEqual((roster["action"], roster["name"]), ("roster", "Guest"))
            self.assertEqual([item["name"] for item in roster["members"]], ["Host"])
            with self.assertRaises(asyncio.TimeoutError):
                await host_ws.receive_json(timeout=0.1)
        finally:
            await guest_ws.close()
            await host_ws.close()

    async def test_unconnected_guests_expire_and_free_capacity(self):
        with patch.object(p2_lobby, "JOIN_GRACE_SECONDS", 0.5):
            owner = await self.create()
            code = owner["code"]
            guests = [await self.join(code, f"Guest {n}") for n in range(7)]
            async with self.http.post(self.base + f"/api/lobby/{code}/join", json={"name": "Extra"}) as response:
                self.assertEqual(response.status, 409)
            await asyncio.sleep(0.7)
            async with self.http.get(self.base + f"/api/lobby/{code}") as response:
                self.assertEqual(len((await response.json())["members"]), 1)
            with self.assertRaises(WSServerHandshakeError) as revoked:
                await self.http.ws_connect(self.base.replace("http", "ws") +
                                           f"/ws/lobby/{code}?token={guests[0]['token']}")
            self.assertEqual(revoked.exception.status, 403)
            await self.join(code, "Replacement")

    async def test_socket_drop_rearms_grace_without_duplicate_leave(self):
        with (patch.object(p2_lobby, "JOIN_GRACE_SECONDS", 0.3),
              patch.object(p2_lobby, "MAX_MEMBERS", 2)):
            owner = await self.create()
            code = owner["code"]
            guest = await self.join(code, "Guest")
            lobby = self.app["p2_lobbies"][code]
            member = lobby.members[guest["token"]]
            url = self.base.replace("http", "ws") + f"/ws/lobby/{code}?token="
            host_ws = await self.http.ws_connect(url + owner["token"])
            guest_ws = await self.http.ws_connect(url + guest["token"])
            try:
                await self.event(host_ws, "state")
                await self.event(host_ws, "presence")  # host joined
                await self.event(host_ws, "presence")  # guest joined
                await guest_ws.close()  # no REST leave: token can still reconnect
                disconnected = await self.event(host_ws, "presence")
                self.assertEqual(disconnected["action"], "leave")
                self.assertEqual([item["name"] for item in disconnected["members"]], ["Host", "Guest"])
                self.assertFalse(disconnected["members"][1]["connected"])
                timer = await self.wait_for_grace(member)
                self.assertIsNotNone(member.grace_deadline)
                async with self.http.post(self.base + f"/api/lobby/{code}/join",
                                          json={"name": "Extra"}) as response:
                    self.assertEqual(response.status, 409)
                await asyncio.wait_for(timer, 2)
                evicted = await self.event(host_ws, "presence")
                self.assertEqual((evicted["action"], evicted["name"]), ("roster", "Guest"))
                self.assertEqual([item["name"] for item in evicted["members"]], ["Host"])
                self.assertNotIn(guest["token"], lobby.members)
                self.assertIsNone(member.grace_task)
                self.assertIsNone(member.grace_deadline)
                with self.assertRaises(WSServerHandshakeError) as revoked:
                    await self.http.ws_connect(url + guest["token"])
                self.assertEqual(revoked.exception.status, 403)
                await self.join(code, "Replacement")
                with self.assertRaises(asyncio.TimeoutError):
                    await host_ws.receive_json(timeout=0.1)  # no second leave after roster sync
            finally:
                await guest_ws.close()
                await host_ws.close()

    async def test_host_socket_drop_keeps_rest_leave_and_reconnect(self):
        with patch.object(p2_lobby, "JOIN_GRACE_SECONDS", 0.2):
            owner = await self.create()
            code = owner["code"]
            lobby = self.app["p2_lobbies"][code]
            member = lobby.members[owner["token"]]
            url = self.base.replace("http", "ws") + f"/ws/lobby/{code}?token={owner['token']}"
            ws = await self.http.ws_connect(url)
            try:
                await self.event(ws, "state")
                await ws.close()  # socket drop, not REST leave
                for _ in range(100):
                    if member.ws is None:
                        break
                    await asyncio.sleep(0.01)
                self.assertIsNone(member.ws, "host socket did not finish closing")
                self.assertIsNone(member.grace_task)
                await asyncio.sleep(0.4)  # past the guest grace period
                self.assertIs(lobby.members.get(owner["token"]), member)
                async with self.http.get(self.base + f"/api/lobby/{code}") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual((await response.json())["members"],
                                     [{"name": "Host", "host": True, "connected": False}])
                reconnected = await self.http.ws_connect(url)
                try:
                    await self.event(reconnected, "state")
                    async with self.http.post(self.base + f"/api/lobby/{code}/leave",
                                              headers={"Authorization": "Bearer " + owner["token"]}) as response:
                        self.assertEqual(response.status, 200)
                        self.assertEqual(await response.json(), {"left": True})
                    async with self.http.get(self.base + f"/api/lobby/{code}") as response:
                        self.assertEqual(response.status, 404)
                finally:
                    await reconnected.close()
            finally:
                await ws.close()

    async def test_flapping_connections_keep_one_grace_timer(self):
        with patch.object(p2_lobby, "JOIN_GRACE_SECONDS", 0.35):
            owner = await self.create()
            guest = await self.join(owner["code"], "Guest")
            lobby = self.app["p2_lobbies"][owner["code"]]
            member = lobby.members[guest["token"]]
            url = self.base.replace("http", "ws") + f"/ws/lobby/{owner['code']}?token={guest['token']}"
            initial = member.grace_task
            ws = await self.http.ws_connect(url)
            try:
                await self.event(ws, "state")
                self.assertTrue(initial.cancelled() or initial.done())
                self.assertIsNone(member.grace_task)
                await ws.close()
                first = await self.wait_for_grace(member)
                await asyncio.sleep(0.15)
                ws = await self.http.ws_connect(url)
                await self.event(ws, "state")
                self.assertTrue(first.cancelled() or first.done())
                await ws.close()
                second = await self.wait_for_grace(member)
                self.assertIsNot(first, second)
                await asyncio.sleep(0.15)
                p2_lobby._arm_grace(self.app, lobby, guest["token"], member)
                self.assertIs(member.grace_task, second)  # refresh, no duplicate task
                self.assertIn(guest["token"], lobby.members)
                await asyncio.wait_for(second, 2)
                self.assertNotIn(guest["token"], lobby.members)
                self.assertIsNone(member.grace_task)
                self.assertIsNone(member.grace_deadline)
                await asyncio.sleep(0)  # timer-set done callback has run
                self.assertNotIn(first, self.app["p2_lobby_timers"])
                self.assertNotIn(second, self.app["p2_lobby_timers"])
            finally:
                await ws.close()

    async def test_unconnected_grace_eviction_broadcasts_presence(self):
        with patch.object(p2_lobby, "JOIN_GRACE_SECONDS", 0.25):
            owner = await self.create()
            url = self.base.replace("http", "ws") + f"/ws/lobby/{owner['code']}?token={owner['token']}"
            host_ws = await self.http.ws_connect(url)
            try:
                await self.event(host_ws, "state")
                await self.event(host_ws, "presence")
                guest = await self.join(owner["code"], "Never connected")
                member = self.app["p2_lobbies"][owner["code"]].members[guest["token"]]
                await asyncio.wait_for(member.grace_task, 2)
                evicted = await self.event(host_ws, "presence")
                self.assertEqual((evicted["action"], evicted["name"]), ("leave", "Never connected"))
                self.assertEqual([item["name"] for item in evicted["members"]], ["Host"])
                self.assertIsNone(member.grace_task)
                self.assertIsNone(member.grace_deadline)
            finally:
                await host_ws.close()

    async def test_grace_eviction_emits_one_leave_per_guest_to_each_peer(self):
        with patch.object(p2_lobby, "JOIN_GRACE_SECONDS", 0.35):
            owner = await self.create()
            code = owner["code"]
            observer = await self.join(code, "Observer")
            url = self.base.replace("http", "ws") + f"/ws/lobby/{code}?token="
            host_ws = await self.http.ws_connect(url + owner["token"])
            observer_ws = await self.http.ws_connect(url + observer["token"])
            transient_ws = None
            try:
                for ws, joins in ((host_ws, ("Host", "Observer")),
                                  (observer_ws, ("Observer",))):
                    await self.event(ws, "state")
                    for name in joins:
                        joined = await self.event(ws, "presence")
                        self.assertEqual((joined["action"], joined["name"]), ("join", name))

                transient = await self.join(code, "Dropped")
                transient_member = self.app["p2_lobbies"][code].members[transient["token"]]
                transient_ws = await self.http.ws_connect(url + transient["token"])
                await self.event(transient_ws, "state")
                for ws in (host_ws, observer_ws):
                    joined = await self.event(ws, "presence")
                    self.assertEqual((joined["action"], joined["name"]), ("join", "Dropped"))
                await transient_ws.close()
                peer_events = {}
                for ws in (host_ws, observer_ws):
                    departed = await self.event(ws, "presence")
                    self.assertEqual((departed["action"], departed["name"]), ("leave", "Dropped"))
                    peer_events[ws] = [departed]
                dropped_timer = await self.wait_for_grace(transient_member)
                await asyncio.wait_for(dropped_timer, 2)
                for ws in (host_ws, observer_ws):
                    roster = await self.event(ws, "presence")
                    peer_events[ws].append(roster)
                    self.assertEqual((roster["action"], roster["name"]), ("roster", "Dropped"))
                    self.assertEqual([item["name"] for item in roster["members"]],
                                     ["Host", "Observer"])

                never = await self.join(code, "Never connected")
                never_member = self.app["p2_lobbies"][code].members[never["token"]]
                await asyncio.wait_for(never_member.grace_task, 2)
                for ws in (host_ws, observer_ws):
                    departed = await self.event(ws, "presence")
                    peer_events[ws].append(departed)
                    self.assertEqual((departed["action"], departed["name"]),
                                     ("leave", "Never connected"))
                    self.assertEqual([(event["action"], event["name"])
                                      for event in peer_events[ws] if event["action"] == "leave"],
                                     [("leave", "Dropped"), ("leave", "Never connected")])
                    self.assertEqual([item["name"] for item in peer_events[ws][-1]["members"]],
                                     ["Host", "Observer"])
                    with self.assertRaises(asyncio.TimeoutError):
                        await ws.receive_json(timeout=0.1)
            finally:
                if transient_ws is not None:
                    await transient_ws.close()
                await observer_ws.close()
                await host_ws.close()

    async def test_handshake_at_grace_deadline_is_not_evicted(self):
        with patch.object(p2_lobby, "JOIN_GRACE_SECONDS", 0.5):
            owner = await self.create()
            guest = await self.join(owner["code"], "Guest")
            lobby = self.app["p2_lobbies"][owner["code"]]
            started, release = asyncio.Event(), asyncio.Event()
            prepare = web.WebSocketResponse.prepare

            async def delayed_prepare(ws, request):
                started.set()
                await release.wait()
                return await prepare(ws, request)

            with patch.object(web.WebSocketResponse, "prepare", delayed_prepare):
                connection = asyncio.create_task(self.http.ws_connect(
                    self.base.replace("http", "ws") +
                    f"/ws/lobby/{owner['code']}?token={guest['token']}"))
                try:
                    await asyncio.wait_for(started.wait(), 1)
                    await asyncio.sleep(1.0)
                    self.assertIn(guest["token"], lobby.members)
                finally:
                    release.set()
                ws = await asyncio.wait_for(connection, 1)
            try:
                self.assertEqual((await self.event(ws, "state"))["type"], "state")
                self.assertIs(lobby.members[guest["token"]].ws.closed, False)
            finally:
                await ws.close()

    async def test_lazy_expiry_tracks_websocket_close(self):
        owner = await self.create()
        lobby = self.app["p2_lobbies"][owner["code"]]
        ws = await self.http.ws_connect(self.base.replace("http", "ws") +
                                        f"/ws/lobby/{owner['code']}?token={owner['token']}")
        try:
            server_ws = lobby.members[owner["token"]].ws
            lobby.created -= p2_lobby.TTL_SECONDS + 1
            async with self.http.get(self.base + f"/api/lobby/{owner['code']}") as response:
                self.assertEqual(response.status, 404)
            close_task = lobby.members[owner["token"]].close_task
            self.assertIsNotNone(close_task)
            await asyncio.wait_for(close_task, 1)
            self.assertTrue(close_task.done())
            self.assertTrue(server_ws.closed)
        finally:
            await ws.close()

    async def test_disabled_capacity_expiry_and_host_close(self):
        owner = await self.create()
        code = owner["code"]
        for n in range(7):
            await self.join(code, f"Guest {n}")
        async with self.http.post(self.base + f"/api/lobby/{code}/join", json={"name": "Extra"}) as response:
            self.assertEqual(response.status, 409)
        async with self.http.post(self.base + f"/api/lobby/{code}/leave", headers={"Authorization": "Bearer " + owner["token"]}) as response:
            self.assertEqual(response.status, 200)
        async with self.http.get(self.base + f"/api/lobby/{code}") as response:
            self.assertEqual(response.status, 404)
        another = await self.create()
        self.app["p2_lobbies"][another["code"]].created -= p2_lobby.TTL_SECONDS + 1
        async with self.http.get(self.base + f"/api/lobby/{another['code']}") as response:
            self.assertEqual(response.status, 404)
        with patch.dict(os.environ, {"VALO_LOBBY": "0"}):
            async with self.http.post(self.base + "/api/lobby", json={"replayId": "demo"}) as response:
                self.assertEqual(response.status, 404)
            async with self.http.get(self.base + f"/api/lobby/{code}") as response:
                self.assertEqual(response.status, 404)
            with self.assertRaises(WSServerHandshakeError) as denied:
                await self.http.ws_connect(self.base.replace("http", "ws") + f"/ws/lobby/{code}?token=x")
            self.assertEqual(denied.exception.status, 404)


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--serve"]:
        from bot.web.app import create_app
        app = create_app()
        if "p2_lobbies" not in app:
            p2_lobby.mount(app)  # Until the p2.py owner registers this optional module.
        web.run_app(app, host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "8787")))
    else:
        unittest.main()
