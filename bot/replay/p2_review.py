"""P2 feature — local-LLM match review (card: data-bot).

Contract: ``VALOPLANT_WORK/P2_CONTRACT.md`` §2; the probe chain was amended by
P3/P4-B (the coach lane): the OpenAI-compatible llama.cpp candidate runs FIRST.

Routes
------
    POST /api/replays/{replay_id}/review   {"backend","model","roundFrom","roundTo"} (all optional)
         -> 200 result schema | 400 bad input | 404 no viewer data | 503 no local LLM
    GET  /api/replays/{replay_id}/review   cached last result | 404

The digest is built from the **normalized viewer JSON only** (``bot.replay.store``
helpers — never raw NDJSON), compact enough for a ~9B model (< 6 KB). The LLM call
goes to a LOCAL endpoint only, probed in this order:

  1. llama.cpp  OpenAI-compatible ``llama-server`` — the Hermes-managed server by
     default, addressed through a server descriptor read at request time
     (``VALO_LLM_KEY_FILE``, default ``%LOCALAPPDATA%/hermes/runtimes/llamacpp/
     server.json``): its ``base_url`` and ``api_key``. ``VALO_LLM_URL`` retargets
     the base (the managed key never travels to a different URL unless
     ``VALO_LLM_KEY_FILE`` names that descriptor explicitly); ``VALO_LLM_MODEL``
     selects the model id — default ``Qwen3.8-9B-Q6_K``, the managed ``light``
     preset, i.e. the operator / P4-R model choice. ``backend="env"`` is kept as
     an alias for this candidate (it used to be the bare ``VALO_LLM_URL`` probe).
  2. LM Studio  ``http://127.0.0.1:1234/v1``
  3. Ollama     ``http://127.0.0.1:11434/api/chat``

stdlib + aiohttp only. The llama.cpp api_key is read from the descriptor at
request time, sent only as a ``Bearer`` header to the base_url it belongs to,
and never logged, echoed, or persisted.

Operational escape hatches (defaults match the contract exactly):
``VALO_LMSTUDIO_URL`` / ``VALO_OLLAMA_URL`` retarget candidates 2-3 so a hermetic
test can stand a mock in front of them; ``VALO_LLM_TIMEOUT`` overrides the 300 s cap.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from . import store
from .offline import assert_local_url, reject_cloud_model
from .redact import install_log_redaction, safe_exc

install_log_redaction()                  # secrets/bodies never reach the log sink

LOG = logging.getLogger("bot.replay.p2_review")

DIGEST_BUDGET_BYTES = 6144          # contract: target < 6 KB of context
DEFAULT_TIMEOUT_S = 300.0           # contract: timeout <= 300 s
PROBE_TIMEOUT_S = 5.0
MAX_FINDINGS = 40
SEVERITIES = ("info", "warn", "tip")
MAX_ROUNDS = 60

LMSTUDIO_DEFAULT = "http://127.0.0.1:1234/v1"
OLLAMA_DEFAULT = "http://127.0.0.1:11434"
CM_PER_M = 100

# llama.cpp candidate (first in the probe chain; P3/P4-B). Default model = the
# managed `light` preset alias, i.e. the operator / P4-R model choice.
LLAMACPP_DEFAULT_MODEL = "Qwen3.8-9B-Q6_K"
BACKEND_ALIASES = {"env": "llamacpp"}  # legacy name of the bare VALO_LLM_URL probe

# last successful review per replay id (in-memory; the contract asks for a cached
# last result and does NOT ask for one on disk — viewer/** stays the normalizer's)
CACHE_KEY = web.AppKey("p2_review_cache", dict)
MOUNTED_KEY = web.AppKey("p2_review_mounted", bool)

RETRY_INSTRUCTION = (
    "Your previous reply could not be parsed. Reply with ONLY a single JSON object, "
    'no prose, no markdown fences, exactly: {"summary": "<string>", "findings": '
    '[{"round": <int>, "tMs": <int|null>, "severity": "info|warn|tip", "title": "<string>", '
    '"detail": "<string>"}]}'
)

SYSTEM_PROMPT = (
    "You are a Valorant coach reviewing one match from a replay-derived digest. "
    "Be concrete and actionable, cite round numbers, and prefer a few high-value "
    "insights over many generic ones.\n"
    "Team 0 (A) starts each half as attackers; team 1 (D) starts as defenders; sides "
    "swap after round 12. Coordinates are metres in the map's world space.\n"
    'Reply with ONLY a JSON object: {"summary": "<2-4 sentence match summary>", '
    '"findings": [{"round": <int>, "tMs": <int|null>, "severity": "info|warn|tip", '
    '"title": "<short title>", "detail": "<what happened and what to do differently>"}]}\n'
    "severity: info = neutral observation, warn = recurring mistake, tip = coaching idea."
)


class ReviewError(Exception):
    """Raised for a request input problem (maps to HTTP 4xx)."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class NoLLMReachable(Exception):
    """No candidate answered; carries the per-candidate attempt log."""

    def __init__(self, tried: list[str], answered: bool = False) -> None:
        super().__init__("no local LLM reachable")
        self.tried = tried
        self.answered = answered


