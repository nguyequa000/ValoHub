"""P3 feature — personal coach API (card P4-B, profile `default`).

Contract: ``VALOPLANT_WORK/P3_CONTRACT.md`` §P4 (P4-B) + ``P3_MULTIAGENT_PLAN.md``
§P4. Rules source: ``bot/replay/coach_metrics.py`` (P4-A). This module mounts through
``bot.replay.p2.FEATURES`` (name ``p3_coach``).

Routes
------
    GET  /api/replays/{replay_id}/me          -> {"replayId", "player": "P4"|null, "updatedAt"}
    PUT  /api/replays/{replay_id}/me          {"player": "P4"} -> the saved pick   [loopback only]
    POST /api/replays/{replay_id}/coach       {"player"?, "backend"?, "model"?}
         -> 200 coach result, never 5xx for a missing model                    [loopback only]
    GET  /api/replays/{replay_id}/coach       cached last result (optional ?player=)

Rules-first: the findings are the deterministic ones from ``coach_metrics`` for the
selected player. A local LLM (the probe chain shared with ``p2_review`` — managed
llama.cpp endpoint first, P4-R/operator pick ``Qwen3.8-9B-Q6_K``) may only *narrate*
them: its strict JSON (summary / top-3 mistakes / drills) is validated so every
cited ref must already exist in the rules findings' refs; an item that cannot cite
one is dropped, and every dropped item/ref is counted in ``llm.drops``. A missing or
unparseable LLM is NOT an error here: the response stays 200 with
``llm.available=false`` + a reason, and the rules findings are the payload.

The "this is me" pick persists in ``viewer/me.json`` (atomic write through
``store.write_json_atomic``); reads tolerate the file being absent.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from aiohttp import web

from bot.web.peer import _loopback_peer

from . import coach_metrics, p2_review, store

LOG = logging.getLogger("bot.replay.p3_coach")

SCHEMA = "valoplant_coach_api/v1"
ME_SCHEMA = "valoplant_me/v1"
MAX_MISTAKES = 3          # contract: top-3 mistakes
MAX_DRILLS = 6
MAX_NARRATION_ITEMS = 20  # parse cap (a model cannot flood the validator/response)
SEVERITIES = ("major", "minor", "info")

CACHE_KEY = web.AppKey("p3_coach_cache", dict)
MOUNTED_KEY = web.AppKey("p3_coach_mounted", bool)

NARRATION_RETRY = (
    "Your previous reply could not be parsed. Reply with ONLY a single JSON object, "
    "no prose, no markdown fences, exactly: "
    '{"summary": "<string>", "mistakes": [{"title": "<string>", "detail": "<string>", '
    '"severity": "major|minor|info", "refs": [{"round": <int>, "t": <int>}]}], '
    '"drills": [{"title": "<string>", "detail": "<string>", '
    '"refs": [{"round": <int>, "t": <int>}]}]}'
)

SYSTEM_PROMPT = (
    "You are a Valorant coach writing a short personal review for one player from "
    "their deterministic replay findings. Narrate THEM only; never invent events, "
    "numbers or timings.\n"
    "Every mistake and every drill MUST cite at least one ref copied verbatim from "
    'the "refs" lists below (each ref looks like {"round": <int>, "t": <ms>}); an '
    "item citing anything not present there is discarded.\n"
    'Reply with ONLY a JSON object: {"summary": "<2-4 sentences>", "mistakes": '
    '[{"title": "<short>", "detail": "<what to do differently>", "severity": '
    '"major|minor|info", "refs": [{"round": 5, "t": 48200}]}], "drills": [{"title": '
    '"<short>", "detail": "<concrete practice drill>", '
    '"refs": [{"round": 5, "t": 48200}]}]}\n'
    "Order mistakes by importance; at most 3 mistakes."
)


class CoachError(Exception):
    """A request input problem (maps to HTTP 4xx)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": str(message)}, status=status)


