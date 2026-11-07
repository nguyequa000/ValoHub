# AGENTS.md — AntiStrats Workspace Guide

> **Status note (Aug 2026):** This document is the authoritative orientation guide for the `AntiStrats` workspace. It describes the *intended* architecture, file inventory, conventions, data sources, commands, and workflows. Treat it as the spec when implementing or refactoring any component. If the described file does not yet exist on disk, this document is the source of truth for what it should be.

This is a complete orientation guide for any agent (human or AI) working in the `AntiStrats` workspace. It describes the purpose, architecture, file inventory, conventions, data sources, commands, and workflows used here.

---

## TL;DR

`AntiStrats` is a competitive Valorant anti-strat and scouting toolkit built for analysts in the VDC (Valorant Draft Circuit) Season 10 Expert league. It is **Windows-first, PowerShell-driven, and Python-augmented**.

- **Toolkit input identifiers:** Riot IDs (`Name#Tag`) and local VDC team-code aliases (e.g. `FV`, `L7`, `MEOW`). Upstream VDC uses database IDs internally and franchise slugs for routes.
- **Primary data source:** VDC.gg Next.js RSC payloads (franchise + player pages).
- **Customs data:** HenrikDev API (`/v3/matches` with `filter=custom`), gated by roster overlap.
- **Outputs:** Markdown scouting reports in `exports/`; optional styled DOCX briefings.
- **Two runtimes:** PowerShell collectors (`Invoke-*.ps1`) and a Discord bot (`bot/`) wrapping the same logic.
- **Riot competitive pool snapshot (verified through Patch 13.02):** Ascent, Breeze, Haven, Lotus, Split, Summit, Sunset. VDC veto eligibility must still be verified against its runtime `ControlPanel` configuration.

---

## 1. Project Purpose

`AntiStrats` is a competitive Valorant anti-strat and scouting toolkit built for analysts working inside the VDC (Valorant Draft Circuit) Season 10 Expert league. It combines:

- Web scraping of VDC.gg franchise and player pages.
- HenrikDev API integration for custom-match round-level data (plants, defuses, first contacts, retakes).
- Tracker.gg customs URLs for roster validation.
- PowerShell data-collection scripts that produce Markdown scouting reports.
- A Discord bot (`bot/`) that wraps the same logic behind slash commands.
- Hand-authored and agent-generated Markdown scout/anti-strat reports.
- A one-off DOCX exporter for polished briefing documents.

The workspace is Windows-first, PowerShell-driven, and Python-augmented. Riot IDs and VDC team-code aliases are the canonical user-facing inputs within this toolkit. Do not confuse those aliases with upstream VDC's internal database identifiers.

---

## 2. Repository Layout

```
AntiStrats/
├── AGENTS.md                          # This file
├── .agents/                           # Empty (reserved for agent configs)
├── .env                               # Local secrets (HENRIKDEV_API_KEY, etc.) — gitignored
├── .gitignore
├── .vscode/                           # Editor config
├── requirements.txt                   # Python deps: python-docx, requests, beautifulsoup4
├── run_bot.bat                        # Launcher: installs deps, validates .env, starts bot
│
├── Invoke-PostplantRetakeScout.ps1    # Primary PS data collector + report builder
├── Invoke-MapBanPrediction.ps1        # PS map veto prediction generator
├── build_meow_antistrat_docx.py       # MD → styled DOCX exporter (MEOW-specific sample)
│
├── bot/                               # Discord bot + Python services
│   ├── main.py                        # Bot entry point, slash command definitions
│   ├── dev.py                         # CLI dev tools (scout/veto/player/standings/test)
│   ├── README.md                      # Bot setup and command reference
│   ├── requirements.txt               # discord.py, aiohttp, python-dotenv
│   ├── .env / .env.example            # DISCORD_TOKEN, HENRIKDEV_TOKEN, GUILD_ID
│   ├── analyze_html.py                # VDC HTML structure probe
│   ├── debug_patterns.py              # VDC regex pattern debug
│   ├── debug_payloads.py              # RSC payload debug
│   ├── debug_vdc.py … debug_vdc5.py   # Iterative VDC parsing debug scripts
│   ├── test_full.py                   # Full parser test
│   ├── test_helpers.py / test_simple.py / test_patterns.py
│   ├── test_requests.py / test_rsc.py / test_rsc_parser.py
│   ├── test_next_data.py
│   ├── vdc_full.html / vdc_sample.html # Saved VDC HTML for offline testing
│   └── services/
│       ├── __init__.py                # Re-exports public API
│       ├── cache.py                   # Async in-memory cache with file persistence
│       ├── rate_limiter.py            # Token-bucket limiter per API (HenrikDev/VDC/Tracker)
│       ├── vdc_parser.py              # VDC Next.js RSC payload parser (primary)
│       └── vdc_rsc_parser.py          # Alternate HTML-based match array extractor
│
├── cache/                             # Runtime cache files (gitignored)
├── exports/                           # Generated reports (.md/.docx/.json) — gitignored
│   └── .gitkeep
│
└── *.md                               # Hand/team scouting reports (committed)
    ├── T1_Valorant_Analyst_Agent.md            # Analyst persona/prompt template
    ├── VDC_Independent_Scouting_Agent.md       # Independent collector agent protocol
    ├── VDC_Season10_Expert_Scouting_Dossier.md  # Consolidated S10 Expert scout notes
    ├── VDC_Season10_Expert_Missing_Team_Scouts.md
    ├── VDC_Season10_Expert_All_Teams_Standings.md
    ├── Exsick_VIKES_All_Matches_Scouting_Report.md
    ├── Quan_1560_All_Matches_Scouting_Report.md
    ├── FF_Friendly_Fire_Execution_AntiStrat_Report.md
    ├── FF_Friendly_Fire_Execution_Updated_Roster_Team_Analysis.md
    ├── L7_Lucky7_Blackjacks_Improvement_Report.md
    ├── L7_vs_FF_Split_VOD_Aware_Prep.md
    ├── MEOW_Tiger_Tacticians_Updated_AntiStrat_Report.md
    ├── NB_Night_Bloom_Eclipse_Team_Analysis.md
    ├── NB_Eclipse_Postplant_Retake_Exploit_Report.md
    └── WS_Equinox_Retake_Postplant_Protocols.md
```