class UpstreamError(RuntimeError):
    """A non-200 upstream answer reduced to ``{code, status, request_id}`` - never the response body."""

    def __init__(self, code: str, status: int, request_id: str | None = None) -> None:
        self.code, self.status, self.request_id = code, status, request_id
        super().__init__(f"code={code} status={status} request_id={request_id or '-'}")

    @classmethod
    def from_response(cls, response) -> "UpstreamError":
        status = int(response.status)
        code = {401: "invalid_key", 403: "forbidden", 404: "not_found", 429: "rate_limited"}.get(
            status, "upstream_unavailable" if status >= 500 else "upstream_rejected")
        rid = None
        for name in ("x-request-id", "request-id"):
            value = response.headers.get(name)
            if isinstance(value, str) and _REQUEST_ID_RE.match(value):
                rid = value
                break
        return cls(code, status, rid)


_REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9._:\-]{1,64}$")


def describe_failure(exc: BaseException) -> str:
    """One safe line for ``tried``/logs: structured for upstream errors, redacted + capped otherwise."""
    if isinstance(exc, UpstreamError):
        return f"UpstreamError: {exc}"
    return safe_exc(exc)


# --------------------------------------------------------------------- digest


def _as_int(value) -> int | None:
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


def _round_numbers(replay_id) -> list[int]:
    """Round numbers present on disk, preferring the match.json index."""
    match = store.load_match(replay_id)
    numbers: list[int] = []
    if isinstance(match, dict) and isinstance(match.get("rounds"), list):
        numbers = [n for n in (_as_int(r.get("n")) for r in match["rounds"] if isinstance(r, dict)) if n]
    if numbers:
        return sorted(set(numbers))

    rdir = store.replay_dir(replay_id)
    found = set()
    for rounds_dir in (rdir / "viewer" / "rounds", rdir / "rounds"):
        if not rounds_dir.is_dir():
            continue
        for path in rounds_dir.glob("r*.json"):
            suffix = path.stem[1:]
            if suffix.isdigit() and int(suffix) > 0:
                found.add(int(suffix))
    return sorted(found)


def _spawn_points(round_data: dict) -> dict:
    """First sampled position per player, in whole metres; absent data is omitted."""
    points: dict[str, list[int]] = {}
    players = round_data.get("players") if isinstance(round_data, dict) else None
    if not isinstance(players, dict):
        return points
    for pid, series in players.items():
        if not isinstance(series, dict):
            continue
        xs, ys = series.get("x"), series.get("y")
        if not isinstance(xs, list) or not isinstance(ys, list) or not xs or not ys:
            continue
        x, y = _as_int(xs[0]), _as_int(ys[0])
        if x is None or y is None:
            continue
        points[str(pid)] = [round(x / CM_PER_M), round(y / CM_PER_M)]
    return points


