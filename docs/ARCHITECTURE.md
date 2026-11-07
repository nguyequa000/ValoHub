# Architecture

Scope: the ValoHub desktop application - the Electron shell (`desktop/electron/`), the local
Python sidecar (`bot/sidecar.py`, `bot/web/desktop.py`) and the replay pipeline (`bot/replay/`).
The sidecar serves the application's own UI over loopback with a per-launch token; it is not a
public website and is never reachable off the machine.

## Pipeline

```
 .vrf file
    │  (copy in, never moved; id = sha256(file)[:16])
    ▼
 data/replays/<id>/source.vrf                 immutable original
    │
    ├─► container index (stdlib, no decoder)   bot/replay/container_index.py
    │     → data/replays/<id>/container/       metadata, indexed_events, round_outcomes
    │       always runs; works even on an undecodable patch (e.g. 11.07) and produces
    │       the timeline: round starts, deaths, plants, defuses, ults, side switch.
    │
    └─► packet decode (only if the patch's branch is registered)
          CliReader export --profile viewer      vendorValorantReplayParser/ (vendored,
          (dotnet10-sdk, async subprocess)         upstream-pinned .NET 10 CLI)
          → data/replays/<id>/raw/*.ndjson       manifest + movement.ndjson + events.ndjson
          │
          ▼
        normalize (pure Python, stdlib only)     bot/replay/normalize.py
          → data/replays/<id>/viewer/            match.json + rounds/rNN.json +
                                                  abilities.json (compact, per-round,
                                                  integer world coords, round-relative ms)
          │
          ▼
        utility tracker (pure Python)            bot/replay/utility_track.py
          → utility[] embedded in each            fed inside normalize's single
            viewer/rounds/rNN.json                  pass over events.ndjson.gz
          │
          ▼
        gzip raw/*.ndjson → *.ndjson.gz          sha256 round-trip verified before the
                                                  uncompressed originals are removed
    │
    ▼
 static app (bot/web/static/replays_app/) reads only data/replays/<id>/viewer/* (replay data) plus
                                          its own local assets/ tree (agent/ability/map art)
```

If the patch's branch isn't in the vendored decoder's registry, the pipeline stops after
the container-index stage and the replay is served as `timeline`-status — same pages, no
positions.

## Module map

Backend (`bot/replay/`):

| Module | Responsibility |
|---|---|
| `store.py` | Hash-keyed store under `data/replays/<id>/`; atomic manifest/JSON writes; dedupe. |
| `jobs.py` (via `pipeline.py`) | One-at-a-time job worker, SSE progress. |
| `pipeline.py` | Orchestrates container-index → decode → normalize → gzip; decode-density check. |
| `container_index.py` | Stdlib-only container/chunk-index reader — the no-decoder timeline. |
| `normalize.py` | CliReader NDJSON → compact per-round viewer JSON; side (attack/defense) derivation; emits match-level `utilityCounts`. |
| `utility_track.py` | Ability utility tracker: classifies, owns, traces flight/bounces/landings; emits `utility[]` into round JSON; `player_counts()` builds match-level `utilityCounts`. The catalog's top-level `states` list is flattened into `kind:"state"` rows (`ability`/`key` null) and skipped by `player_counts`. |
| `coach_metrics.py` | Per-player deterministic findings (`player_report`, `build_findings`, `resolve_ref`). |
| `p3_coach.py` | Coach API: "this is me", rules + optional LLM narration, grounding/validation. |
| `p2_analysis.py` | Heatmaps / analysis page data, reusing first-contact/trade logic over VRF events. |
| `p2_identity.py` | Optional HenrikDev identity join (Riot ID by map+time). |
| `p2_lobby.py` | LAN watch-together WebSocket sync. |
| `p2_watcher.py` | Demos-folder auto-import watcher. |
| `p2_review.py` | LLM probe chain (llama.cpp → LM Studio → Ollama) shared by the coach. |
| `p3_parser_admin.py` | "Update parser" endpoint/status, loopback-gated. |
| `tools/update_parser.py` | CLI updater: pull upstream, isolated candidate build, selftest/density guard, publish. |
| `tools/fetch_agent_assets.py` (repo-root `tools/`) | Agent art fetcher: valorant-api.com → map icon / portrait / ability tiers + `index.json` v2 + `agent_ids.json`. Idempotent; `--check` reports drift. |
| `agent_ids.json` | Agent display name ↔ lowercase uuid (30 agents), written by the fetcher, read once by `normalize.py`. |
| `api.py` / `p2.py` | Route registration / feature mounting (`FEATURES`). |