### 2.1 Data flow at a glance

```
Tracker URL ──► Riot ID ──► HenrikDev /v3/matches (custom) ──┐
                                                              │
VDC franchise page ──► roster + recent maps ──┐                ├──► Analyze-CustomMatches
                                             ├──► Compare ───┘         │
VDC player pages ──► per-agent stats ────────┘                         │
                                                                      ▼
                                            PowerShell builder ──► exports/*.md ──► (optional) DOCX
                                                    ▲
                                                    │
                                            Discord bot / dev CLI wrap the same logic
```

---

## 3. Data Sources

### 3.1 VDC.gg (primary)
- Franchise page: `https://vdc.gg/franchises/<slug>?team=<tier>&view=<view>` → current-season roster, stats, maps, transactions, and recent/future matches. The upstream route always loads the current season from `ControlPanel`; a `season` query parameter is ignored. `team` selects an active tier tab and `view` accepts `overview`, `stats`, `maps`, or `transactions`.
- Player page: `https://vdc.gg/player/<encoded-riot-id>?season=<N>&type=season&by=summary` → season-scoped player data. At the audited revision, valid `type` values are `season` and `combine`; valid `by` values are `summary`, `agents`, `maps`, and `stats`; valid seasons run from the current season down through Season 6.
- Standings: `https://vdc.gg/standings?by=<franchises-or-tier>` → live current-season franchise or tier standings and optional playoff odds. The upstream page does not accept a historical `season` selector. `VDC_Season10_Expert_All_Teams_Standings.md` and `bot/main.py` are derivative snapshots, not the authority.

VDC is a Next.js app. Its data is embedded in `self.__next_f.push([1,"..."])` RSC streaming payloads. The Python parser (`vdc_parser.py`) decodes these payloads and uses bracket-depth JSON extraction. Both PS scripts also include regex fallbacks for older payload shapes.

### 3.2 HenrikDev API (customs)
- Endpoint: `https://api.henrikdev.xyz/valorant/v3/matches/<region>/<name>/<tag>?filter=custom&size=<limit>`
- Auth: `Authorization` header with `HENRIKDEV_API_KEY` from `.env` (PS) or `HENRIKDEV_TOKEN` (bot).
- Rate limits: 30 req/min enforced client-side by the bot's `rate_limiter`.
- Returns round-level data: `bomb_planted`, `plant_events.plant_site`, `bomb_defused`, `kill_events` (first contact by `kill_time_in_round`), `player_stats`, teams, metadata.

### 3.3 Tracker.gg
- Customs URL for a roster player is supplied as input (`-TrackerLink`).
- Used to extract the player Riot ID (`/riot/<name>%23<tag>` form) which then drives the HenrikDev pull.
- Direct scraping is not performed; HenrikDev is the actual data channel.

### 3.4 Riot Official — Competitive Map Pool
Riot competitive snapshot last verified August 9, 2026 against official patch notes through Patch 13.02:
- Pool: Ascent, Breeze, Haven, Lotus, Split, Summit, Sunset
- Patch 13.00, published June 23, 2026, added Summit and Sunset and removed Fracture and Pearl.
- Patch 12.08 had previously added Ascent and removed Bind.
- Patches 13.01 (July 14, 2026) and 13.02 (July 28, 2026) did not announce another Competitive pool change.
- Primary source: `https://playvalorant.com/en-us/news/game-updates/valorant-patch-notes-13-00/`

The Riot pool is contextual evidence, not the final VDC veto authority. Official VDC web and bot code load the league map pool and per-format ban order from database-backed `ControlPanel` values. Before generating a veto report, verify the pool exposed by the live VDC match/map-ban workflow and compare it with Riot's current pool. The hardcoded PS/Python mirrors must be treated as cached copies with a verification date.

### 3.5 Upstream Authority Order
When upstream sources disagree, use this order:
1. Live VDC Wiki (`https://wiki.vdc.gg/operations/rulebook`) for competition rules.
2. Live VDC match/map-ban behavior and runtime `ControlPanel` values for season, league state, map pool, and veto order.
3. `vdc-prisma/schema.prisma` for database models, relations, and enums.
4. `vdc-web` for public routes, accepted query parameters, UI behavior, and data loading.
5. `vdc-bot` for official Discord workflows and permissions.
6. Local snapshots and hardcoded constants only as dated derivative data.

The public `VDC-Operations-Rulebook` GitHub repository is archived. The public `vdc-common` repository had no commits at the audited revision and is not a source of constants or contracts.

---

## 4. VDC Team Directory

16 Expert teams. Codes are the toolkit's canonical command-line and bot input aliases. Upstream VDC routes use `Franchise.slug`, while relations use integer team/franchise IDs; the audited Prisma `Teams` model does not contain a `code` field.