def _round_digest(n: int, round_data: dict, meta: dict | None) -> dict:
    raw_events = round_data.get("events")
    events = raw_events if isinstance(raw_events, list) else []
    kills, ults, plants, defuses = [], [], [], []
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        t_ms = _as_int(event.get("t"))
        actor = event.get("actor")
        target = event.get("target")
        if kind == "kill" and actor:
            kills.append([actor, target, t_ms])
        elif kind == "ult" and (actor or t_ms is not None):
            # an unattributed ult is still a real event — keep it, actor stays null
            ults.append([actor, t_ms])
        elif kind == "plant" and (actor or t_ms is not None):
            plants.append([actor, t_ms])
        elif kind == "defuse" and (actor or t_ms is not None):
            defuses.append([actor, t_ms])

    raw_spike = round_data.get("spike")
    spike = raw_spike if isinstance(raw_spike, dict) else {}
    plant = plants[0] if plants else [None, None]
    defuse = defuses[0] if defuses else [None, None]
    if _as_int(spike.get("plantT")) is not None and plant[1] is None:
        plant = [None, _as_int(spike.get("plantT"))]
    if _as_int(spike.get("defuseT")) is not None and defuse[1] is None:
        defuse = [None, _as_int(spike.get("defuseT"))]

    start_ms = _as_int(round_data.get("startMs"))
    end_ms = _as_int(round_data.get("endMs"))
    if isinstance(meta, dict):
        if start_ms is None:
            start_ms = _as_int(meta.get("startMs"))
        if end_ms is None:
            end_ms = _as_int(meta.get("endMs"))

    digest = {
        "n": n,
        "startMs": start_ms,
        "endMs": end_ms,
        "kills": kills,
        "ults": ults,
        "plant": {"actor": plant[0], "tMs": plant[1]},
        "defuse": {"actor": defuse[0], "tMs": defuse[1]},
        "spawn": _spawn_points(round_data),
    }
    # counts are only used when the round file carried no event list at all
    if isinstance(meta, dict):
        if not events:
            k = _as_int(meta.get("kills"))
            p = _as_int(meta.get("plants"))
            d = _as_int(meta.get("defuses"))
            if k:
                digest["killsNote"] = f"{k} kills (count only — event detail unavailable)"
            if p:
                digest["plantsNote"] = f"{p} plant(s)"
            if d:
                digest["defusesNote"] = f"{d} defuse(s)"
        winner = _as_int(meta.get("winner"))
        if winner is not None:
            digest["winner"] = winner
        end_type = meta.get("endType")
        if isinstance(end_type, str) and end_type:
            digest["endType"] = end_type
    return digest


def build_digest(replay_id, round_from=None, round_to=None) -> dict:
    """Compact per-round digest from viewer JSON only (raises ReviewError on bad input)."""
    match = store.load_match(replay_id)
    if not isinstance(match, dict):
        raise ReviewError(f"replay '{replay_id}' has no normalized viewer data yet", 404)

    numbers = _round_numbers(replay_id)[:MAX_ROUNDS]
    if not numbers:
        raise ReviewError(f"replay '{replay_id}' has no rounds", 404)

    lo = _as_int(round_from)
    hi = _as_int(round_to)
    if round_from not in (None, "") and lo is None:
        raise ReviewError("'roundFrom' must be an integer", 400)
    if round_to not in (None, "") and hi is None:
        raise ReviewError("'roundTo' must be an integer", 400)
    if lo is not None and hi is not None and lo > hi:
        raise ReviewError("'roundFrom' must not exceed 'roundTo'", 400)

    selected = [n for n in numbers if (lo is None or n >= lo) and (hi is None or n <= hi)]
    if not selected:
        raise ReviewError(
            f"no rounds selected (available 1-{numbers[-1]} for replay '{replay_id}')", 400
        )

    meta_by_n = {}
    if isinstance(match.get("rounds"), list):
        for item in match["rounds"]:
            if isinstance(item, dict):
                meta_by_n[_as_int(item.get("n"))] = item

    rounds = []
    for n in selected:
        round_data = store.load_round(replay_id, n)
        if not isinstance(round_data, dict):
            continue  # never fabricate a round that is not on disk
        rounds.append(_round_digest(n, round_data, meta_by_n.get(n)))
    if not rounds:
        raise ReviewError(f"replay '{replay_id}' has no readable rounds in range", 404)

    players = []
    if isinstance(match.get("players"), list):
        for player in match["players"]:
            if not isinstance(player, dict) or not player.get("id"):
                continue
            players.append(
                {
                    "id": str(player["id"]),
                    "agent": player.get("agent"),
                    "team": _as_int(player.get("team")),
                }
            )

    raw_map = match.get("map")
    map_block = raw_map if isinstance(raw_map, dict) else {}
    return {
        "schema": "valoplant_review_digest/v1",
        "replayId": str(replay_id),
        "map": map_block.get("name"),
        "patch": match.get("patch"),
        "durationMs": _as_int(match.get("durationMs")),
        "roundFrom": rounds[0]["n"],
        "roundTo": rounds[-1]["n"],
        "availableRounds": [numbers[0], numbers[-1]],
        "players": players,
        "rounds": rounds,
    }


