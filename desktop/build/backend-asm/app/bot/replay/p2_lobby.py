"""Opt-in, in-memory watch-together sessions for a trusted LAN.

No account authentication: the random code is an invitation and the random per-member
bearer token is a capability. A non-loopback HOST exposes the entire app, not just the
lobby. The entrypoint guard keeps non-lobby mutations and watcher status
loopback-only; GET /api/demos omits local paths for remote peers. Other read-only
routes and lobby invitations remain public to the trusted LAN. Never bind to an
untrusted network. Sessions expire after two hours (not persisted across restarts).
"""
from __future__ import annotations

import asyncio
import ipaddress
import math
import os
import re
import secrets
import socket
import time
from dataclasses import dataclass, field
from urllib.parse import quote

from aiohttp import WSMsgType, web

TTL_SECONDS = 2 * 60 * 60
MAX_MEMBERS = 8
JOIN_GRACE_SECONDS = 60
CODE_RE = re.compile(r"[A-Z2-9]{8}\Z")


@dataclass
class Member:
    name: str
    host: bool = False
    ws: web.WebSocketResponse | None = None
    grace_task: asyncio.Task | None = None
    grace_deadline: float | None = None
    close_task: asyncio.Task | None = None
    connecting: bool = False
    leave_announced: bool = False


@dataclass
class Lobby:
    code: str
    replay_id: str
    created: float
    host_token: str
    members: dict[str, Member] = field(default_factory=dict)
    expiry_task: asyncio.Task | None = None
    state: dict = field(default_factory=lambda: {
        "round": 1, "tMs": 0, "speed": 1, "paused": True, "by": "Host"})


def _enabled() -> bool:
    return os.getenv("VALO_LOBBY") == "1"


def _not_found() -> web.Response:
    return web.json_response({"error": "lobby not found"}, status=404)


def _sessions(request: web.Request) -> dict[str, Lobby]:
    sessions = request.app["p2_lobbies"]
    now = time.monotonic()
    for code, session in list(sessions.items()):
        if now - session.created >= TTL_SECONDS:
            sessions.pop(code, None)
            if session.expiry_task is not None:
                session.expiry_task.cancel()
            for member in session.members.values():
                _clear_grace(member)
                if member.ws is not None:
                    task = asyncio.create_task(member.ws.close(code=1001, message=b"lobby expired"))
                    member.close_task = task
                    request.app["p2_lobby_closers"].add(task)
                    task.add_done_callback(request.app["p2_lobby_closers"].discard)
    return sessions


def _lobby(request: web.Request) -> Lobby | None:
    code = request.match_info["code"].upper()
    if not CODE_RE.fullmatch(code):
        return None
    return _sessions(request).get(code)