Frontend (`bot/web/static/replays_app/`, vanilla JS/CSS, no build step):

| File | Responsibility |
|---|---|
| `index.html` / `library.js` | Library page: cards, drag/drop + picker upload, SSE progress. |
| `view.html` / `viewer.js` | 2D canvas viewer: map transform, playback, trails, deaths, kill feed, spike, deep links; agent-portrait marker layer (pre-baked sprites, team ring, heading chevron, dead/missing-art states, `window.replayDebug()` / `window.replayPerf` QA hooks). |
| `utility.js` | Recorded utility layer: dashed flight paths, bounce ×, landing icons, effect areas, tooltips, click-to-seek. |
| `coach.js` | "This is me" + coach panel: severity, click-to-seek refs, rules/LLM state. |
| `lobby.js` / `lobby.css` | LAN watch-together client. |
| `decoder.css` | Parser-health / decoder-status panel styling. |
| `assets/` | Local, offline valorant-api.com art, written by `tools/fetch_agent_assets.py`: `agents/map/` (64×64), `agents/` (96×96), `abilities/` (≤128), `maps/`, `agents/index.json` v2. |
| `demo/` | Bundled synthetic fixture so the viewer renders with no backend/data. |

Server entry points (`bot/web/`): `__main__.py` (production entry, installs the
remote-guard middleware on any non-loopback bind), `app.py` (route wiring/dashboard),
`peer.py` (loopback/LAN peer classification shared by the guard and route-level checks).

## Agent art and marker pipeline

The asset stage runs outside the import pipeline: it is a one-time (re-runnable) fetch, not part
of decoding a replay. Command: `py -3.12 tools/fetch_agent_assets.py` (`--check` = drift report,
writes nothing, exit 1 on drift; `--force` ignores the journal; `--agents A,B` for a subset).

```
 valorant-api.com  GET /v1/agents?isPlayableCharacter=true   (30 agents)
    │              GET /v1/version                           (apiVersion 13.09.00.5777412)
    ▼
 fetch        6 workers, 3 attempts (0.6 s, 1.2 s backoff), 60 s timeout, project User-Agent
    ▼
 downscale    minimapPortrait → agents/map/<uuid>.png        64×64, bytes copied verbatim
 (Pillow)     displayIcon     → agents/<uuid>.png            96×96 LANCZOS, never upscaled
              ability icons   → abilities/<uuid>-<Slot>.png  long side capped at 128, never upscaled
    ▼
 index v2     agents/index.json  +  .asset_sources.json (source-sha256 journal)  +  bot/replay/agent_ids.json
    ▼
 viewer       viewer.js fetches index.json, preloads portraits, bakes marker sprites (see below)
```

Everything is keyed by agent **uuid**, because the display name `KAY/O` contains a slash.
Slots are `Ability1`, `Ability2`, `Grenade`, `Ultimate` (all four required for every agent)
and an optional `Passive`, kept only where the API publishes an icon. Measured on the first run
(`p8-b1-assets.md`): 190 planned jobs, 183 written, 7 optional passives absent; 30 map icons
(5,367–7,897 B), 30 portraits (15,062–22,572 B), 123 ability icons (120 combat + 3 passives, all
128×128); the whole `assets/` tree went from 5,799,414 B to 2,029,830 B. A second run wrote 0
files. Idempotency is a sha256 of the *source* bytes per output, stored in `.asset_sources.json`;
an output is skipped only if the file exists, the journal entry matches and the freshly
downloaded source still hashes the same. A required asset (portrait, map icon, four combat
icons) that cannot be written exits 1; a missing optional passive does not. Nothing is faked:
no placeholder file, no borrowed art.