def _seconds(ms) -> str:
    return f"{ms / 1000:.1f}" if isinstance(ms, int) else "?"


def _positions(round_digest: dict) -> str:
    spawn = round_digest.get("spawn") or {}
    if not spawn:
        return ""
    return " ".join(f"{pid}({xy[0]},{xy[1]})" for pid, xy in sorted(spawn.items()))


def render_digest(digest: dict, budget: int = DIGEST_BUDGET_BYTES) -> str:
    """Compact text sent to the model; drops detail (full → no positions → counts
    only) until it fits ``budget``. The counts-only form is the floor: every round
    stays represented, its narrative detail does not."""
    for detail in ("full", "no-positions", "minimal"):
        text = _render(digest, detail)
        if len(text.encode("utf-8")) <= budget:
            return text
    return _render(digest, "minimal")


def _render(digest: dict, detail: str) -> str:
    lines = [
        f"MATCH {digest.get('replayId')} map={digest.get('map')} patch={digest.get('patch')} "
        f"rounds={digest.get('roundFrom')}-{digest.get('roundTo')} of "
        f"{digest.get('availableRounds')} duration={_seconds(digest.get('durationMs'))}s"
    ]
    roster = []
    for player in digest.get("players") or []:
        flag = "A" if player.get("team") == 0 else "D" if player.get("team") == 1 else "?"
        roster.append(f"{player.get('id')}:{player.get('agent') or '?'}/{flag}")
    if roster:
        lines.append("ROSTER(A=attackers r1, D=defenders r1) " + " ".join(roster))

    for round_digest in digest.get("rounds") or []:
        kills = round_digest.get("kills") or []
        kill_parts = [f"{a}>{b or '?'}@{_seconds(t)}" for a, b, t in kills]
        parts = [
            f"R{round_digest['n']:02d}",
            f"{_seconds(round_digest.get('startMs'))}-{_seconds(round_digest.get('endMs'))}s",
        ]
        if detail == "minimal":
            parts.append(f"kills={len(kills)}")
        elif kill_parts:
            parts.append("kills " + ",".join(kill_parts))
        else:
            parts.append("kills=0")

        plant = round_digest.get("plant") or {}
        if plant.get("tMs") is not None or plant.get("actor"):
            parts.append(f"plant {plant.get('actor') or '?'}@{_seconds(plant.get('tMs'))}")
        else:
            parts.append("no-plant")

        defuse = round_digest.get("defuse") or {}
        if defuse.get("tMs") is not None or defuse.get("actor"):
            parts.append(f"defuse {defuse.get('actor') or '?'}@{_seconds(defuse.get('tMs'))}")

        ults = round_digest.get("ults") or []
        if ults and detail == "full":
            parts.append("ults " + ",".join(f"{a or '?'}@{_seconds(t)}" for a, t in ults))
        elif ults:
            parts.append(f"ults={len(ults)}")

        for key in ("killsNote", "plantsNote", "defusesNote"):
            if round_digest.get(key):
                parts.append(str(round_digest[key]))
        if "winner" in round_digest:
            parts.append(f"winner=team{round_digest['winner']}")

        if detail == "full":
            positions = _positions(round_digest)
            if positions:
                parts.append("spawn(m) " + positions)
        lines.append(" | ".join(parts))

    if detail != "full" and any(r.get("spawn") for r in digest.get("rounds") or []):
        lines.append(f"(positions omitted to stay under the {budget_note()} digest budget)")
    return "\n".join(lines) + "\n"


