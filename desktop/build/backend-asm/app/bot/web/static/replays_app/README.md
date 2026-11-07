# ValoHub replay client

Vanilla HTML/CSS/JavaScript. No build step, package install, or CDN scripts.

## With the local aiohttp app

On Windows, double-click `run_bot.bat` in the repository root: it checks Python 3.12/aiohttp, prints non-fatal first-run diagnostics, and opens on `http://127.0.0.1:18791/replays` (unless `PORT` is set). Alternatively, start `py -3.12 -m bot.web` manually (default port 8787) and open the port printed by the app. Import a `.vrf` in the library, then open a ready/timeline replay. On a new clone, the API library is empty; the separate **synthetic demo** card still works without `data/` or the .NET decoder. The backend owns the `/api/replays`, `/api/jobs/stream`, and `/static/replays_app/` routes; this folder does not decode replays.

## Demo without backend

From repository root, run `py -3.12 -m http.server 8765`, then open `http://localhost:8765/bot/web/static/replays_app/view.html?demo=1`. The packaged synthetic fixture is in `bot/web/static/replays_app/demo/` (match and two rounds); no upload/API or ignored `data/` is required. The library page at `/bot/web/static/replays_app/index.html` shows the demo card even if the API is missing. A plain file:// URL cannot use fetch; serve over HTTP.

Use Space to play/pause, left/right arrows to seek five seconds, `[`/`]` to switch rounds; scrub to a moment, then click “Share this moment” to copy a URL with `#r=<round>&t=<seconds>`. Missing/zero map multipliers result in a fit-to-image background and a warning, with positions hidden rather than invented.

## Agent art and `index.json` v2 (viewer contract)

Agent art is local. Fetch it once with `py -3.12 tools/fetch_agent_assets.py` (`--check` reports drift and writes nothing; see the repo `README.md`, "Agent art"). The viewer reads `assets/agents/index.json` from `window.replayAssetBase`:

- **Shape.** A flat object keyed by agent display name, plus one reserved key `_meta` (`{schema: "valoplant_agent_assets/v2", apiVersion, generatedAt, missing[]}`). Each agent entry carries `uuid`, `portrait` (96×96), `map` (64×64) and `abilities` (display name → icon path). 31 top-level keys: 30 agents + `_meta`.
- **`_meta` is ignored when iterating agents.** `viewer.js` only ever looks an agent up by name (`agentEntry()`), and that helper rejects any name starting with `_`, any non-object value and any inherited property. Code that walks the index keys must skip `_meta` too.
- **`map` is preferred, `portrait` is the fallback.** The marker layer tries `entry.map`, and on an image error `entry.portrait`; if both fail, or the agent has no entry, it draws initials. Each path must match `^agents/(map/)?[a-z0-9-]+\.png$` (paths are uuid-named, so `KAY/O` is safe). The roster uses `portrait` only; kill rows show the killer's first candidate (the map icon).
- **Failure is a state, not an error.** If `index.json` fails to load the index is `{}` and every marker shows initials. The viewer decides "missing art" from the absence of a usable path in the entry; it does not consult `_meta.missing`, which is an audit list. Art is never borrowed from another agent.
- **No remote agent art.** Agent icons are never requested from `media.valorant-api.com` (the QA harness asserts zero such requests on the viewer pages). The map image loads from `assets/maps/<uuid>.png` first; the remote `match.map.image` URL is used only as a fallback when the local file is absent or fails.
- The viewer looks art up by agent **name**, not by `match.json`'s `agentUuid`; older stores whose `agentUuid` is `null` get art the same way.

## Player markers

Each alive player is a 26 px circular portrait (sprite box 40 px, baked once per art/team/state/DPR, drawn with one `drawImage`). **Ring = team identity (Team A / Team B), not side.** Team ids are stable all match; ATK/DEF swap at halftime, so never read the ring as attacker/defender.

