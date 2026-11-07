"""P2 feature — HenrikDev identity join (card: default).

Contract: ``VALOPLANT_WORK/P2_CONTRACT.md`` §3.

Routes
------
    GET  /api/replays/{replay_id}/identity          always 200, §3 envelope
    POST /api/replays/{replay_id}/identity/refresh  same envelope, forces recompute

The envelope is ALWAYS 200 with the §3 schema::

    {"available": bool, "reason": str|null, "refreshedAt": iso8601,
     "players": [{"slot", "displayName", "riotId", "puuid", "confidence", "source"}]}

``players`` always lists the ``viewer/match.json`` roster (P1..P10 in slot order,
``displayName`` when the normalizer found one). Enrichment joins the replay
against HenrikDev custom matches:

1. **Seeds** — Riot IDs (``Name#Tag``) from env ``VALO_IDENTITY_SEED`` (comma
   separated, operator/test escape hatch) or from header display names that
   already carry a ``#tag``. Without a seed nothing can be queried.
2. **Fetch** — the app's EXISTING client pattern: same endpoint, response
   schema, ``Authorization`` header and global ``henrikdev`` rate limiter as
   ``bot/services/first_contact_analyzer.py`` (its ``_get_custom_matches`` is
   reused verbatim). ``VALO_HENRIK_BASE_URL`` retargets the host so the tests
   can stand a stdlib mock in front of it (default ``https://api.henrikdev.xyz``).
3. **Candidate match** — same map (display name or map-folder alias, e.g.
   Haven/Triad) and ``game_start`` within ±15 min of the replay start anchor
   (env ``VALO_IDENTITY_TIME`` as ISO-8601/epoch, else the source ``.vrf``
   mtime — a documented approximation; the store holds no absolute match
   timestamp). Closest candidate wins.
4. **Slot join** — replay team agent multisets are paired with the candidate's
   Red/Blue agent multisets (both orders tried); within a paired team a replay
   slot matches iff its agent identifies exactly one HenrikDev player. When
   the header name is present and disagrees with the candidate name the slot
   stays unmatched (never guess).

Matched → ``confidence:"matched"`` with ``riotId``/``puuid`` and
``source:"henrikdev"``; everything else stays ``unmatched`` / ``source:"header"``.

Caching: lazy compute on the first GET, cached atomically to
``data/replays/<id>/viewer/identity.json`` (only when the replay dir exists);
the cache is served while it is not older than ``match.json``. Only the refresh
route recomputes unconditionally. Environment- or upstream-dependent results
(``offline mode``, ``henrikdev token not configured``, request failures and
timeouts, computation failures) are never persisted and are never served from a
cache file. Under ``VALO_OFFLINE=1`` a cached envelope that is not
``available:true`` is also ignored, so an offline boot answers exactly
``offline mode`` without overwriting the cache; a cached ``available:true``
result is still served. ``match.json`` and ``rounds/`` are read-only and
nothing is ever read from ``raw/``.

Graceful ALWAYS: offline mode (``VALO_OFFLINE=1``), missing token, missing
seed, unknown start time, network failure and no candidate all yield
``available:false`` + a human reason — never an exception, never a 5xx. A
non-existent replay id also yields a 200 envelope (the reason carries the
"not found" state) because the contract says the route is ALWAYS 200.

Security: the token comes from ``request.app["henrik_token"]`` / env only, is
sent solely in the ``Authorization`` header, and is never logged or persisted.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections import Counter
from datetime import datetime
from urllib.parse import quote

import aiohttp
from aiohttp import web

from . import store

try:  # reuse the app's existing HenrikDev client + rate limiter
    from bot.services import rate_limiter as _rate_limiter
    from bot.services.first_contact_analyzer import _get_custom_matches as _client_get_matches
except Exception:  # noqa: BLE001 — identity must degrade gracefully, never break boot
    _rate_limiter = None
    _client_get_matches = None

LOG = logging.getLogger("bot.replay.p2_identity")

DEFAULT_BASE_URL = "https://api.henrikdev.xyz"
CUSTOM_MATCH_SIZE = 10            # `size` used per seed query
MATCH_WINDOW_SECONDS = 900        # contract: ±15 min
MAX_SEEDS = 2                     # at most two upstream calls per compute
COMPUTE_TIMEOUT_S = 50.0          # hard cap on one endpoint request
OWNED_TIMEOUT_S = 20.0            # session we create when the app has none
USER_AGENT = "AntiStratsBot/1.0"

_SLOT_RE = re.compile(r"(\d+)\s*$")


# ------------------------------------------------------------------ helpers

class _Envelope(dict):
    """§3 envelope dict with an in-process ``transient`` flag.

    ``transient`` marks results that depend on the runtime environment
    (``VALO_OFFLINE``, token presence) or on an upstream request outcome
    (failure/timeout, computation failure). Transient envelopes are never
    persisted to ``viewer/identity.json`` and never served from an older
    cache. The flag is a Python attribute, not a key, so the four §3 keys
    stay exact and nothing can leak into the JSON.
    """

    transient = False


_TRANSIENT_REASONS = ("offline mode", "henrikdev token not configured")
_TRANSIENT_REASON_PREFIXES = (
    "henrikdev request failed:",
    "henrikdev request timed out",
    "identity computation failed:",
    "identity handler failed:",
)


def _is_transient(data):
    """True for env/upstream-dependent envelopes — never cached, never re-served.

    Recognizes the in-process ``transient`` attribute, an in-file
    ``"transient": true`` marker (caches written by other builds), and the
    reason strings those results carry.
    """
    if not isinstance(data, dict):
        return False
    if getattr(data, "transient", False) or data.get("transient") is True:
        return True
    reason = data.get("reason")
    return isinstance(reason, str) and (
        reason in _TRANSIENT_REASONS
        or reason.startswith(_TRANSIENT_REASON_PREFIXES))


def _result(available, reason, refreshed_at, players, transient=False):
    envelope = _Envelope(available=bool(available), reason=reason,
                         refreshedAt=refreshed_at, players=players)
    envelope.transient = bool(transient)
    return envelope


def _norm(value):
    """Casefolded stripped string, or None for anything unusable."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value.casefold() if value else None