def budget_note() -> str:
    return f"{DIGEST_BUDGET_BYTES // 1024} KB"


def build_messages(digest_text: str) -> list[dict]:
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": "Match digest:\n" + digest_text + "\nReview this match. JSON only."},
    ]


# ------------------------------------------------------------ response parsing


def _strip_fences(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    return match.group(1) if match else text


def _extract_json(text: str):
    """First balanced JSON object in ``text`` (tolerates prose around it)."""
    start = text.find("{")
    while start != -1:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:index + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    return None


def extract_json(text):
    """Public shared parser (used by the P3 coach narration too): markdown fences
    stripped, first balanced JSON value returned, tolerates prose around it."""
    if not isinstance(text, str) or not text.strip():
        return None
    return _extract_json(_strip_fences(text))


def _severity(value) -> str:
    if isinstance(value, str) and value.strip().lower() in SEVERITIES:
        return value.strip().lower()
    return "info"


def _text(value, limit: int) -> str:
    if isinstance(value, str):
        return value.strip()[:limit]
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)[:limit]
    return ""


def parse_review(text) -> dict | None:
    """Defensive parse of a model reply into ``{"summary", "findings"}`` or None."""
    if not isinstance(text, str) or not text.strip():
        return None
    raw = _extract_json(_strip_fences(text))
    if raw is None:
        return None
    if isinstance(raw, dict) and isinstance(raw.get("review"), dict):
        raw = raw["review"]
    if isinstance(raw, list):
        raw = {"summary": "", "findings": raw}
    if not isinstance(raw, dict):
        return None

    summary = raw.get("summary")
    if isinstance(summary, list):
        summary = " ".join(_text(item, 500) for item in summary)
    summary = _text(summary, 4000)
    if not summary:
        summary = _text(raw.get("overview") or raw.get("match_summary"), 4000)
    if not summary:
        return None

    findings = []
    raw_findings = raw.get("findings")
    if isinstance(raw_findings, list):
        for item in raw_findings[:MAX_FINDINGS]:
            if not isinstance(item, dict):
                continue
            title = _text(item.get("title") or item.get("name"), 200)
            if not title:
                continue
            findings.append(
                {
                    "round": _as_int(item.get("round")),
                    "tMs": _as_int(item.get("tMs") if "tMs" in item else item.get("timeMs")),
                    "severity": _severity(item.get("severity")),
                    "title": title,
                    "detail": _text(item.get("detail") or item.get("description"), 2000),
                }
            )
    return {"summary": summary, "findings": findings}


# ------------------------------------------------------------------ backends


def _default_descriptor_path(env) -> Path:
    """Default llama.cpp descriptor: the Hermes-managed server.json."""
    local = (env.get("LOCALAPPDATA") or "").strip() or os.path.expanduser("~")
    return Path(local) / "hermes" / "runtimes" / "llamacpp" / "server.json"


def _read_descriptor(path: Path) -> dict:
    """``{"base", "key"}`` from a llama.cpp server descriptor (missing/corrupt -> {}).

    The values are used only to build the request; they are never logged."""
    try:
        with path.open(encoding="utf-8") as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    base, key = data.get("base_url"), data.get("api_key")
    return {
        "base": base.strip() if isinstance(base, str) and base.strip() else None,
        "key": key.strip() if isinstance(key, str) and key.strip() else None,
    }