| Code | Org | Team |
|---|---|---|
| 808 | 808 Angels | Dominion |
| ASH | Ashen Coven | Echoes |
| BS | Serious Business | Lunch Takers |
| CHA | Chaos Theory | Entropy |
| FF | Friendly Fire | Execution |
| FV | Final Verdict | Paragon |
| GRR | Grizzlies | Carnivora |
| INF | Inferno | Eternal |
| KC | Kingdom Coffee | Espresso |
| L7 | Lucky 7 | Blackjacks |
| LG | Light Garden | Purple Iris |
| MEOW | Catpocalypse | Tiger Tacticians |
| MF | The Mafia | Consiglieri |
| NAT | Natural 20 | Eldritch Knights |
| NB | Night Bloom | Eclipse |
| WS | Wolfstara | Equinox |

This directory is duplicated in `Invoke-PostplantRetakeScout.ps1`, `Invoke-MapBanPrediction.ps1`, `bot/services/vdc_parser.py`, `bot/services/vdc_rsc_parser.py`, and `bot/dev.py`. **Keep them in sync when teams change.** (See §6 Drift Targets.)

---

## 5. PowerShell Scripts

### 5.1 `Invoke-PostplantRetakeScout.ps1`
The primary collector. Two modes controlled by `-CollectionOnly`:
- **CollectionOnly** → emits a neutral research packet Markdown (evidence, no conclusions). Consumed by the `VDC_Independent_Scouting_Agent.md` protocol.
- **Default** → emits a full postplant/retake anti-strat report with player cards, map reads, phase diagnosis, exploit protocols, and final game plan.

Key parameters:
- `-TeamCode`, `-TeamName`, `-FullOrgName`, `-Tier`, `-Season`
- `-Players` (`Name#Tag` array, comma-separated)
- `-TrackerLink` (Tracker customs URL; required for HenrikDev pull)
- `-CustomMatchLimit` (default 25)
- `-MinimumVdcRosterPlayers` (default 2; gates HenrikDev usage)
- `-StopIfNewRoster` (block HenrikDev if roster match count is too low)
- `-Region` (default `na`, overridable via `.env` `VALORANT_REGION`)
- `-OutputStem` (filename stem; defaults to `<CODE>_<Team>_<suffix>`)

Pipeline:
1. `Import-DotEnv` reads `.env` (looks for `HENRIKDEV_API_KEY`).
2. Prompt for missing required values if not supplied as params.
3. `Resolve-TeamCode` validates/infers the VDC code.
4. `Fetch-VdcPage` GETs the franchise HTML and HTML-decodes the payload.
5. `Parse-VdcPlayerRows` extracts roster player stat rows (modern regex first, legacy fallback).
6. `Fetch-VdcPlayerReport` per player pulls the player page and `Parse-VdcPlayerAgentRows` extracts per-agent rows via balanced-array JSON extraction.
7. `Parse-VdcMapLog` extracts the "Recent Results" matches array.
8. `Compare-RosterToVdc` matches submitted Riot IDs to VDC roster (exact/normalized-exact/tag-only).
9. `Fetch-HenrikCustoms` pulls customs → `Analyze-CustomMatches` filters to competitive customs, validates against VDC GameID/score, computes postplant/retake rows and first-contact rows, and tracks roster overlap.
10. Analysis scope selection (`VDCVisibleFullRoster` > `VDCVisibleBasePlayers` > `TendencyOnlyBasePlayers` > `NoUsableCustoms`).
11. Builds Markdown via `Build-ResearchPacketMarkdown` (CollectionOnly) or `Build-ReportMarkdown` (full report).
12. Writes to `exports/<OutputStem>.md`.

Riot ID normalization (`Normalize-RiotId`): NFKC, non-breaking space fix, collapse whitespace, strip `Captain/Cap/IR/Sub` prefixes. Comparison uses lowercased name + comparable tag (numeric tags normalized).

### 5.2 `Invoke-MapBanPrediction.ps1`
Generates a veto prediction markdown for a single team. Key parameters:
- `-TeamCode`, `-TeamName`, `-FullOrgName`, `-Tier`, `-Season`
- `-YourBanMaps`, `-YourComfortMaps` (comma-separated)
- `-RecentMatchCount` (default 8, recency weighting window)
- `-OutputStem`

Pipeline:
1. Fetch VDC franchise page → `Parse-VdcMapLog`.
2. `Get-MapProfile` computes per-map: games, record, win rate, round diff, recent score, `StrengthScore` and `BanRiskScore` (weighted by wins/losses/roundDiff/recency/own-ban-penalty/own-comfort-bonus/sample-penalty), confidence label, read tag.
3. `Get-Predictions` ranks ban order by BanRiskScore, pick order by StrengthScore (excluding own bans), selects correlated maps with confidence %.
4. `Build-ReportMarkdown` publishes map profile table, ban/pick orders, three veto scripts (first ban / you ban best / you leave best), correlated map prediction, anti-team veto plan, and raw map log.
5. Writes to `exports/<OutputStem>.md`.

The bot's `map_veto` command runs this script twice (once per team) with swapped ban/comfort lists and looks for a matchup file `exports/<A>_vs_<B>_Current_Map_Pool_Ban_Prediction.md`.

---

## 6. Duplicated Constants — Drift Targets

The same facts are hardcoded in multiple places. When any of these change, update **all** listed locations in one commit.