| Ring | Meaning | Pattern |
|---|---|---|
| Solid, `#65c9e8` | Team A (team id 0) | one 3 px band |
| Double, `#ff6874` | Team B (team id 1) | the 3 px band plus a second 2 px band outside a 2 px gap |
| Solid grey `#9aa5ab` | Unknown team | one 3 px band |

The ring colour alone is not enough on pale minimap patches (WCAG 1.4.11 asks for 3:1). Measured on the Haven minimap composite: Team A `#65c9e8` is 3.17:1 on the dark patch but **1.24:1** on the pale patch; Team B `#ff6874` is 2.14:1 / 1.83:1; grey 2.39:1 / 1.64:1. The dark keyline around the marker is 3.13:1 dark / 12.30:1 pale, so the marker *shape* passes on both. That is why team identity also rides the ring **pattern**, the roster and the labels, never colour alone. This is a recorded limitation, not a fix. The canvas `aria-label` is a computed sentence, updated on state change only, e.g. `Round 7, 1:03.7. Team A · DEF: 5 of 5 alive. Team B · ATK: 5 of 5 alive.` — "Team A · DEF" after the halftime swap is correct. The roster's `ATK`/`DEF` tag, by contrast, is derived from the team id (team 0 → `ATK`) and does not swap at halftime.

Marker states (the `state`, `ring` and `fallback` fields of `window.replayDebug().markers[]`):

| `state` | When | Drawn |
|---|---|---|
| `alive` | A position sample exists at the current time | Full sprite with ring; HP bar below it when `hp` is known; heading chevron (tip at r+10, base at r+2) when yaw is known |
| `dead` | The player's death time has passed, for the first 1.5 s | Portrait at 55 % alpha with a team-colour ✕ over it, ring at full colour; no HP bar, no chevron |
| `x` | More than 1.5 s after death, until the round ends | A dimmed (alpha 0.6) team-colour ✕ at the death spot only |

A player who is alive again for more than 2 s after a recorded death is treated as revived and drawn as `alive`. Death time comes from the latest `kill` event with that victim, else the first `alive == 0` sample.

Orthogonal flags:

| Flag | Values | Meaning |
|---|---|---|
| `ring` | `solid` / `double` / `neutral` | Team A / Team B / unknown team |
| `fallback` | `null` / `"initials"` | `"initials"` = art missing: same footprint, team-colour disc, up to 2 white letters (agent name, else player name, else id; `?` if none) with a dark outline. Also used for an unknown agent and after an image `onerror` |
| `displaced` (`alive` only) | `true` / `false` | Markers closer than 22 px are nudged apart by at most 6 px; a displaced marker gets a 1 px hairline and a 2 px dot at its true position (`trueX`/`trueY`) |

Labels: a label is placed in the first free slot around the marker (offsets ±29/±46 px) or omitted and logged. Three or more markers within 1.5 marker diameters collapse into one `+N` badge above the pile and their individual labels are omitted. The roster keeps identity either way.

**Rotation.** The **Rotate icons** checkbox (`#marker-rotate`, unchecked on every load, not persisted) rotates the portrait to the heading; the heading chevron stays drawn. The angle comes from projecting a point ahead of the player through `project()` and `atan2`, never from raw world yaw.

**Hit test.** Tooltips show for markers within 16 px of the pointer.

## QA hooks

`window.replayDebug()` returns a snapshot of the last drawn frame; call it from the devtools console while a round is open:

| Field | Meaning |
|---|---|
| `markers[]` | One row per drawn marker: `id`, `agent`, `team`, `state`, `x`, `y`, `ring`, `hasSprite`, `fallback`; `alive` rows also carry `trueX`, `trueY`, `displaced` |
| `rotation` | Whether the rotate checkbox is on |
| `sprites` | Number of cached sprites |
| `artReady` | `true` once portraits have settled (or the 2.5 s wait / 3.5 s hard stop elapsed); markers are not drawn before it |
| `skipMarkers`, `dpr` | The `?markers=off` flag; the baked device-pixel ratio (capped at 2) |
| `markerMs` | Up to the last 600 marker-pass durations in ms |
| `ariaLabel` | The canvas's current computed `aria-label` |
| `labels` | `{placed: [{id, team, name}], omitted: [{id, reason}]}` for the last frame |