def _slot_key(slot):
    match = _SLOT_RE.search(slot or "")
    return (int(match.group(1)) if match else 10 ** 6, slot or "")


def _split_riot_id(value):
    """('name', 'tag') for ``Name#Tag``; None otherwise (needs both parts)."""
    if not isinstance(value, str):
        return None
    name, sep, tag = value.partition("#")
    name, tag = name.strip(), tag.strip()
    return (name, tag) if sep and name and tag else None


def _epoch(value):
    """Epoch seconds from an int/float/ISO-8601 string; None when unusable."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _metadata_epoch(metadata):
    meta = metadata if isinstance(metadata, dict) else {}
    for key in ("game_start", "game_start_patched"):
        value = _epoch(meta.get(key))
        if value is not None:
            return value
    return None


def _start_anchor(manifest):
    """Replay start anchor: VALO_IDENTITY_TIME override, else source .vrf mtime."""
    override = os.getenv("VALO_IDENTITY_TIME", "")
    if override.strip():
        when = _epoch(override)
        if when is not None:
            return when
        LOG.warning("VALO_IDENTITY_TIME=%r is not ISO-8601/epoch; using source mtime", override)
    source = manifest.get("source") if isinstance(manifest, dict) else None
    if not isinstance(source, str) or not source:
        return None
    try:
        resolved = store.resolve_source(source).resolve()
        roots = {store.ROOT.resolve(), store.DATA_ROOT.resolve()}
        if resolved in roots or not any(root in resolved.parents for root in roots) or not resolved.is_file():
            return None
        return resolved.stat().st_mtime
    except OSError:
        return None


def _map_aliases(match, manifest):
    """Normalized map names a candidate may carry (display + folder alias)."""
    names = set()

    def add(value):
        norm = _norm(value)
        if norm:
            names.add(norm)

    raw_match_map = match.get("map")
    raw_man_map = manifest.get("map")
    match_map = raw_match_map if isinstance(raw_match_map, dict) else {}
    man_map = raw_man_map if isinstance(raw_man_map, dict) else {}
    for block in (match_map, man_map):
        add(block.get("name"))
        path = block.get("path")
        if isinstance(path, str) and path.strip():
            folder = path.rstrip("/").rsplit("/", 1)[-1]
            add(folder)
            add(store.MAP_NAMES.get(folder))
    return names


def _map_display_name(match):
    raw_block = match.get("map")
    block = raw_block if isinstance(raw_block, dict) else {}
    name = block.get("name")
    return name if isinstance(name, str) and name else "the replay map"


def _base_players(match):
    """Internal roster: sorted [{slot, name, team, agent}]; never guesses."""
    out = []
    players = match.get("players") if isinstance(match, dict) else None
    if not isinstance(players, list):
        return out
    for entry in players:
        if not isinstance(entry, dict):
            continue
        slot = entry.get("id")
        if not isinstance(slot, str) or not slot:
            continue
        team = entry.get("team")
        out.append({
            "slot": slot,
            "name": entry.get("name") if isinstance(entry.get("name"), str) else None,
            "team": team if team in (0, 1) else None,
            "agent": _norm(entry.get("agent")),
        })
    out.sort(key=lambda p: _slot_key(p["slot"]))
    return out


def _public_players(base, joined):
    """§3 players array: base roster enriched where the join proved a slot."""
    out = []
    for player in base:
        henrik = joined.get(player["slot"])
        if henrik is None:
            out.append({
                "slot": player["slot"],
                "displayName": player["name"],
                "riotId": None,
                "puuid": None,
                "confidence": "unmatched",
                "source": "header",
            })
            continue
        name = henrik.get("name") if isinstance(henrik.get("name"), str) else None
        tag = henrik.get("tag") if isinstance(henrik.get("tag"), str) else None
        puuid = henrik.get("puuid") if isinstance(henrik.get("puuid"), str) else None
        out.append({
            "slot": player["slot"],
            "displayName": player["name"],
            "riotId": f"{name}#{tag}" if name and tag else None,
            "puuid": puuid,
            "confidence": "matched",
            "source": "henrikdev",
        })
    return out


def _name_conflict(replay_name, henrik_name):
    """True when both names exist and disagree (name part of a Riot ID)."""
    if not isinstance(replay_name, str) or not replay_name.strip():
        return False
    left = replay_name.split("#", 1)[0].strip().casefold()
    right = henrik_name.strip().casefold() if isinstance(henrik_name, str) else ""
    return bool(left and right and left != right)


def _join_players(base, henrik_players):
    """Agent/team join; returns {slot: henrik_player} for unique identifications.

    Team pairing uses the agent multiset of each replay team against the
    candidate's Red/Blue multisets. When both pairings are plausible (or none
    is) the code falls back to matching only agents that are unique across the
    whole lobby; anything ambiguous or name-conflicting stays unmatched.
    """
    henrik_players = [p for p in henrik_players if isinstance(p, dict)]
    h_teams = {"red": [], "blue": []}
    for player in henrik_players:
        team = _norm(player.get("team"))
        agent = _norm(player.get("character"))
        if team in h_teams and agent:
            h_teams[team].append(agent)

    r_counts = {0: Counter(), 1: Counter()}
    for player in base:
        if player["team"] in (0, 1) and player["agent"]:
            r_counts[player["team"]][player["agent"]] += 1
    red, blue = Counter(h_teams["red"]), Counter(h_teams["blue"])

    if r_counts[0] == red and r_counts[1] == blue and r_counts[0] == blue and r_counts[1] == red:
        team_map = None                                  # both pairings plausible
    elif r_counts[0] == red and r_counts[1] == blue:
        team_map = {0: "red", 1: "blue"}
    elif r_counts[0] == blue and r_counts[1] == red:
        team_map = {0: "blue", 1: "red"}
    else:
        team_map = None                                  # no clean pairing

    joined = {}
    for player in base:
        agent = player["agent"]
        if not agent:
            continue
        if team_map is not None:
            candidates = [h for h in henrik_players
                          if _norm(h.get("character")) == agent
                          and _norm(h.get("team")) == team_map.get(player["team"])]
        else:
            candidates = [h for h in henrik_players if _norm(h.get("character")) == agent]
        if len(candidates) != 1:
            continue
        if _name_conflict(player["name"], candidates[0].get("name")):
            continue
        joined[player["slot"]] = candidates[0]
    return joined


def _find_candidate(matches, aliases, anchor):
    """Closest on-map custom match inside the ±15 min window; (match, checked)."""
    best, best_delta, checked = None, None, 0
    for entry in matches:
        if not isinstance(entry, dict):
            continue
        raw_meta = entry.get("metadata")
        metadata = raw_meta if isinstance(raw_meta, dict) else {}
        if _norm(metadata.get("map")) not in aliases:
            continue
        when = _metadata_epoch(metadata)
        if when is None:
            continue
        checked += 1
        delta = abs(when - anchor)
        if delta <= MATCH_WINDOW_SECONDS and (best_delta is None or delta < best_delta):
            best, best_delta = entry, delta
    return best, checked


def _seeds(base):
    """Riot-ID seeds: VALO_IDENTITY_SEED first, else header names with #TAG."""
    raw = os.getenv("VALO_IDENTITY_SEED", "")
    seeds = [chunk.strip() for chunk in raw.split(",") if _split_riot_id(chunk.strip())]
    if not seeds:
        seeds = [p["name"] for p in base if _split_riot_id(p["name"])]
    unique, seen = [], set()
    for seed in seeds:
        key = seed.casefold()
        if key not in seen:
            seen.add(key)
            unique.append(seed)
    return unique[:MAX_SEEDS]