| Constant | Locations |
|---|---|
| 16-team directory | `Invoke-PostplantRetakeScout.ps1`, `Invoke-MapBanPrediction.ps1`, `bot/services/vdc_parser.py`, `bot/services/vdc_rsc_parser.py`, `bot/dev.py`, §4 of this file |
| VDC veto-pool mirror (`CURRENT_POOL` / `KnownMaps`) | Runtime authority: VDC `ControlPanel.MAP_POOL`. Local mirrors: `Invoke-PostplantRetakeScout.ps1`, `Invoke-MapBanPrediction.ps1`, `bot/services/vdc_parser.py`, §14 of this file |
| Standings snapshot | `bot/main.py` (`/standings`), `VDC_Season10_Expert_All_Teams_Standings.md` |
| HenrikDev env var name | `.env` (`HENRIKDEV_API_KEY`, PS), `bot/.env` (`HENRIKDEV_TOKEN`, bot) |

---

## 7. Discord Bot (`bot/`)

### 7.1 Entry point — `bot/main.py`
`ValorantAnalystBot(commands.Bot)` with `command_prefix="!"` and slash command tree. Intent config: `message_content`, `guilds`, `members`. Loads `.env` via `python-dotenv`. `setup_hook` creates an `aiohttp.ClientSession` and syncs the command tree globally or to `GUILD_ID`. `close()` persists the cache.

Slash commands (registered via `@bot.tree.command`):

| Command | Description |
|---|---|
| `/scout_team` | Full team research packet via `Invoke-PostplantRetakeScout.ps1 -CollectionOnly`. Streams up to 3 chunks of the generated MD. |
| `/scout_player` | VDC player profile link embed. |
| `/map_veto` | Runs `Invoke-MapBanPrediction.ps1` for both teams and posts the matchup file. |
| `/postplant_retake` | Runs `Invoke-PostplantRetakeScout.ps1` full report mode. |
| `/henrik_matches` | Direct HenrikDev `/v3/matches` pull with custom filter, auth header, rate-limited. |
| `/list_teams` | Embed listing all 16 VDC team codes. |
| `/current_pool` | Embed with the locally mirrored pool. It must identify whether it represents Riot Competitive or the VDC league pool and include a verification date. |
| `/standings` | Hardcoded S10 Expert standings table. |
| `/help_analyst` | Command help embed. |
| `/quick_scout` | Direct VDC franchise parse via `parse_vdc_franchise_page` + `calculate_map_profiles`. Cached 10 min. |
| `/quick_veto` | Direct two-team veto prediction via `predict_veto`. Cached 10 min. |
| `/player_agents` | VDC player page parse for agent stats table. Cached 10 min. |

`run_powershell_script(script_name, args)` shells out via `subprocess.run` with `powershell -ExecutionPolicy Bypass -File`, 300-second timeout, `cwd=WORKSPACE`. The "heavy" commands (`/scout_team`, `/map_veto`, `/postplant_retake`) call PowerShell; the "quick" commands use the in-Python parsers directly.

Error handling: global `@bot.tree.error` handler for `CommandOnCooldown` and `MissingPermissions`, otherwise generic error text.

### 7.2 Services

- `cache.py`: `Cache` dataclass-based in-memory store with TTL, async lock, `persist`/`load` to `cache/cache.json`. `cached_fetch(key, fetch_fn, ttl)` and `cached(ttl, key_prefix)` decorator.
- `rate_limiter.py`: `RateLimit` token bucket per endpoint; `rate_limiter` singleton preconfigured for `henrikdev` (30/60), `vdc` (60/60), `tracker` (20/60).
- `vdc_parser.py`: primary VDC parser. `_extract_rsc_data` pulls `self.__next_f.push([1,"..."])` payloads and decodes escape sequences. `_find_json_in_rsc` uses bracket-depth extraction. Public functions: `parse_vdc_franchise_page`, `parse_vdc_player_page`, `calculate_map_profiles`, `predict_veto`, plus `TEAM_DIRECTORY`, `KNOWN_MAPS`, `CURRENT_POOL`, `normalize_map_name`.
- `vdc_rsc_parser.py`: alternate parser that scans for `"matches":` arrays directly in the raw HTML. Includes a `test()` coroutine for live verification against `https://vdc.gg/franchises/FV` and a `__main__` block.

### 7.3 Development CLI — `bot/dev.py`
`python -m bot.dev <command>` for offline testing without Discord:
- `scout <code> [--tier] [--season]` — print map profiles.
- `veto <team_a> <team_b> [--tier] [--season]` — print veto prediction.
- `player <riot_id> [--season]` — print agent table.
- `standings` — print standings table.
- `test` — invoke `test_helpers.main()`.

### 7.4 Bot environment (`.env`)
See §9 for the consolidated environment variable table.

---

## 8. Agent Prompts (Markdown)

### 8.1 `T1_Valorant_Analyst_Agent.md`
A persona/protocol for a "Tier 1 Valorant analyst". Phases:
1. Enemy map pool intelligence (VDC).
2. Player profiling (Tracker.gg + customs).
3. Correlated map round-by-round deep dive.
4. OSINT/stream research (Twitch/YouTube/Kick/social).
5. Counter-strat report + recommended composition.

Rules of engagement: source every claim, flag uncertainty, prefer recency (30 days), think like the opponent IGL, every observation maps to a counter.