### `index.json` v2

A flat object keyed by agent display name, plus one reserved key, `_meta`:

```json
{
  "_meta": {"schema": "valoplant_agent_assets/v2", "apiVersion": "13.09.00.5777412",
            "generatedAt": "…", "missing": [{"agent": "Fade", "uuid": "…", "key": "Fade|Passive",
                                              "reason": "…", "optional": true, "path": "…"}]},
  "KAY/O": {"uuid": "601dbbe7-…", "portrait": "agents/601dbbe7-….png",
            "map": "agents/map/601dbbe7-….png",
            "abilities": {"FRAG/ment": "abilities/601dbbe7-…-Grenade.png"}}
}
```

31 top-level keys = 30 agents + `_meta`; 120 combat ability entries; every path resolves on disk.
`generatedAt` only changes when the asset content changes. `bot/replay/test_agent_assets.py`
(16 tests) pins the contract from PNG IHDR headers, not file names, and `test_agent_ids.py`
(10 tests) pins the name ↔ uuid map. If an index entry has no `map` key, the fetcher recorded a
required failure in `_meta.missing`; the viewer then falls back to `portrait`.

### `agentUuid` flow

1. The fetcher writes `bot/replay/agent_ids.json`
   (`{"schema": "valoplant_agent_ids/v1", "apiVersion": …, "byName": {…}, "byUuid": {…}}`).
2. `normalize.py` loads it **once at import**. The load is soft: a missing or unreadable file
   gives an empty map and `agentUuid` stays `null`, so a fresh checkout still imports.
3. Each player in `match.json` gets `"agentUuid": AGENT_UUID_BY_NAME.get(agent)` when `agent`
   is set, else `null`. The `agent` name comes from `AGENT_BY_CODENAME` (30 codenames; `Paprika`
   = Hush was added for 13.09). The player dict's key set and order are unchanged.
4. Stores written before this change keep `agentUuid: null` until they are re-normalised; nothing
   back-fills them.

**The viewer does not read `agentUuid`.** It finds art by agent *name* (`assetIndex[player.agent]`)
and takes the uuid from the file paths in the index, so old stores (null `agentUuid`) get art
too. The field is written for consumers that want a stable id.

### Why marker sprites are pre-baked

A marker is a clipped circular portrait, a tint disc, a team ring and two keylines. Per-frame
clipping and sprite blits cost about the same at DPR 1 (≈0.10 ms for 10 markers, from a benchmark
that is no longer in the tree; treat as ±50 %), so the case for pre-baking is memory, sharpness and
no first-frame flash, not raw speed: a 1024×1024 portrait is 4,096 KB decoded (48 MB for the 12
portraits held when this was measured), for a 26 px marker. So the viewer:

- uses the 64×64 map icon as the marker source (96×96 portrait only as a fallback and for the
  roster);
- bakes each marker **once** into an offscreen canvas — sprite box 40 CSS px, at device
  resolution `min(devicePixelRatio, 2)` — keyed by (art URL + load state, team, state
  `alive`/`dead`/`x`, dpr), then promotes it to an `ImageBitmap`;
- per frame draws one `drawImage` per marker, then the heading chevron and HP bar (cheap
  paths). `ctx.clip` appears in the marker code only inside `bakeSprite`; there is no
  `shadowBlur` or `ctx.filter` in `viewer.js`;
- clears the cache in `resize()` and rebakes when the DPR changes, so sprites are always
  crisp at the current scale;