# ------------------------------------------------------------------ fetching

async def _fetch_matches(session, seed, token, region, base_url):
    """One custom-match query using the app's existing client pattern."""
    if _client_get_matches is None:
        raise RuntimeError("henrikdev client unavailable")
    parts = _split_riot_id(seed)
    if parts is None:
        raise ValueError("seed is not a Riot ID (Name#Tag)")
    name, tag = parts
    url = ("%s/valorant/v3/matches/%s/%s/%s?filter=custom&size=%d"
           % (base_url.rstrip("/"), quote(region, safe=""), quote(name, safe=""),
              quote(tag, safe=""), CUSTOM_MATCH_SIZE))
    headers = {"Authorization": token, "User-Agent": USER_AGENT}
    if _rate_limiter is not None:
        await _rate_limiter.acquire("henrikdev")
    return await _client_get_matches(session, url, headers)


async def _fetch_with_session(session, seed, token, region, base_url):
    if session is not None:
        return await _fetch_matches(session, seed, token, region, base_url)
    timeout = aiohttp.ClientTimeout(total=OWNED_TIMEOUT_S)
    async with aiohttp.ClientSession(timeout=timeout) as owned:
        return await _fetch_matches(owned, seed, token, region, base_url)


# ------------------------------------------------------------------ compute