def _llamacpp_candidate(env) -> dict | None:
    """First candidate: an OpenAI-compatible llama.cpp server.

    Defaults to the Hermes-managed endpoint (base_url + api_key read at request time
    from ``VALO_LLM_KEY_FILE``, default ``%LOCALAPPDATA%/hermes/runtimes/llamacpp/
    server.json``). ``VALO_LLM_URL`` retargets the base; a key is attached to a
    retargeted base only when ``VALO_LLM_KEY_FILE`` names a descriptor explicitly,
    so the managed key never travels to a different URL. ``VALO_LLM_MODEL`` pins the
    model id (default: the managed ``light`` preset, Qwen3.8-9B-Q6_K). No base
    (no URL and no readable descriptor) -> no candidate; the probe moves on.
    """
    url = (env.get("VALO_LLM_URL") or "").strip()
    explicit_key_file = (env.get("VALO_LLM_KEY_FILE") or "").strip()
    model = (env.get("VALO_LLM_MODEL") or "").strip() or LLAMACPP_DEFAULT_MODEL
    path = Path(explicit_key_file).expanduser() if explicit_key_file else _default_descriptor_path(env)
    descriptor = _read_descriptor(path)
    base = url or descriptor.get("base")
    if not base:
        return None
    key = descriptor.get("key") if (not url or explicit_key_file) else None
    return {"backend": "llamacpp", "kind": "openai", "base": base.rstrip("/"),
            "label": "llamacpp", "model": model, "api_key": key}


def _auth_headers(candidate: dict) -> dict:
    key = candidate.get("api_key")
    return {"Authorization": f"Bearer {key}"} if key else {}


def probe_candidates(backend=None, environ=None) -> list[dict]:
    """Probe candidates in contract order, filtered by the requested backend.

    Order (P3/P4-B amendment): llama.cpp (managed, OpenAI-compatible) → LM Studio →
    Ollama. ``backend="env"`` is an alias for the llama.cpp candidate — it was the
    name of the bare ``VALO_LLM_URL`` probe before that candidate existed.
    """
    env = os.environ if environ is None else environ
    wanted = (backend or "auto").strip().lower() or "auto"
    wanted = BACKEND_ALIASES.get(wanted, wanted)
    if wanted not in ("auto", "llamacpp", "lmstudio", "ollama"):
        raise ReviewError(f"unknown backend {backend!r}; expected auto|llamacpp|lmstudio|ollama", 400)

    lmstudio = (env.get("VALO_LMSTUDIO_URL") or "").strip() or LMSTUDIO_DEFAULT
    ollama = (env.get("VALO_OLLAMA_URL") or "").strip() or OLLAMA_DEFAULT

    candidates: list[dict] = []
    if wanted in ("auto", "llamacpp"):
        candidate = _llamacpp_candidate(env)
        if candidate is not None:
            candidates.append(candidate)
        elif wanted == "llamacpp":
            raise ReviewError(
                "backend 'llamacpp' requested but no llama.cpp endpoint is configured "
                "(set VALO_LLM_URL or provide VALO_LLM_KEY_FILE)", 400)
    if wanted in ("auto", "lmstudio"):
        candidates.append({"backend": "lmstudio", "kind": "openai", "base": lmstudio.rstrip("/"), "label": "lmstudio"})
    if wanted in ("auto", "ollama"):
        candidates.append({"backend": "ollama", "kind": "ollama", "base": ollama.rstrip("/"), "label": "ollama"})
    if not candidates:
        raise ReviewError(f"backend {wanted!r} requested but no endpoint is configured", 400)
    return candidates


def chat_url(candidate: dict) -> str:
    base = candidate["base"]
    if candidate["kind"] == "ollama":
        return base + "/api/chat"
    return base if base.endswith("/chat/completions") else base + "/chat/completions"


def models_url(candidate: dict) -> str:
    base = candidate["base"]
    if candidate["kind"] == "ollama":
        return base + "/api/tags"
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    return base + "/models"


async def _get_json(session, url: str, timeout: float, headers: dict | None = None):
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as response:
        text = await response.text()
        if response.status != 200:
            raise UpstreamError.from_response(response)
    try:
        return json.loads(text)
    except ValueError as exc:
        raise RuntimeError(f"GET {url} -> non-JSON body") from exc