- never shows a half-loaded marker: markers are not drawn until the art settles (or 2.5 s, hard
  stop 3.5 s), and a sprite whose art is still loading is re-baked when it arrives.

Measured by the marker lane (`p8-c-markers.md`; headless Chrome, DPR 2, 10 markers; timer
resolution 0.1 ms, treat as ±50 %): the marker layer adds 0.2 ms p50 / 0.3 ms p95 per frame,
against a 1 ms budget. Sprite memory was 184,960 B for 10 sprites at the original 34 px box; the
box is now 40 px, so 10 sprites at DPR 2 are 256,000 B (derived: 80×80×4 B each, not re-measured).
Players who share an agent and team share a sprite.

## Decoder bump (pin → publish → rollback)

The vendored decoder moved from upstream `ebef830d` to `09a8e1e1d81c48840b8810f523f1595e539b7d55`
("support release 13.09 and decode Hush"). It was done in two staged lanes rather than by running
the updater end to end, because the updater does not run the vendor tests, does not back up the
identity files and does not write `parser-pin.txt`.

1. **Snapshot.** `VALOPLANT_WORK/rollback/<stamp>/` holds `parser-commit.txt`,
   `parser-overlay.patch`, `parser-pin.txt`, `parser-baseline.json` and a `SHA256SUMS`.
2. **Candidate.** Fetch and detached-checkout the target SHA, apply
   `parser-overlay.patch` to it (`git apply --check`, then apply), Release-build
   `src/CliReader/CliReader.csproj` with `dotnet10-sdk/dotnet.exe` into
   `VALOPLANT_WORK/pu/a/<attempt>/`. The overlay applied unmodified (sha256 `cba0b5b6…` unchanged,
   no re-roll) and the build had 0 errors, 0 warnings. `parser-pin.txt` is **not** touched at this
   stage.
3. **Vendor tests.** `dotnet10-sdk/dotnet.exe test <candidate>/ValorantReplayParser.sln -c Release`:
   3,285 passed / 9 skipped / 0 failed (previous baseline 3,197 / 9 / 0; the 9 skips are fixtures
   that need replays not on this machine).
4. **Density self-test** against the 13.06 Haven baseline: `validated`. This is a
   catastrophic-loss floor, not semantic proof.
5. **Decode comparison.** The same two `.vrf` files decoded with the live build and with the
   candidate. Movement streams are byte-identical (same SHA-256 and line count); the only
   difference is additive event rows:

   | Replay | Branch | Movement rows | Rounds | Event rows (old → new) | Diagnostics |
   |---|---|---:|---:|---:|---:|
   | Haven | 13.06 | 1,572,247 | 18 | 458,357 → 461,754 | 0 |
   | Pearl | 13.08 | 2,483,916 | 22 | 641,325 → 645,804 | 0 |

   No event type was removed or reshaped.
6. **Publish.** The updater's own `_publish_candidate` swaps the tree atomically, with
   `tested=[…]` carrying the existing records (13.06 `validated`, 13.08 `validated`, 11.07
   `failed`) and **no** 13.09 record. The pin is then rewritten by hand to the 40-hex SHA.
   The previous tree is kept at `VALOPLANT_WORK/pu/b/vendor-<UTCstamp>-<oldsha12>` (here
   `vendor-20261031T094704Z-ebef830d6b0e`, whose `CliReader.dll` hash matches the pre-publish
   record).
7. **Verify.** `store.build_state()` reads `verified` (recorded == live, `changed: []`);
   all six assembly hashes in `parser-commit.txt` were recomputed from the published binaries and
   match; the updater's dry run lists 27 registered branches, with `++Ares-Core+release-13.09` in
   `registeredWithoutReplayTest` and **not** in `replayTested`. Re-decoding both replays with
   the published tree reproduced step 5 exactly.