### 8.2 `VDC_Independent_Scouting_Agent.md`
Analyst runbook that wraps `Invoke-PostplantRetakeScout.ps1 -CollectionOnly`. Workflow:
- Collect required inputs (code, names, tier, season, submitted Riot IDs, Tracker URL, region).
- Run the collector once, read the `exports/<CODE>_<Team>_Live_Research_Packet.md` fully.
- Perform targeted live verification (VDC franchise/player pages, Tracker account, map-pool verification) only when needed.
- Evidence rules (current-team vs season-wide, customs must overlap roster, no Deathmatch, missing data = unknown not 0%, plain-language confidence).
- Map ban prediction method: side-by-side matchup table, ban/pick logic, main + alternate veto scripts, BO2/BO3 format caveat, confidence %.
- Final report format (single-team assessment) and matchup veto report format (side-by-side).
- Quality checklist (every player covered, no mixed eras, no Deathmatch in conclusions, every weakness has a counter, maps use current pool, no-sample maps stay `unknown`).

---

## 9. Environment & Secrets

Secrets live in two `.env` files (both gitignored; never commit or log):

| Var | Where | Required | Purpose |
|---|---|---|---|
| `HENRIKDEV_API_KEY` | root `.env` | yes (PS scripts) | HenrikDev `Authorization` header |
| `HENRIKDEV_TOKEN` | `bot/.env` | yes (bot) | Same key, bot-side name |
| `DISCORD_TOKEN` | `bot/.env` | yes (bot) | Discord bot auth |
| `GUILD_ID` | `bot/.env` | optional | Restrict command sync to one guild (instant vs ~1h global) |
| `VALORANT_REGION` | `bot/.env` or root `.env` | optional (default `na`) | HenrikDev region segment |
| `VDC_RATE_LIMIT_DELAY` | `bot/.env` | optional | Scraping delay (advisory) |

`.env.example` and `bot/.env.example` are the templates. Note the two different names for the same HenrikDev key (`HENRIKDEV_API_KEY` vs `HENRIKDEV_TOKEN`) — this is intentional legacy naming, kept for compatibility.

---

## 10. Scouting Reports (root `*.md`)

These are human/agent-authored scout reports. They follow a consistent schema:
- Header: generated date, target (team code/org/team), VDC source URL.
- Executive read / current standing card.
- Roster read table (player, rating, ACS, K-D-A, KD, role, scout read).
- Map read (per-map context).
- Anti-strat / counter plan.
- For custom-supported reports: phase diagnosis (postplant %, retake %, defuse losses), per-player Henrik correlation, exploit protocols.

Examples by team:
- `FF_Friendly_Fire_Execution_AntiStrat_Report.md` — top-heavy team read; deny SpykePrime first contact.
- `NB_Eclipse_Postplant_Retake_Exploit_Report.md` — postplant 77% vs retake 25%; force retakes.
- `WS_Equinox_Retake_Postplant_Protocols.md` — Ascent/Lotus defensive retake leak; role-by-role protocol.
- `MEOW_Tiger_Tacticians_Updated_AntiStrat_Report.md` — full updated dossier (also used as the DOCX source).
- `Exsick_VIKES_All_Matches_Scouting_Report.md` and `Quan_1560_All_Matches_Scouting_Report.md` — player-level all-matches reads (separates WS/L7-only rows from prior-team rows).
- `VDC_Season10_Expert_Scouting_Dossier.md` / `VDC_Season10_Expert_Missing_Team_Scouts.md` — consolidated per-team briefs covering all 16 Expert teams.
- `VDC_Season10_Expert_All_Teams_Standings.md` — standings snapshot and coverage gap tracker.
- `L7_vs_FF_Split_VOD_Aware_Prep.md` — matchup prep document.

---

## 11. DOCX Exporter — `build_meow_antistrat_docx.py`

One-off script that converts `MEOW_Tiger_Tacticians_Updated_AntiStrat_Report.md` into a styled `.docx` at `exports/MEOW_Tiger_Tacticians_Updated_AntiStrat_Report.docx`. Uses `python-docx` with:
- Custom styles: `Normal` (Aptos 9.5pt), `Title`, `Heading 1/2/3`, `Report Meta`.
- Header text "VDC SEASON 10 EXPERT • UPDATED 5 AUGUST 2026" and footer page numbers.
- Markdown table parsing → `Table Grid` with shaded navy header row, light-blue alternating rows, repeated header on page breaks.
- Inline parser supporting `**bold**` and bare URLs (rendered as hyperlinks).
- Page breaks before `Current Map Profile`, `Attack Plan Against MEOW Defense`, `Sources`.
- Document core properties set (title/subject/author/keywords).

This script is a template. To export another team's report, duplicate it with a new `SOURCE`/`OUTPUT` and adjust the header/footer strings. **Generalize before reuse on a second team.**

---

## 12. Common Workflows

### 12.1 Prerequisites (one-time)
- Windows 10/11 with PowerShell 5.1+ (`powershell` on PATH).
- Python 3.11+ (`python` on PATH).
- `pip install -r requirements.txt` (root) and `pip install -r bot/requirements.txt` (bot).
- Populate root `.env` with `HENRIKDEV_API_KEY`; populate `bot/.env` with `DISCORD_TOKEN`, `HENRIKDEV_TOKEN` (optional `GUILD_ID`).

### 12.2 Generate a team research packet (CLI)
```powershell
.\Invoke-PostplantRetakeScout.ps1 `
  -TeamCode FV `
  -TeamName "Paragon" `
  -FullOrgName "Final Verdict" `
  -Tier expert `
  -Season 10 `
  -Players "Ptrang#0110","Sixto#lgacy","yearn#love","BayHarborGooner#Goonr","Portent#FV1" `
  -TrackerLink "https://tracker.gg/valorant/profile/riot/BayHarborGooner%23Goonr/customs" `
  -CustomMatchLimit 25 `
  -CollectionOnly `
  -OutputStem "FV_Paragon_Live_Research_Packet"
```
Output: `exports/FV_Paragon_Live_Research_Packet.md`.