`window.replayPerf` is the companion for perf and pixel checks: `measureMarkers(n)` (p50/p95/max over `n` redraws), `probeMarker({team, agent, angle, hp, name})` (paints one marker through the real sprite/HP-bar/chevron code and returns the RGBA), and `spriteStats()` (`count`, `scale`, `spriteScales`, `bytes`). URL flags for A/B runs: `?markers=off`, `?markers=nolabels`, `?markers=nudge-off`.

The visual-QA harness uses these hooks: `PORT=18793 CDP_PORT=9333 node VALOPLANT_WORK/qa/cdp_visual_qa.mjs` (reported 70 PASS / 0 FAIL, exit 0, by the marker-fix lane; not re-run for this document). Port 18793 is the harness's own default, deliberately not the app's 18791. Its screenshots land in `VALOPLANT_WORK/qa/screens/` (`p8-marker-on-map`, `p8-dead-state`, `p8-missing-art`, `p8-rotation-off`, `p8-rotation-on`, `p8-demo`); they were verified by pixel probe only and have not been visually inspected.

## Environment variables (server side — `py -3.12 -m bot.web`)

This client is static JS with no env vars of its own; everything below is read by the aiohttp
backend it talks to. Grep commands + hits are listed under each flag so this table stays
checkable against the code; re-run the same greps if the backend changes.

**Bind / lobby (`bot/web/__main__.py`, `bot/replay/p2_lobby.py`)**

| Var | Default | Effect |
|---|---|---|
| `HOST` | `127.0.0.1` | Bind address for `python -m bot.web`. Loopback (`127.0.0.1`/`localhost`/`::1`) binds local-only with no extra guard. Any other value also installs a middleware that 403s non-safe-method requests from non-loopback peers — except the lobby create/join/leave POSTs when `VALO_LOBBY=1` — and 403s non-loopback `GET /api/watcher/status`. `HOST` is also read by `_advertise_host()` to build the lobby guest URL: an explicit non-wildcard value is used as-is; `0.0.0.0` advertises the machine's real LAN IP (found via a UDP route probe) instead of the literal `0.0.0.0`. Any IPv6-unspecified spelling (`::`, `::0`, `0:0:0:0:0:0:0:0`) is rejected at startup with `ValueError` — use `0.0.0.0` for all IPv4 interfaces. |
| `PORT` | `8787` | Bind port for `python -m bot.web`; also the port written into advertised lobby guest URLs. |
| `VALO_LOBBY` | unset (off) | Gates every lobby route (`POST /api/lobby`, `GET /api/lobby/{code}`, `POST .../join`, `POST .../leave`, `WS /ws/lobby/{code}`). Must be the exact string `"1"`; anything else (unset, `0`, `true`) leaves them returning 404. Trusted-LAN only — no account auth, no encryption; see `bot/replay/p2_lobby.py` module docstring. |

Grep: `rg "VALO_LOBBY|HOST|PORT" bot/web/__main__.py bot/replay/p2_lobby.py` →
`__main__.py:39 host = os.getenv("HOST", "127.0.0.1")`;
`__main__.py:47 port = int(os.getenv("PORT", "8787"))`;
`p2_lobby.py:55 return os.getenv("VALO_LOBBY") == "1"`;
`p2_lobby.py:105 host = os.getenv("HOST", "127.0.0.1")`;
`p2_lobby.py:130 port = int(os.getenv("PORT", "8787"))`.

**Local demos watcher (`bot/replay/p2_watcher.py`)**