async def _pick_model(session, candidate: dict, requested, timeout: float) -> str:
    """Requested model wins; else the candidate's pinned id when the server serves it;
    else the first id the server lists."""
    if isinstance(requested, str) and requested.strip():
        return requested.strip()
    data = await _get_json(session, models_url(candidate), timeout, headers=_auth_headers(candidate))
    if candidate["kind"] == "ollama":
        names = [item.get("name") or item.get("model") for item in (data.get("models") or []) if isinstance(item, dict)]
    else:
        names = [item.get("id") for item in (data.get("data") or []) if isinstance(item, dict)]
    names = [name for name in names if isinstance(name, str) and name]
    if not names:
        raise RuntimeError(f"no model is served at {models_url(candidate)}")
    pin = candidate.get("model")
    if isinstance(pin, str) and pin in names:
        return pin
    return names[0]


async def _chat(session, candidate: dict, model: str, messages: list[dict], timeout: float) -> str:
    url = chat_url(candidate)
    if candidate["kind"] == "ollama":
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False,
                                   "options": {"temperature": 0.2}}
    else:
        payload = {"model": model, "messages": messages, "temperature": 0.2, "stream": False}
    headers = _auth_headers(candidate)
    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout), headers=headers) as response:
        body = await response.text()
        if response.status != 200:
            raise UpstreamError.from_response(response)      # {code,status,request_id}; the body is dropped
    try:
        data = json.loads(body)
    except ValueError as exc:
        raise RuntimeError(f"POST {url} -> non-JSON envelope") from exc
    if not isinstance(data, dict):
        raise RuntimeError(f"POST {url} -> unexpected envelope type")
    if candidate["kind"] == "ollama":
        message = data.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        if content is None and isinstance(data.get("response"), str):
            content = data["response"]          # /api/generate shape, harmless fallback
    else:
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if content is None and isinstance(first, dict) and isinstance(first.get("text"), str):
            content = first["text"]
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError(f"POST {url} -> empty message content")
    return content


async def request_chat(messages: list[dict], *, backend=None, model=None,
                       timeout: float | None = None, environ=None,
                       parse=None, retry_instruction: str | None = None,
                       parse_noun: str = "JSON") -> dict:
    """Walk the probe list; return the first usable answer (raises NoLLMReachable).

    Shared with the P3 coach narration (``bot.replay.p3_coach``). ``parse`` validates
    the raw reply; when it returns None the same candidate is asked once more with
    ``retry_instruction`` before the candidate is abandoned. Return shape:
    ``{"content": <str, or the parsed value when ``parse`` is given>, "backend",
    "model", "tried", "retried"}``.
    """
    candidates = probe_candidates(backend, environ)
    limit = float(timeout) if timeout else _timeout(environ)
    tried: list[str] = []
    answered = False

    for candidate in candidates:
        label = f"{candidate['backend']} {chat_url(candidate)}"
        try:
            assert_local_url(candidate["base"])          # offline policy: loopback endpoints only
            if isinstance(model, str) and model.strip():
                reject_cloud_model(model)
            async with aiohttp.ClientSession() as session:
                chosen = await _pick_model(session, candidate, model, PROBE_TIMEOUT_S)
                reject_cloud_model(chosen)
                content = await _chat(session, candidate, chosen, messages, limit)
                answered = True
                retried = False
                if parse is not None:
                    parsed = parse(content)
                    if parsed is None and retry_instruction:
                        retried = True
                        retry_messages = messages + [
                            {"role": "assistant", "content": content[:2000]},
                            {"role": "user", "content": retry_instruction},
                        ]
                        content = await _chat(session, candidate, chosen, retry_messages, limit)
                        parsed = parse(content)
                    if parsed is None:
                        raise RuntimeError(
                            f"model answered but no {parse_noun} could be parsed (after 1 retry)")
                    content = parsed
            return {"content": content, "backend": candidate["backend"], "model": chosen,
                    "tried": tried, "retried": retried}
        except Exception as exc:  # noqa: BLE001 — any candidate failure just advances the probe
            reason = describe_failure(exc)
            tried.append(f"{label} :: {reason}")
            LOG.info("chat candidate %s failed: %s", label, reason)
    raise NoLLMReachable(tried, answered=answered)