def _cache(app: web.Application) -> dict:
    cache = app.get(CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
        app[CACHE_KEY] = cache
    return cache


def _loopback_only(request: web.Request) -> web.Response | None:
    """Mutating routes are loopback-only — same semantics as ``bot/web/peer.py``."""
    if _loopback_peer(request.remote):
        return None
    return _error("this endpoint is loopback-only", 403)


async def _json_body(request: web.Request) -> dict:
    if not request.can_read_body:
        return {}
    try:
        body = await request.json()
    except Exception:
        raise CoachError("request body must be JSON", 400) from None
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise CoachError("request body must be a JSON object", 400)
    return body


# ------------------------------------------------------------------ me pick


def _me_path(replay_id) -> Path:
    return store.replay_dir(replay_id) / "viewer" / "me.json"


def load_me(replay_id) -> dict:
    """``{"player": slot|None, "updatedAt": iso|None}`` — safe when the file is absent."""
    data = store.read_json(_me_path(replay_id))
    if not isinstance(data, dict):
        data = {}
    player, updated = data.get("player"), data.get("updatedAt")
    return {
        "player": player if isinstance(player, str) and player else None,
        "updatedAt": updated if isinstance(updated, str) and updated else None,
    }


def save_me(replay_id, slot: str) -> dict:
    payload = {"schema": ME_SCHEMA, "replayId": str(replay_id), "player": slot,
               "updatedAt": store.iso_now()}
    store.write_json_atomic(_me_path(replay_id), payload)
    return payload


def _match_slots(match) -> list[str]:
    return [str(item["id"]) for item in (match.get("players") or [])
            if isinstance(item, dict) and item.get("id")]


def resolve_player(replay_id, requested) -> str:
    """Explicit ``player`` wins over ``viewer/me.json``; both absent -> CoachError."""
    raw = requested if requested not in (None, "") else load_me(replay_id)["player"]
    if raw in (None, ""):
        raise CoachError(
            "no player selected: pass {\"player\": \"P1\"} or set the pick with "
            f"PUT /api/replays/{replay_id}/me", 400)
    try:
        return coach_metrics.normalize_slot(raw)
    except ValueError:
        raise CoachError(f"invalid player slot: {raw!r}", 400) from None


# --------------------------------------------------------------- narration


def _as_int(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return None
    return None


def _text(value, limit: int) -> str:
    if isinstance(value, str):
        return value.strip()[:limit]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)[:limit]
    return ""


def _severity(value) -> str:
    if isinstance(value, str) and value.strip().lower() in SEVERITIES:
        return value.strip().lower()
    return "info"


def _narration_refs(raw) -> list[dict]:
    refs: list[dict] = []
    if not isinstance(raw, list):
        return refs
    for ref in raw[: coach_metrics.MAX_REFS]:
        if not isinstance(ref, dict):
            continue
        number = _as_int(ref.get("round"))
        stamp = _as_int(ref.get("t") if "t" in ref else ref.get("tMs"))
        if number is None or stamp is None:
            continue
        refs.append({"round": number, "t": stamp, "tSec": round(stamp / 1000.0, 3)})
    return refs


def _narration_items(raw) -> list[dict]:
    items: list[dict] = []
    if not isinstance(raw, list):
        return items
    for item in raw[:MAX_NARRATION_ITEMS]:
        if not isinstance(item, dict):
            continue
        title = _text(item.get("title"), 200)
        if not title:
            continue
        items.append({"title": title, "detail": _text(item.get("detail"), 2000),
                      "severity": _severity(item.get("severity")),
                      "refs": _narration_refs(item.get("refs"))})
    return items


def parse_narration(text) -> dict | None:
    """Defensive parse of a model reply into ``{"summary", "mistakes", "drills"}``.

    Returns None when there is no usable summary — the caller may retry once.
    Structural filtering only; grounding is ``validate_narration``'s job.
    """
    raw = p2_review.extract_json(text)
    if isinstance(raw, dict) and isinstance(raw.get("coaching"), dict):
        raw = raw["coaching"]
    if not isinstance(raw, dict):
        return None
    summary = _text(raw.get("summary"), 4000)
    if not summary:
        return None
    return {"summary": summary,
            "mistakes": _narration_items(raw.get("mistakes")),
            "drills": _narration_items(raw.get("drills"))}


def allowed_refs(findings) -> set[tuple[int, int]]:
    """Every ``(round, t)`` the rules findings cite — the only refs narration may use."""
    allowed: set[tuple[int, int]] = set()
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        for ref in finding.get("refs") or []:
            if not isinstance(ref, dict):
                continue
            number, stamp = _as_int(ref.get("round")), _as_int(ref.get("t"))
            if number is not None and stamp is not None:
                allowed.add((number, stamp))
    return allowed


def validate_narration(narration: dict, allowed: set[tuple[int, int]]) -> tuple[dict, dict]:
    """Filter a parsed narration to the grounded subset.

    An item is dropped when none of its refs exists in ``allowed`` (an item without
    refs cites nothing and is dropped too). Ungrounded refs on a surviving item are
    removed. Mistakes are capped at ``MAX_MISTAKES``, drills at ``MAX_DRILLS``.
    Returns ``(clean, drops)`` where ``drops`` counts removed items and refs.
    """
    clean = {"summary": narration.get("summary") or "", "mistakes": [], "drills": []}
    drops = {"items": 0, "refs": 0, "mistakes": 0, "drills": 0}
    for kind, cap in (("mistakes", MAX_MISTAKES), ("drills", MAX_DRILLS)):
        for item in narration.get(kind) or []:
            kept = []
            for ref in item.get("refs") or []:
                if (ref.get("round"), ref.get("t")) in allowed:
                    kept.append(ref)
                else:
                    drops["refs"] += 1
            if not kept or len(clean[kind]) >= cap:
                drops["items"] += 1
                drops[kind] += 1
                continue
            clean[kind].append({**item, "refs": kept})
    return clean, drops


def build_messages(report: dict) -> list[dict]:
    """The strict narration prompt (deterministic for a given rules report)."""
    findings = []
    for finding in report.get("findings") or []:
        if not isinstance(finding, dict):
            continue
        findings.append({
            "id": finding.get("id"),
            "kind": finding.get("kind"),
            "severity": finding.get("severity"),
            "text": finding.get("text"),
            "refs": [{"round": ref.get("round"), "t": ref.get("t")}
                     for ref in finding.get("refs") or [] if isinstance(ref, dict)],
        })
    payload = {
        "player": report.get("player"),
        "map": report.get("map"),
        "roundsAnalyzed": report.get("roundsAnalyzed"),
        "findings": findings,
    }
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Findings (JSON):\n"
         + json.dumps(payload, ensure_ascii=False) + "\nWrite the review. JSON only."},
    ]