| Var | Default | Effect |
|---|---|---|
| `VALO_WATCHER` | unset (off) | Truthy values (`1`/`true`/`yes`/`on`, case-insensitive) auto-start the demos-folder poll loop on boot. Can also be toggled at runtime via `POST /api/watcher/toggle` (loopback-only once `HOST` is non-loopback). |
| `VALO_DEMOS_DIR` | unset → `%LOCALAPPDATA%\VALORANT\Saved\Demos` (or `~` if `LOCALAPPDATA` is unset) | Folder the **watcher** polls for new `.vrf` files. Distinct from `VALOHUB_DEMOS_DIR` below — set both if you relocate your Demos folder and want the watcher and the `/api/demos` picker to agree. |
| `VALO_AUTO_REQUEUE` | unset (off) | Truthy → on startup, once, queue each eligible failed transform/patch decode for the current parser commit (`auto_requeue()`), independent of `VALO_WATCHER`. |

Grep: `rg "VALO_WATCHER|VALO_DEMOS_DIR|VALO_AUTO_REQUEUE" bot/replay/p2_watcher.py` →
`:31 def _env_enabled(name): return os.getenv(name, "").strip().lower() in {"1","true","yes","on"}`;
`:36 override = os.getenv("VALO_DEMOS_DIR")`;
`:148 self.enabled = _env_enabled("VALO_WATCHER")`;
`:160 if _env_enabled("VALO_AUTO_REQUEUE"): await self.auto_requeue()`.

**Local demos picker (`bot/replay/api.py`)**

| Var | Default | Effect |
|---|---|---|
| `VALOHUB_DEMOS_DIR` | unset → same fallback as `VALO_DEMOS_DIR` above | Folder listed by `GET /api/demos` (the "import from local demos" picker used by `import-path`). The `path` field is only included for loopback callers (`_loopback_peer()`, `bot/web/peer.py`); non-loopback LAN peers get `name`/`size` only. |

Grep: `rg "VALOHUB_DEMOS_DIR|_loopback_peer" bot/replay/api.py` →
`:225 override = os.getenv("VALOHUB_DEMOS_DIR")`;
`:234 include_paths = _loopback_peer(request.remote)`;
`:242-243 if include_paths: item["path"] = str(path)`.

**Player identity (`bot/replay/p2_identity.py`)** — used by the viewer's identity panel

| Var | Default | Effect |
|---|---|---|
| `VALO_OFFLINE` | unset (off) | `"1"` forces identity lookups to report "offline mode" instead of calling HenrikDev; also makes a cached-but-not-`available:true` envelope get ignored. |
| `VALO_IDENTITY_SEED` | unset → inferred from header display names | Comma-separated `Name#Tag` Riot IDs used as identity seeds; operator/test escape hatch. |
| `VALO_IDENTITY_TIME` | unset → source `.vrf` file mtime | ISO-8601 or epoch override for the "replay start anchor" used to window the HenrikDev match-history lookup. |
| `VALO_HENRIK_REGION` | `na` | HenrikDev API region segment. |
| `VALO_HENRIK_BASE_URL` | `https://api.henrikdev.xyz` | Retargets the HenrikDev API base (tests point this at a local mock; usable operationally too). |
| `HENRIKDEV_TOKEN` | `""` (empty) | HenrikDev API token, read once in `bot/web/app.py:create_app()` into `app["henrik_token"]`. Without it, identity calls return a graceful "no token" result rather than failing hard. |

Grep: `rg "VALO_OFFLINE|VALO_IDENTITY_SEED|VALO_IDENTITY_TIME|VALO_HENRIK_REGION|VALO_HENRIK_BASE_URL" bot/replay/p2_identity.py; rg HENRIKDEV_TOKEN bot/web/app.py` →
`p2_identity.py:199 override = os.getenv("VALO_IDENTITY_TIME", "")`;
`:381 raw = os.getenv("VALO_IDENTITY_SEED", "")`;
`:450 if os.getenv("VALO_OFFLINE") == "1":`;
`:474 region = os.getenv("VALO_HENRIK_REGION", "na").strip() or "na"`;
`:475 base_url = os.getenv("VALO_HENRIK_BASE_URL", DEFAULT_BASE_URL).strip() or DEFAULT_BASE_URL` (`DEFAULT_BASE_URL = "https://api.henrikdev.xyz"`, `:87`);
`app.py:221 app["henrik_token"] = os.getenv("HENRIKDEV_TOKEN", "")`.