async def request_review(digest_text: str, *, backend=None, model=None,
                         timeout: float | None = None, environ=None) -> dict:
    """P2 review over the shared probe walk; the first parseable review wins."""
    return await request_chat(
        build_messages(digest_text), backend=backend, model=model, timeout=timeout,
        environ=environ, parse=parse_review, retry_instruction=RETRY_INSTRUCTION,
        parse_noun="JSON review")


def _timeout(environ=None) -> float:
    env = os.environ if environ is None else environ
    try:
        value = float(env.get("VALO_LLM_TIMEOUT") or DEFAULT_TIMEOUT_S)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_S
    if value <= 0:
        return DEFAULT_TIMEOUT_S
    return min(value, DEFAULT_TIMEOUT_S)


# -------------------------------------------------------------------- routes


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": str(message)}, status=status)


def _cache(app: web.Application) -> dict:
    cache = app.get(CACHE_KEY)
    if not isinstance(cache, dict):
        cache = {}
        app[CACHE_KEY] = cache
    return cache


async def review_post(request: web.Request) -> web.Response:
    replay_id = request.match_info["replay_id"]
    try:
        store.replay_dir(replay_id)
    except ValueError:
        return _error("replay not found", 404)

    payload = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except Exception:
            return _error("request body must be JSON", 400)
        if body is None:
            body = {}
        if not isinstance(body, dict):
            return _error("request body must be a JSON object", 400)
        payload = body

    try:
        digest = build_digest(replay_id, payload.get("roundFrom"), payload.get("roundTo"))
    except ReviewError as exc:
        return _error(str(exc), exc.status)

    digest_text = render_digest(digest)
    try:
        result = await request_review(
            digest_text,
            backend=payload.get("backend"),
            model=payload.get("model"),
        )
    except ReviewError as exc:
        return _error(str(exc), exc.status)
    except NoLLMReachable as exc:
        envelope = {"error": "no local LLM reachable", "tried": exc.tried}
        if exc.answered:
            envelope["note"] = "at least one local endpoint answered but returned no parseable JSON review"
        return web.json_response(envelope, status=503)

    review = {
        "replayId": str(replay_id),
        "backend": result["backend"],
        "model": result["model"],
        "generatedAt": store.iso_now(),
        "summary": result["content"]["summary"],
        "findings": result["content"]["findings"],
        "digestBytes": len(digest_text.encode("utf-8")),
    }
    _cache(request.app)[str(replay_id)] = review
    return web.json_response(review)


async def review_get(request: web.Request) -> web.Response:
    replay_id = request.match_info["replay_id"]
    try:
        store.replay_dir(replay_id)
    except ValueError:
        return _error("replay not found", 404)
    cached = _cache(request.app).get(str(replay_id))
    if cached is None:
        return _error(f"no cached review for replay '{replay_id}'", 404)
    return web.json_response(cached)


def mount(app: web.Application) -> None:
    """Register the review routes (idempotent: a second call is a no-op)."""
    if app.get(MOUNTED_KEY):
        return
    app[MOUNTED_KEY] = True
    _cache(app)
    app.router.add_post("/api/replays/{replay_id}/review", review_post)
    app.router.add_get("/api/replays/{replay_id}/review", review_get)
    LOG.info("p2 review mounted (timeout=%.0fs, digest budget=%d B)", DEFAULT_TIMEOUT_S, DIGEST_BUDGET_BYTES)


# ----------------------------------------------------------------------- cli


def _main(argv: list[str]) -> int:
    import sys

    if len(argv) < 2:
        print("usage: py -3.12 -m bot.replay.p2_review <replay_id> [roundFrom] [roundTo]")
        return 2
    replay_id = argv[1]
    round_from = argv[2] if len(argv) > 2 else None
    round_to = argv[3] if len(argv) > 3 else None
    digest = build_digest(replay_id, round_from, round_to)
    text = render_digest(digest)
    sys.stdout.write(text)
    print(f"--- digest bytes: {len(text.encode('utf-8'))} (budget {DIGEST_BUDGET_BYTES})"
          f" rounds={len(digest['rounds'])} range={digest['roundFrom']}-{digest['roundTo']}")
    return 0


if __name__ == "__main__":  # pragma: no cover - manual evidence tool
    import sys

    raise SystemExit(_main(sys.argv))