### 12.3 Generate a postplant/retake anti-strat report
Same invocation without `-CollectionOnly`. Output suffix becomes `Postplant_Retake_Scout` if no `-OutputStem`.

### 12.4 Predict a single team's veto
```powershell
.\Invoke-MapBanPrediction.ps1 `
  -TeamCode L7 `
  -TeamName "Blackjacks" `
  -FullOrgName "Lucky 7" `
  -Tier expert `
  -Season 10 `
  -YourBanMaps "Haven" `
  -YourComfortMaps "Split" `
  -OutputStem "L7_Blackjacks_Map_Ban_Prediction"
```

### 12.5 Matchup veto (two teams)
Run the PS script twice (Team B with swapped ban/comfort), then either let the bot's `/map_veto` assemble `exports/<A>_vs_<B>_Current_Map_Pool_Ban_Prediction.md` or build the side-by-side table by hand following the method in `VDC_Independent_Scouting_Agent.md`.

### 12.6 Use the dev CLI
```powershell
python -m bot.dev scout FV
python -m bot.dev veto FV L7
python -m bot.dev player "Exsick#VIKES"
python -m bot.dev standings
python -m bot.dev test
```

### 12.7 Run the bot
```powershell
.\run_bot.bat
```
Ensures deps installed, `.env` present, then `python -m bot.main`.

### 12.8 Export a DOCX
```powershell
python build_meow_antistrat_docx.py
```
(Customize `SOURCE`/`OUTPUT` for other teams.)

---

## 13. Conventions and Rules

- **Riot ID format**: `Name#Tag`. All comparisons normalize to NFKC, strip `Captain/Cap/IR/Sub` prefixes, lowercase. Numeric tags are comparable as ints.
- **Map normalization**: case-insensitive match against `KNOWN_MAPS`; otherwise title-cased.
- **Evidence rules** (from `VDC_Independent_Scouting_Agent.md`):
  - Current VDC team results are team evidence.
  - Player pages are season-wide and may include prior-team games; warn when mixing eras.
  - Customs become team evidence only when roster overlap + VDC match identity are credible.
  - Deathmatch/Escalation/Swiftplay are not team evidence.
  - Missing custom rounds = unknown, never `0%`.
  - Prefer recent/current-roster evidence over older volume.
  - Confidence labels: high / medium / low / unknown.
  - Never invent a tendency to fill an empty section.
- **Map pool**: only maps in the current VDC runtime map pool are veto-eligible. Cross-check that pool against Riot's official Competitive pool and the live VDC rulebook, but do not let a hardcoded local list override the VDC match/map-ban workflow. If the sources disagree and the live VDC pool cannot be confirmed, stop the veto recommendation and report the uncertainty. Out-of-pool results are historical context only.
- **Exports**: `exports/` is gitignored except `.gitkeep`. Reports saved there as `.md` (and `.docx`/`.json` for raw Henrik payloads).
- **Secrets**: `.env` files (root and `bot/`) are gitignored. Required keys: `DISCORD_TOKEN`, `HENRIKDEV_TOKEN` (bot), `HENRIKDEV_API_KEY` (PS scripts). Never log or commit secrets.
- **Team directory drift**: the same 16-team dictionary is duplicated in 5 places (see §6). Update all of them when a team changes.
- **No comments in generated code**: follow the docstring/literal conventions of existing `bot/services` modules. The PS scripts use `#` comments; Python code uses docstrings and inline short comments.
- **Commit hygiene**: never commit `exports/`, `cache/`, `.env`, or `__pycache__/`.
- **Output format**: final captain reports are Markdown only; raw stat tables are not exposed in the final report. Use the section order defined in the agent protocols. Link VDC franchise page, every referenced player page, and the submitted Tracker page.

---

## 14. Map Pool Snapshot and VDC Authority

| | Maps |
|---|---|
| **Riot Competitive pool, verified through Patch 13.02** | Ascent, Breeze, Haven, Lotus, Split, Summit, Sunset |
| **Patch 13.00 changes (June 23, 2026)** | In: Summit, Sunset. Out: Fracture, Pearl. |
| **Prior Patch 12.08 change** | In: Ascent. Out: Bind. |

The former July 12, 2026 date for Patch 13.00 was incorrect; Riot published the notes on June 23, 2026. Patch 13.01 was published July 14 and Patch 13.02 on July 28, with no later Competitive pool change identified in those notes.

VDC can intentionally lag or freeze a league pool. The current Wiki rulebook defines a delayed adoption policy, while the official web and bot implementations obtain the actual pool and ban order from `ControlPanel`. Therefore:
- Use live VDC runtime configuration for veto eligibility and sequence.
- Use Riot patch notes to explain the broader Competitive context.
- Treat the Wiki's inline map list and every local hardcoded list as potentially stale snapshots.
- Record the verification date and source in every generated veto report.

---

## 15. Key Files to Read First