# -------------------------------------------------------------------- routes


async def me_get(request: web.Request) -> web.Response:
    replay_id = request.match_info["replay_id"]
    try:
        store.replay_dir(replay_id)
    except ValueError:
        return _error("replay not found", 404)
    me = load_me(replay_id)
    return web.json_response({"replayId": str(replay_id), "player": me["player"],
                              "updatedAt": me["updatedAt"]})


async def me_put(request: web.Request) -> web.Response:
    denied = _loopback_only(request)
    if denied is not None:
        return denied
    replay_id = request.match_info["replay_id"]
    try:
        store.replay_dir(replay_id)
    except ValueError:
        return _error("replay not found", 404)
    try:
        payload = await _json_body(request)
    except CoachError as exc:
        return _error(str(exc), exc.status)

    raw = payload.get("player")
    if raw in (None, ""):
        return _error("'player' is required", 400)
    try:
        slot = coach_metrics.normalize_slot(raw)
    except ValueError as exc:
        return _error(str(exc), 400)

    match = store.load_match(replay_id)
    if not isinstance(match, dict):
        return _error(f"replay '{replay_id}' has no normalized viewer data yet", 404)
    slots = _match_slots(match)
    if slot not in slots:
        return _error(f"slot {slot!r} is not in this match: {slots}", 400)

    saved = await asyncio.to_thread(save_me, replay_id, slot)
    LOG.info("me pick saved for %s: %s", replay_id, slot)
    return web.json_response({"replayId": str(replay_id), "player": saved["player"],
                              "updatedAt": saved["updatedAt"]})