8. **Rollback.** Move the live `vendorValorantReplayParser/` aside, move the `pu/b/vendor-…`
   tree back, restore the four snapshot files into `VALOPLANT_WORK/`, check `SHA256SUMS`, and
   re-run `build_state()`; the paste-able one-liner is in `p8-a2-publish.md` §9. Keep the
   snapshot and the previous tree until the final gate passes.

**13.09 is registered + upstream-tested but not replay-proven locally.** No 13.09 `.vrf` exists
here. "Registered" means `ValorantSeededTransform13_09` is in the payload-transform registry
(exact-string match on `++Ares-Core+release-13.09`) and the 13.9 descriptor layouts are wired in.
"Upstream-tested" means the vendored suite passes on the pinned tree (+88 passing over the
previous baseline); the string `release-13.09` appears nowhere under the vendor `tests/`, so
there is no replay-level 13.09 test. Four 13.09 items are unreviewed offsets that only fire on a 13.09 stream:
the hard-coded `AresRoundResults` handle map and `RoundParticipantsInfos` 106 → 113, the new
`EAresItemSlot` slot (`Unnamed1309`, count 16 → 17), the equippable resolver names, and
`BombPlayerState.Ping`. **Patch 11.07 remains unsupported**: content-block framing still fails
at packet 0 (position 294 of 809 bits) and none of the 14 upstream commits touches framing.

Consequences already visible on current replays:

- The equippable renames change output on 13.06/13.08 wherever those paths occur
  (`"Compact Pistol"` → `"Bandit"`, Guardian path case `Dmr/` → `DMR/`, new Neon ultimate
  `"Overdrive"`); anything keyed on the old names changes.
- Stores record the decode fingerprint. Both real stores (Haven, Pearl) record commit
  `c3071816`, which already differed from the pre-publish pin, so they read `stale` and the 11.07
  store reads `missing`; the publish did not newly invalidate anything. Reprocessing both would
  cost about 91 s of decode, about 11.2 GB of transient scratch and about 461 MB of store
  rewrite; it was not performed.
- The vendored `global.json` requests SDK `10.0.301`, which is not installed; restore recovers
  and the build succeeds, but the noisy block is not a failure.

## Utility item schema

Each `utility[]` entry in `viewer/rounds/rNN.json` (schema recorded in the current stores: decode commit `c3071816`, manifest schema 9; the decoder pin has since moved to `09a8e1e1` and those stores read stale until reprocessed):

| Key | Meaning |
|---|---|
| `id` | Unique item id |
| `t` | Throw time (round-relative ms) |
| `endT` | Effect end time |
| `actor` | Owning player id (or `null` if unowned) |
| `agent` | Agent codename |
| `ability` | Ability name |
| `key` | Ability slot key (Q/E/C/X) |
| `kind` | `smoke`/`molly`/`flash`/`recon`/`cage`/`wall`/`trap`/`blast`/`nearsight`/`decay`/`teleport`/`tether`/`camera`/`drone`/`respawn`/`projectile`/`interrogate`/`weapon`/`unknown`/`state` |
| `route` | `"path"` (flight recorded) or `"landing"` (landing only) |
| `path` | `[[dt, x, y], …]` simplified flight samples |
| `bounces` | `[[dt, x, y], …]` detected bounce points |
| `land` | `{t, x, y}` landing point |
| `effect` | `{t0, t1, x, y, r, rSource, classPath}` observed area lifetime |
| `confidence` | `"owned"` or `"unowned"` |
| `classPath` | Raw actor class path |

`kind:"state"` marks a caster-side state actor (Reyna's Devour heal pool,
`GameObject_Vampire_Q_Heal_HealPool`): it is kept in `utility[]` as evidence but is not thrown or
placed utility, so `classify_class` returns `ability:null` and `key:null` for it. The catalog's
top-level `states` list names these tokens; `catalog_rows()` flattens them into `kind:"state"`
rows alongside `abilities` and `auxiliary`.