If you are an agent starting work here, read in this order:
1. `VDC_Independent_Scouting_Agent.md` — the operating protocol.
2. `T1_Valorant_Analyst_Agent.md` — the analytical philosophy.
3. `Invoke-PostplantRetakeScout.ps1` — the collector and report builder.
4. `bot/services/vdc_parser.py` — the Python VDC parser (mirrors the PS logic).
5. `bot/main.py` — the Discord command surface.
6. `VDC_Season10_Expert_All_Teams_Standings.md` — current standings snapshot.
7. One finished report (e.g., `NB_Eclipse_Postplant_Retake_Exploit_Report.md`) — the output style to match.

---

## 16. Testing and Verification

- No formal test runner (no pytest/CI). Tests live alongside `bot/` as `test_*.py` scripts and `debug_*.py` iterative probes against saved `vdc_sample.html` / `vdc_full.html`.
- `python -m bot.dev test` runs `test_helpers.main()`.
- Live validation: `python -m bot.dev scout FV` confirms the VDC parser works against the live site.
- HenrikDev health: `python -m bot.dev player "Exsick#VIKES"` exercises the player-page parser; the bot's `/henrik_matches` exercises the API end-to-end.
- The PS scripts throw on missing `.env` keys, missing team code, or empty VDC map log; use those failures as the smoke test.
- **Recommended smoke test before any report run:**
  ```powershell
  python -m bot.dev scout FV   # parser alive?
  python -m bot.dev test       # helpers alive?
  ```
  (If either errors, fix before invoking the heavy PS collectors.)

---

## 17. Known Limitations

- The VDC parser targets a specific Next.js RSC payload shape. VDC UI changes can break extraction; the `debug_vdc*.py` scripts capture the iterative repair history.
- The franchise page exposes current-season data only. Passing `season=<N>` does not provide a historical franchise view.
- HenrikDev customs quality depends on the Tracker Riot ID actually being on the roster; `MinimumVdcRosterPlayers` and `StopIfNewRoster` gate bad samples.
- Veto predictions are derived from visible map results, not confirmed ban/pick history. Treat them as prep, not ground truth.
- Standings in `bot/main.py` and `VDC_Season10_Expert_All_Teams_Standings.md` are a June 26 2026 snapshot; refresh before relying on ranks/records.
- VDC's effective map pool and veto orders are runtime database values and are not fully recoverable from public repository defaults alone.
- The public GitHub rulebook is archived; use the live VDC Wiki for current competition rules.
- The DOCX exporter is single-team-hardcoded; generalize before reuse.
- No CI. Verification is manual (see §16).

---

## 18. Glossary

| Term | Meaning |
|---|---|
| **Anti-strat** | Defensive preparation targeting an opponent's tendencies/protocols. |
| **Customs** | Custom (private) Valorant matches; the only HenrikDev match filter used here. |
| **GameID** | VDC's per-match identifier; used to cross-validate HenrikDev customs against VDC results. |
| **First contact** | First kill event in a round (by `kill_time_in_round`); proxy for aggressive peek outcomes. |
| **Postplant** | Phase after a Spike plant; used to assess attacker site-exec / retake-hold success. |
| **Retake** | Defender re-take of a planted site; the defensive counterpart to postplant. |
| **RSC payload** | React Server Components streaming chunks embedded in VDC HTML via `self.__next_f.push(...)`. |
| **Riot ID** | `Name#Tag` player identifier; normalized before all comparisons. |
| **Veto** | Map ban/pick phase; the bot/scripts model a predicted veto order, not actual history. |
| **VDC** | Valorant Draft Circuit; the league whose Season 10 Expert tier this toolkit targets. |

---

## 19. Error Handling Summary

| Layer | Behavior |
|---|---|
| PS scripts | Throw on missing `.env` key, unresolvable team code, or empty VDC map log. Errors surface as PowerShell terminating exceptions — check `$Error[0]`. |
| Bot slash commands | Global `@bot.tree.error` handler: `CommandOnCooldown` → cooldown message; `MissingPermissions` → permission message; otherwise generic error text. |
| `run_powershell_script` | 300-second `subprocess.run` timeout; non-zero exit surfaces as bot error text. |
| HenrikDev rate limit | Token bucket in `rate_limiter.py` blocks/yields; 30 req/min budget. |
| VDC fetch | No retry built in; a failed GET surfaces as empty parse and downstream throw. |
| Cache | Graceful: load failure on `cache/cache.json` is non-fatal (starts fresh); persist failure logs but does not crash. |

---

## 20. Upstream VDC Source Audit — Completed August 10, 2026