async def compute_identity(replay_id, *, token, session=None):
    """Compute (never raises) the §3 identity envelope for one replay."""
    refreshed_at = store.iso_now()
    try:
        return await _compute_identity(replay_id, token=token, session=session,
                                       refreshed_at=refreshed_at)
    except Exception as exc:  # noqa: BLE001 — graceful ALWAYS
        LOG.exception("identity compute failed for %r", replay_id)
        return _result(False, f"identity computation failed: {type(exc).__name__}",
                       refreshed_at, [], transient=True)


async def _compute_identity(replay_id, *, token, session, refreshed_at):
    try:
        rdir = store.replay_dir(replay_id)
    except ValueError:
        return _result(False, f"invalid replay id: {replay_id!r}", refreshed_at, [])

    match = await asyncio.to_thread(store.load_match, replay_id)
    manifest = await asyncio.to_thread(store.read_manifest, replay_id)

    if not rdir.is_dir():
        return _result(False, "replay not found in the store", refreshed_at, [])
    base = _base_players(match or {})
    base_view = _public_players(base, {})
    if match is None:
        return _result(False, "no normalized match data for this replay", refreshed_at, base_view)
    if os.getenv("VALO_OFFLINE") == "1":
        return _result(False, "offline mode", refreshed_at, base_view, transient=True)
    if not token:
        return _result(False, "henrikdev token not configured", refreshed_at, base_view,
                       transient=True)

    seeds = _seeds(base)
    if not seeds:
        named = sum(1 for p in base if p["name"])
        return _result(
            False,
            "no joinable Riot ID seed in replay data (%d player(s), %d display name(s), 0 with #TAG)"
            % (len(base), named),
            refreshed_at, base_view)

    aliases = _map_aliases(match, manifest or {})
    if not aliases:
        return _result(False, "replay map unknown", refreshed_at, base_view)
    anchor = _start_anchor(manifest or {})
    if anchor is None:
        return _result(False,
                       "replay start time unknown (no VALO_IDENTITY_TIME, no source .vrf mtime)",
                       refreshed_at, base_view)

    region = os.getenv("VALO_HENRIK_REGION", "na").strip() or "na"
    base_url = os.getenv("VALO_HENRIK_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL

    matches, failures = [], []
    for seed in seeds:
        try:
            fetched = await _fetch_with_session(session, seed, token, region, base_url)
        except Exception as exc:  # noqa: BLE001 — client/network failures degrade
            failures.append(f"{type(exc).__name__}: {exc}")
            continue
        if isinstance(fetched, list):
            matches.extend(fetched)
    if not matches:
        reason = ("henrikdev request failed: " + failures[0]) if failures \
            else "no custom matches returned for the seed(s)"
        return _result(False, reason, refreshed_at, base_view, transient=bool(failures))

    candidate, checked = _find_candidate(matches, aliases, anchor)
    if candidate is None:
        return _result(
            False,
            "no custom match on %s within ±15 min of the replay start (%d returned, %d time-checked)"
            % (_map_display_name(match), len(matches), checked),
            refreshed_at, base_view)

    raw_block = candidate.get("players")
    players_block = raw_block if isinstance(raw_block, dict) else {}
    henrik_players = players_block.get("all_players")
    if not isinstance(henrik_players, list):
        henrik_players = []
    joined = _join_players(base, henrik_players)
    public = _public_players(base, joined)
    if not joined:
        return _result(False, "candidate custom match found but no agent-consistent player mapping",
                       refreshed_at, public)
    LOG.info("identity: %s -> %d/%d slot(s) matched against a %s custom match",
             replay_id, len(joined), len(base), _map_display_name(match))
    return _result(True, None, refreshed_at, public)


# ------------------------------------------------------------------ cache

def _load_cached(replay_id):
    """Cached §3 envelope when it is at least as new as match.json, else None."""
    try:
        rdir = store.replay_dir(replay_id)
        cache = rdir / "viewer" / "identity.json"
        data = store.read_json(cache)
    except (ValueError, OSError):
        return None
    if (not isinstance(data, dict) or not isinstance(data.get("available"), bool)
            or not isinstance(data.get("players"), list)
            or "reason" not in data or not isinstance(data.get("refreshedAt"), str)):
        return None
    if _is_transient(data):
        return None                          # env/upstream negatives are never re-served
    try:
        cache_mtime = cache.stat().st_mtime
    except OSError:
        return None
    for source in (rdir / "viewer" / "match.json", rdir / "match.json"):
        if source.is_file():
            try:
                if cache_mtime < source.stat().st_mtime:
                    return None
            except OSError:
                return None
            break
    return data


def _store_cache(replay_id, data):
    """Best-effort atomic cache write; never touches anything but identity.json."""
    if _is_transient(data):
        return                               # env/upstream negatives are never persisted
    try:
        rdir = store.replay_dir(replay_id)
    except ValueError:
        return
    if not rdir.is_dir():
        return
    try:
        store.write_json_atomic(rdir / "viewer" / "identity.json", data)
    except OSError as exc:
        LOG.warning("identity cache write failed for %s: %s", replay_id, exc)


# ------------------------------------------------------------------ HTTP

def _json(data):
    return web.json_response(data, status=200, headers={"Cache-Control": "no-store"})


async def _identity_response(request, force):
    replay_id = request.match_info.get("replay_id", "")
    token = request.app.get("henrik_token") or ""
    offline = os.getenv("VALO_OFFLINE") == "1"
    try:
        data = None
        if not force:
            data = await asyncio.to_thread(_load_cached, replay_id)
            if offline and not (isinstance(data, dict) and data.get("available") is True):
                # §3: an offline boot is authoritative for non-positive results —
                # never serve a cached negative; the recompute below returns the
                # exact "offline mode" envelope.
                data = None
        if data is None:
            data = await asyncio.wait_for(
                compute_identity(replay_id, token=token, session=request.app.get("session")),
                timeout=COMPUTE_TIMEOUT_S)
            if not offline:
                await asyncio.to_thread(_store_cache, replay_id, data)
        return _json(data)
    except (asyncio.TimeoutError, TimeoutError):
        return _json(_result(False, "henrikdev request timed out", store.iso_now(), [],
                             transient=True))
    except Exception as exc:  # noqa: BLE001 — the route is ALWAYS 200
        LOG.exception("identity handler failed for %r", replay_id)
        return _json(_result(False, f"identity handler failed: {type(exc).__name__}",
                             store.iso_now(), [], transient=True))


async def api_identity(request):
    return await _identity_response(request, force=False)


async def api_identity_refresh(request):
    return await _identity_response(request, force=True)


def mount(app):
    """Register the identity routes (called by bot.replay.p2)."""
    app.router.add_get("/api/replays/{replay_id}/identity", api_identity)
    app.router.add_post("/api/replays/{replay_id}/identity/refresh", api_identity_refresh)