Match/round rendering reads only `viewer/*` files — utility data is embedded at normalize time, not fetched separately. The quality badge additionally reads the store-level `manifest.json` via `GET /api/replays/{id}/quality`.

## Match-level utility counts

`match.json` (schema `valoplant_match/v2`) carries one additive key, `utilityCounts`, built from the
same utility items that feed the round files:

```json
"utilityCounts": {
  "P1": {"agent": "Sova", "total": 7,
         "byKind": {"recon": 1, "blast": 2},
         "abilities": {"Recon Bolt": 1, "Shock Bolt": 2}}
}
```

`utility_track.player_counts(utility_items, player_list)` — called from `normalize.py` — emits one
row per match player, zeros included. Only items owned by a player (`actor` matching an `id`)
count, so `actor: null` / unowned items are ignored. `kind:"state"` items are skipped entirely — a
caster-side state actor is not thrown utility, so it adds nothing to `total`/`byKind`/`abilities`
even though it stays in `utility[]`. A null `kind`/`ability` still adds to `total` but is not keyed.

The two consumers read different sources: the viewer's action feed and timeline ticks read
`round.utility` from `viewer/rounds/rNN.json`, while the analysis page's per-player table reads
`match.utilityCounts`. Both UI paths filter `kind:"state"` out — `utility.js` skips it in the draw
loop, and `viewer.js` filters it in the feed and in the tick build. `match.utilityCounts` is
match-level **thrown utility** (state actors excluded; current stores: Pearl 323, Haven 359).
Stores without the key (including the bundled demo) hide the analysis section.

Accessibility: the utility feed rows are `<button>`s with an `aria-label`
(round · time · player · ability) and are the keyboard path to utility information and seeking;
the timeline tick strip is `aria-hidden`, so utility ticks are mouse targets (`tabindex="-1"`).
Roster role is also shown as `ATK`/`DEF` text, and tick shapes differ by type
(circle/rect/diamond/bar) in addition to colour.

## API surface (JSON)

| Route | Behaviour |
|---|---|
| `GET /api/replays` | List/filter/sort the store. |
| `PUT /api/replays/upload?name=` | Raw-stream upload → 202 + job id. |
| `GET /api/demos` | List `.vrf` files in the configured demos folder. |
| `POST /api/replays/import-path` | Copy-import a file already on disk. |
| `GET /api/replays/{id}` | `match.json` (overview). |
| `GET /api/replays/{id}/rounds/{n}` | Per-round viewer JSON (gzip). |
| `POST /api/replays/{id}/reprocess` | Re-run normalize/decode on an existing store entry. |
| `DELETE /api/replays/{id}` | Remove a store entry. |
| `GET /api/jobs`, `GET /api/jobs/stream` | Job list / SSE progress. |
| `GET /api/decoder` | Parser commit, supported branches, health. |
| `POST /api/decoder/update` | Trigger the updater (loopback-only; 403 on LAN). |
| `GET/PUT /api/replays/{id}/me` | Read/set "this is me" player slot (PUT is loopback-only). |
| `GET/POST /api/replays/{id}/coach` | Cached / fresh coach report (POST is loopback-only). |
| `/assets/*` | Cached valorant-api.com map/agent/ability art. |

All subprocess decode/build work runs via `asyncio.create_subprocess_exec`; CPU-bound
normalize work runs in an executor so the event loop stays responsive during a decode.

## Security posture

The production entry point (`bot/web/__main__.py`) installs a remote-guard middleware
whenever the app binds to anything other than `127.0.0.1`: non-loopback requests get `403`
on every mutating/control route (`/me` PUT, `/coach` POST, `/api/decoder/update`, watcher
status), while safe `GET`s stay open so a LAN guest can watch. Route-level loopback checks
exist as defense-in-depth on top of the middleware (see `COACH_GUIDE.md`/P4 and the P5
updater gate for the specific routes verified).