**Local LLM review and rules-first coach (`bot/replay/p2_review.py`, `p3_coach.py`)**

| Var | Default | Effect |
|---|---|---|
| `VALO_LLM_URL` | unset | First-priority local LLM endpoint (OpenAI-compatible `/chat/completions`) probed for `POST /api/replays/{id}/review`. |
| `VALO_LLM_KEY_FILE` | unset → managed local server descriptor | Optional explicit descriptor path for the llama.cpp candidate; required to send its key to a retargeted `VALO_LLM_URL`. Launcher checks do not open this file. |
| `VALO_LLM_MODEL` | `Qwen3.8-9B-Q6_K` | Preferred model id for llama.cpp; coach narration and review use the same candidate chain. |
| `VALO_LMSTUDIO_URL` | `http://127.0.0.1:1234/v1` | Second-priority candidate; override lets a hermetic test (or a relocated LM Studio) stand in. |
| `VALO_OLLAMA_URL` | `http://127.0.0.1:11434` | Third-priority candidate; same override pattern for Ollama. |
| `VALO_LLM_TIMEOUT` | `300` (seconds) | Per-request LLM call timeout. Can only shorten the 300 s cap, never extend it (`min(value, DEFAULT_TIMEOUT_S)`); invalid/missing values fall back to 300. |

The managed descriptor's API key is read only when a model request is made and sent to the descriptor's endpoint; retargeting with `VALO_LLM_URL` drops that key unless `VALO_LLM_KEY_FILE` explicitly names a descriptor. The key is never logged or persisted in replay output. Keep configured URLs on trusted loopback addresses. First-run diagnostics only test loopback socket availability, without reading any descriptor. If no model can be reached the personal coach stays rules-only (review requests can return 503).

Grep: `rg "VALO_LLM_URL|VALO_LLM_KEY_FILE|VALO_LLM_MODEL|VALO_LMSTUDIO_URL|VALO_OLLAMA_URL|VALO_LLM_TIMEOUT|_DEFAULT =" bot/replay/p2_review.py` → constants `DEFAULT_TIMEOUT_S`, `LMSTUDIO_DEFAULT`, `OLLAMA_DEFAULT`, `LLAMACPP_DEFAULT_MODEL`; `_llamacpp_candidate()` reads the managed descriptor/model; `probe_candidates()` adds LM Studio/Ollama; `_timeout()` clamps to 300 seconds.

Test-only, not read by the running app: `VALO_REVIEW_REAL_E2E` opts one hermetic review test into hitting a real local LLM endpoint (`bot/replay/test_p2_review.py:556`); it has no effect on `python -m bot.web`.

**Normalizer (`bot/replay/normalize.py`)** — run by the pipeline as a subprocess (`bot/replay/pipeline.py`), so it inherits the server's env

| Var | Default | Effect |
|---|---|---|
| `VALOHUB_NORMALIZED_AT` | unset → current UTC time | Overrides the `decode.normalizedAt` timestamp the normalizer writes into `match.json`, letting a run be pinned for byte-reproducible output (see the module docstring at `normalize.py:21`). The `--normalized-at` CLI flag takes precedence over this env var if both are set. |

Grep: `rg "VALOHUB_NORMALIZED_AT" bot/replay/normalize.py` →
`:1430 normalized_at = os.environ.get("VALOHUB_NORMALIZED_AT") or None`.

## Checks

`node --check bot/web/static/replays_app/library.js`
`node --check bot/web/static/replays_app/viewer.js`

From a repo-root HTTP server, request the two HTML pages, both scripts, CSS, and demo JSON. Match/round JSON follow `VALOPLANT_WORK/BUILD_CONTRACT.md` §§3–4. Agent and ability icons load from the local `assets/` tree (see "Agent art and `index.json` v2" above); the map image loads from `assets/maps/<uuid>.png`, with `https://media.valorant-api.com/` used only as a fallback for `map.image` when the local file is missing.