This audit compared the guide with the public repositories maintained by the [Valorant Draft Circuit GitHub organization](https://github.com/Valorant-Draft-Circuit), the live VDC Wiki rulebook, and Riot's official patch notes. The workspace contained only `AGENTS.md`, so this pass corrected the specification but could not patch or test the described application files.

### 20.1 Audited Revisions

| Source | Inspected revision/status |
|---|---|
| [`vdc-web`](https://github.com/Valorant-Draft-Circuit/vdc-web) | `84df3180404461b5f77a0d02fbb4f33c20858d78` (`main`, August 7, 2026) |
| [`vdc-prisma`](https://github.com/Valorant-Draft-Circuit/vdc-prisma) | `f3dd6d720991a3b78cf298edd6a8e55f22bf4f31` (`main`, July 31, 2026) |
| [`vdc-bot`](https://github.com/Valorant-Draft-Circuit/vdc-bot) | `8fc1cc54672ad650b81a8e6abcbf0503949c16e1` (`main`, July 27, 2026) |
| [`VDC-Operations-Rulebook`](https://github.com/Valorant-Draft-Circuit/VDC-Operations-Rulebook) | `3ee502138936832b38c663a2e690713a4baba8ba` (`main`, September 24, 2025); repository declares itself archived |
| [`.github`](https://github.com/Valorant-Draft-Circuit/.github) | `637915a1b7e5c187c7521e04a6ead43fa1cd3cba` (`main`, February 5, 2026) |
| [`vdc-common`](https://github.com/Valorant-Draft-Circuit/vdc-common) | Public `main` branch had no commits |
| [Live VDC Wiki rulebook](https://wiki.vdc.gg/operations/rulebook) | Version 7.1.0, dated March 31, 2026 |

All repository clones were shallow. The recorded commit is the exact audited tree, not a claim that older history was reviewed.

### 20.2 Verified Findings

1. **Franchise route is current-season only.** `vdc-web/app/franchises/[slug]/page.tsx` obtains the current season from `ControlPanel`, passes it to `getFranchiseDetailsBySlugCached`, and only reads `team` and `view` from the query. Existing collectors must not imply that `?season=<N>` selects historical franchise data.
2. **Player query semantics changed from this guide's prior assumptions.** `vdc-web/app/player/[player]/page.tsx` accepts `type=season|combine`, `by=summary|agents|maps|stats`, and seasons from the current season down through Season 6. The existing `type=season&by=summary` collector URL remains valid.
3. **Standings are current-season only.** `vdc-web/app/standings/page.tsx` and `components/standings/StandingsPanel.tsx` load the current `ControlPanel` season and use `by` only for franchise/tier selection.
4. **Upstream identifiers differ from toolkit aliases.** `vdc-prisma/schema.prisma` uses integer `Teams.id`, integer `Franchise.id`, `Franchise.slug`, string `User.id`, string account/provider IDs, and `Account.riotIGN`. The `Teams` model has no `code` field. Local team codes remain useful inputs but are not universal upstream primary keys.
5. **Schema enums must be copied exactly.** The audited schema includes tiers `RECRUIT`, `PROSPECT`, `APPRENTICE`, `EXPERT`, `MYTHIC`, and `MIXED`; match types `PRE_SEASON`, `BO2`, `BO3`, and `BO5`; game types `COMBINE`, `INVALID`, `PRE_SEASON`, `SEASON`, `FORFEIT`, and `PLAYOFF`; map-ban types `PICK`, `BAN`, `DISCARD`, and `DECIDER`.
6. **Map pool and veto order are runtime configuration.** `ControlPanel.getMapPool()` reads control ID 21; `getBanOrder()` reads control IDs 18-20 for BO2/BO3/BO5. Both `vdc-web` and `vdc-bot` split these database strings and use them to create `MapBans` rows. Static lobby-format arrays are defaults for that feature, not proof of the live league values.
7. **Current BO2 rulebook flow is explicit.** Wiki rule 6.2.4 defines Away Ban, Home Ban, Away Ban, Home Ban, Home Pick, Away Pick, with the final map ignored. The Wiki says any of the ten rostered match players may run and participate in map bans; audited web/bot checks are roster/team based rather than captain-only.
8. **The GitHub rulebook is historical.** Its README redirects readers elsewhere and its document is Version 7.0.1 dated September 10, 2025. The live Wiki is the current public rule source.
9. **`vdc-common` is not an authority at this revision.** The public repository exists but has no commits. Shared database access and enums are instead provided by `vdc-prisma`, as also described by the organization profile.
10. **The Wiki's inline map list can lag.** Rule 6.1 describes VDC's pool-adoption policy, but the actual official veto implementations consume the runtime `ControlPanel` pool. A report must confirm runtime behavior rather than copying the inline list.

### 20.3 Required Implementation Follow-up

When the application files are restored to this workspace:
1. Remove or clearly deprecate the franchise-page `season` argument in collectors and CLI commands; it does not select historical franchise data.
2. Update player-page validation and parser fixtures for `season|combine` and `summary|agents|maps|stats`.
3. Replace hardcoded veto eligibility with a single VDC-pool provider where practical. Keep a dated Riot pool only as a fallback/context value.
4. Label local team codes as aliases and preserve upstream IDs/slugs when parsing source records.
5. Replace hardcoded standings with live VDC data or add a prominent snapshot timestamp.
6. Add parser fixtures for the audited RSC shape before refactoring extraction.
7. Reconcile all drift targets in §6 in one change after the missing files are available.

### 20.4 Remaining Runtime Unknowns

- The exact live values of VDC `ControlPanel.MAP_POOL` and the BO2/BO3/BO5 ban-order rows are database state and are not committed in the public repositories.
- The local 16-team Expert directory could not be compared with the current VDC database from source alone.
- No local collector, bot, parser, or test file existed in this workspace, so no implementation smoke tests were possible.
- Next.js RSC payloads remain an unstable scraping surface even when route behavior is known from source.

---

## Changelog

| Date | Change |
|---|---|
| 2026-08-10 | Completed the upstream VDC source audit; corrected route/query semantics, identifier authority, Riot patch dates, VDC map-pool authority, rulebook status, and implementation follow-up requirements. |
| 2026-08-09 | Added §20 to preserve the scope, progress, constraints, and next steps of the upstream VDC source audit. |
| 2026-08-08 | Full revision: added TL;DR, data-flow diagram, drift-target table, env-var table, glossary, error-handling summary, prerequisites workflow; renumbered sections; fixed typos (`HENDERIKDEV`→`HENRIKDEV`, `inferes`→`infers`); dropped hardcoded line counts; added status note. |
| (initial) | First version of AGENTS.md. |