async def coach_post(request: web.Request) -> web.Response:
    denied = _loopback_only(request)
    if denied is not None:
        return denied
    replay_id = request.match_info["replay_id"]
    try:
        store.replay_dir(replay_id)
    except ValueError:
        return _error("replay not found", 404)
    try:
        payload = await _json_body(request)
        slot = resolve_player(replay_id, payload.get("player"))
    except CoachError as exc:
        return _error(str(exc), exc.status)
    except ValueError:
        return _error("replay not found", 404)

    try:
        report = await asyncio.to_thread(
            coach_metrics.player_report, store.replay_dir(replay_id), slot)
    except ValueError as exc:
        return _error(str(exc), 400)
    if report.get("error"):
        return _error(f"replay '{replay_id}': {report['error']}", 404)

    allowed = allowed_refs(report.get("findings"))
    narration = None
    llm: dict = {"available": False, "reason": "narration not attempted"}
    try:
        answer = await p2_review.request_chat(
            build_messages(report),
            backend=payload.get("backend"), model=payload.get("model"),
            parse=parse_narration, retry_instruction=NARRATION_RETRY,
            parse_noun="JSON narration")
    except p2_review.ReviewError as exc:
        return _error(str(exc), exc.status)
    except p2_review.NoLLMReachable as exc:
        llm = {"available": False,
               "reason": ("the local LLM answered but returned no parseable narration "
                          "JSON (after 1 retry)" if exc.answered else "no local LLM reachable"),
               "tried": exc.tried}
    else:
        clean, drops = validate_narration(answer["content"], allowed)
        narration = clean
        llm = {"available": True, "backend": answer["backend"], "model": answer["model"],
               "retried": answer["retried"], "drops": drops}

    result = {
        "schema": SCHEMA,
        "replayId": str(replay_id),
        "player": report.get("player"),
        "map": report.get("map"),
        "generatedAt": store.iso_now(),
        "roundsAnalyzed": report.get("roundsAnalyzed"),
        "rules": {
            "findings": report.get("findings") or [],
            "metrics": report.get("metrics"),
            "baseline": report.get("baseline"),
            "sideInference": report.get("sideInference"),
            "notes": report.get("notes") or [],
        },
        "narration": narration,
        "llm": llm,
    }
    _cache(request.app).setdefault(str(replay_id), {})[slot] = result
    return web.json_response(result)


async def coach_get(request: web.Request) -> web.Response:
    replay_id = request.match_info["replay_id"]
    try:
        store.replay_dir(replay_id)
    except ValueError:
        return _error("replay not found", 404)
    requested = request.query.get("player")
    if requested in (None, ""):
        requested = load_me(replay_id)["player"]
    slot = None
    if requested not in (None, ""):
        try:
            slot = coach_metrics.normalize_slot(requested)
        except ValueError:
            return _error(f"invalid player slot: {requested!r}", 400)

    entry = _cache(request.app).get(str(replay_id)) or {}
    if slot is not None:
        cached = entry.get(slot)
    elif len(entry) == 1:
        cached = next(iter(entry.values()))
    else:
        cached = None
    if cached is None:
        return _error(f"no cached coach result for replay '{replay_id}'", 404)
    return web.json_response(cached)


def mount(app: web.Application) -> None:
    """Register the coach routes (idempotent: a second call is a no-op)."""
    if app.get(MOUNTED_KEY):
        return
    app[MOUNTED_KEY] = True
    _cache(app)
    app.router.add_get("/api/replays/{replay_id}/me", me_get)
    app.router.add_put("/api/replays/{replay_id}/me", me_put)
    app.router.add_post("/api/replays/{replay_id}/coach", coach_post)
    app.router.add_get("/api/replays/{replay_id}/coach", coach_get)
    LOG.info("p3 coach mounted (llm default=%s, max mistakes=%d)",
             p2_review.LLAMACPP_DEFAULT_MODEL, MAX_MISTAKES)