def _name(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    value = raw.strip()
    if not 1 <= len(value) <= 32 or any(ord(c) < 32 for c in value):
        return None
    return value


async def _body(request: web.Request) -> dict | None:
    try:
        data = await request.json()
    except (ValueError, UnicodeError):
        return None
    return data if isinstance(data, dict) else None


def _advertise_host() -> str:
    host = os.getenv("HOST", "127.0.0.1")
    try:
        bind_address = ipaddress.ip_address(host)
    except ValueError:
        return host  # Preserve explicit DNS hostnames.
    if not bind_address.is_unspecified:
        return host
    # UDP connect picks the LAN interface without sending a datagram.
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            address = ipaddress.ip_address(sock.getsockname()[0])
            # Anything but loopback/public is LAN-reachable: keeps private
            # ranges and also accepts RFC 6598 shared space (100.64.0.0/10,
            # used by CGNAT/campus LANs) which is_private() alone rejects.
            if not (address.is_unspecified or address.is_loopback or address.is_global):
                return str(address)
    except OSError:
        pass
    return "127.0.0.1"


def _urls(session: Lobby) -> dict[str, str]:
    host = _advertise_host()
    try:
        port = int(os.getenv("PORT", "8787"))
    except ValueError:
        port = 8787
    origin = f"http://{host if ':' not in host else '[' + host + ']'}:{port}"
    if session.replay_id == "demo":
        path = "/static/replays_app/view.html?demo=1&lobby=" + session.code
    else:
        path = f"/replays/{quote(session.replay_id, safe='')}/view?lobby={session.code}"
    return {"guest": origin + path}


def _public(session: Lobby) -> dict:
    return {"code": session.code, "replayId": session.replay_id,
            "expiresIn": max(0, int(TTL_SECONDS - (time.monotonic() - session.created))),
            "state": dict(session.state),
            "members": [{"name": member.name, "host": member.host,
                         "connected": member.ws is not None and not member.ws.closed}
                        for member in session.members.values()]}


def _token(request: web.Request, data: dict | None = None) -> str | None:
    authorization = request.headers.get("Authorization", "")
    if authorization.startswith("Bearer "):
        return authorization[7:]
    return data.get("token") if data and isinstance(data.get("token"), str) else None


async def create(request: web.Request) -> web.Response:
    if not _enabled():
        return _not_found()
    data = await _body(request)
    if data is None:
        return web.json_response({"error": "JSON object required"}, status=400)
    replay_id = data.get("replayId")
    name = _name(data.get("name", "Host"))
    if not isinstance(replay_id, str) or not re.fullmatch(r"(?:[a-f0-9]{16}|demo)", replay_id) or not name:
        return web.json_response({"error": "valid replayId and name required"}, status=400)
    sessions = _sessions(request)
    while True:
        code = "".join(secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZ23456789") for _ in range(8))
        if code not in sessions:
            break
    token = secrets.token_urlsafe(32)
    lobby = Lobby(code, replay_id, time.monotonic(), token)
    lobby.members[token] = Member(name, host=True)
    lobby.state["by"] = name
    sessions[code] = lobby
    task = asyncio.create_task(_expire_after(request.app, lobby))
    lobby.expiry_task = task
    request.app["p2_lobby_timers"].add(task)
    task.add_done_callback(request.app["p2_lobby_timers"].discard)
    return web.json_response({"code": code, "token": token, "urls": _urls(lobby)}, status=201)


async def _expire_after(app: web.Application, lobby: Lobby) -> None:
    await asyncio.sleep(TTL_SECONDS)
    if app["p2_lobbies"].get(lobby.code) is lobby:
        app["p2_lobbies"].pop(lobby.code, None)
        await _broadcast(lobby, {"type": "closed", "reason": "expired"})
        for member in lobby.members.values():
            _clear_grace(member)
            if member.ws is not None:
                await member.ws.close(code=1001, message=b"lobby expired")


def _clear_grace(member: Member) -> None:
    task, member.grace_task = member.grace_task, None
    member.grace_deadline = None
    if task is not None and task is not asyncio.current_task():
        task.cancel()


def _arm_grace(app: web.Application, lobby: Lobby, token: str, member: Member) -> None:
    member.grace_deadline = time.monotonic() + JOIN_GRACE_SECONDS
    if member.grace_task is not None and not member.grace_task.done():
        return  # Refresh the deadline, but keep a single eviction task.
    task = asyncio.create_task(_expire_unconnected(lobby, token, member))
    member.grace_task = task
    app["p2_lobby_timers"].add(task)
    task.add_done_callback(app["p2_lobby_timers"].discard)


async def _evict_grace(lobby: Lobby, token: str, member: Member) -> None:
    if (lobby.members.get(token) is member and member.ws is None and not member.connecting
            and member.grace_deadline is not None
            and time.monotonic() >= member.grace_deadline):
        lobby.members.pop(token)
        _clear_grace(member)
        # Socket drop already sent its one leave, but peers still need the
        # post-eviction roster. A never-connected guest needs its first leave.
        action = "roster" if member.leave_announced else "leave"
        member.leave_announced = True
        await _broadcast(lobby, {"type": "presence", "action": action, "name": member.name,
                                 "members": _public(lobby)["members"]})


async def _expire_unconnected(lobby: Lobby, token: str, member: Member) -> None:
    try:
        while member.grace_deadline is not None:
            await asyncio.sleep(max(0, member.grace_deadline - time.monotonic()))
            if time.monotonic() < member.grace_deadline:
                continue  # A repeated disconnect refreshed the deadline.
            # A handshake may be awaiting prepare() at the deadline. Its
            # failure path evicts if the grace period has already elapsed.
            await _evict_grace(lobby, token, member)
            break
    finally:
        if member.grace_task is asyncio.current_task():
            member.grace_task = None


async def state(request: web.Request) -> web.Response:
    if not _enabled():
        return _not_found()
    lobby = _lobby(request)
    return web.json_response(_public(lobby)) if lobby else _not_found()


async def join(request: web.Request) -> web.Response:
    if not _enabled():
        return _not_found()
    lobby = _lobby(request)
    if lobby is None:
        return _not_found()
    data = await _body(request)
    name = _name(data.get("name")) if data else None
    if not name:
        return web.json_response({"error": "name must be 1–32 characters"}, status=400)
    if len(lobby.members) >= MAX_MEMBERS:
        return web.json_response({"error": "lobby full"}, status=409)
    # The invitation code itself authorizes first-time joining; return a unique bearer token.
    token = secrets.token_urlsafe(32)
    member = Member(name)
    lobby.members[token] = member
    _arm_grace(request.app, lobby, token, member)
    return web.json_response({"code": lobby.code, "token": token, "replayId": lobby.replay_id})


async def _broadcast(lobby: Lobby, message: dict) -> None:
    for member in list(lobby.members.values()):
        if member.ws is not None and not member.ws.closed:
            try:
                await member.ws.send_json(message)
            except (ConnectionError, RuntimeError):
                pass


async def leave(request: web.Request) -> web.Response:
    if not _enabled():
        return _not_found()
    lobby = _lobby(request)
    if lobby is None:
        return _not_found()
    data = await _body(request)
    token = _token(request, data)
    member = lobby.members.get(token) if token else None
    if member is None:
        return web.json_response({"error": "invalid token"}, status=403)
    if member.host:
        _sessions(request).pop(lobby.code, None)
        if lobby.expiry_task is not None:
            lobby.expiry_task.cancel()
        for participant in lobby.members.values():
            _clear_grace(participant)
        await _broadcast(lobby, {"type": "closed"})
        for participant in lobby.members.values():
            if participant.ws is not None:
                await participant.ws.close(code=1000, message=b"lobby closed")
    else:
        assert token is not None
        lobby.members.pop(token, None)
        _clear_grace(member)
        ws, member.ws = member.ws, None
        # A dropped socket already sent leave; REST revocation still updates
        # the roster without sending a second leave for the same departure.
        action = "roster" if member.leave_announced else "leave"
        member.leave_announced = True
        await _broadcast(lobby, {"type": "presence", "action": action, "name": member.name,
                                 "members": _public(lobby)["members"]})
        if ws is not None:
            await ws.close(code=1000, message=b"left lobby")
    return web.json_response({"left": True})


def _playback(data: dict, name: str) -> dict | None:
    n, t, speed, paused = (data.get(key) for key in ("round", "tMs", "speed", "paused"))
    if type(n) is not int or not 1 <= n <= 100:
        return None
    if not isinstance(t, (int, float)) or isinstance(t, bool) or not math.isfinite(t) or not 0 <= t <= 3600000:
        return None
    if not isinstance(speed, (int, float)) or isinstance(speed, bool) or not math.isfinite(speed) or not 0 < speed <= 8:
        return None
    if type(paused) is not bool:
        return None
    return {"type": "state", "round": n, "tMs": int(t), "speed": speed,
            "paused": paused, "by": name}


async def socket_handler(request: web.Request) -> web.StreamResponse:
    if not _enabled():
        return _not_found()
    lobby = _lobby(request)
    if lobby is None:
        return _not_found()
    token = request.query.get("token", "")
    member = lobby.members.get(token)
    if member is None:
        return web.json_response({"error": "invalid token"}, status=403)
    if member.connecting or (member.ws is not None and not member.ws.closed):
        return web.json_response({"error": "member already connected"}, status=409)
    ws = web.WebSocketResponse(heartbeat=25, max_msg_size=2048)
    member.connecting = True
    try:
        await ws.prepare(request)
    except BaseException:
        member.connecting = False
        await _evict_grace(lobby, token, member)
        raise
    member.connecting = False
    if lobby.members.get(token) is not member or request.app["p2_lobbies"].get(lobby.code) is not lobby:
        await ws.close(code=1000, message=b"lobby closed")
        return ws
    member.ws = ws
    _clear_grace(member)
    member.leave_announced = False
    try:
        await ws.send_json({"type": "state", **lobby.state})
        await _broadcast(lobby, {"type": "presence", "action": "join", "name": member.name,
                                 "members": _public(lobby)["members"]})
        async for message in ws:
            if message.type != WSMsgType.TEXT:
                continue
            try:
                data = message.json()
            except ValueError:
                await ws.send_json({"type": "error", "error": "invalid JSON"})
                continue
            if not isinstance(data, dict) or data.get("type") != "state":
                await ws.send_json({"type": "error", "error": "state message required"})
                continue
            if not member.host:
                await ws.send_json({"type": "error", "error": "host only"})
                continue
            playback = _playback(data, member.name)
            if playback is None:
                await ws.send_json({"type": "error", "error": "invalid playback state"})
                continue
            lobby.state = {key: playback[key] for key in ("round", "tMs", "speed", "paused", "by")}
            await _broadcast(lobby, playback)
    finally:
        if member.ws is ws:
            member.ws = None
            if request.app["p2_lobbies"].get(lobby.code) is lobby and lobby.members.get(token) is member:
                if not member.host:
                    _arm_grace(request.app, lobby, token, member)
                member.leave_announced = True
                await _broadcast(lobby, {"type": "presence", "action": "leave", "name": member.name,
                                         "members": _public(lobby)["members"]})
    return ws


async def _cleanup(app: web.Application) -> None:
    closers = list(app["p2_lobby_closers"])
    if closers:
        await asyncio.gather(*closers, return_exceptions=True)
    tasks = list(app["p2_lobby_timers"])
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    for lobby in app["p2_lobbies"].values():
        for member in lobby.members.values():
            if member.ws is not None:
                await member.ws.close(code=1001, message=b"server stopping")


def mount(app: web.Application) -> None:
    app["p2_lobbies"] = {}
    app["p2_lobby_timers"] = set()
    app["p2_lobby_closers"] = set()
    app.on_cleanup.append(_cleanup)
    app.router.add_post("/api/lobby", create)
    app.router.add_get("/api/lobby/{code}", state)
    app.router.add_post("/api/lobby/{code}/join", join)
    app.router.add_post("/api/lobby/{code}/leave", leave)
    app.router.add_get("/ws/lobby/{code}", socket_handler)
