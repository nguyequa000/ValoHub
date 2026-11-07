# ValoHub

ValoHub is a desktop replay reviewer for VALORANT. It imports your own `.vrf` match
replays, draws any round on a 2D minimap with player positions, utility and deaths, and
runs a rules-based self-review over the same data. It runs on Windows only, needs no
account, has no installer, and does not need the network to review.

The Windows package is a single portable zip: extract it, run `ValoHub.exe`, and your replays and
results stay under `%LOCALAPPDATA%\ValoHub` on that PC.

## Quick start

1. Build `ValoHub-0.1.0-win64.zip` using [docs/BUILD.md](docs/BUILD.md), or use a
   package supplied by its maintainer. Public release availability is not verified.
   Building currently requires maintainer-supplied inputs; a clean-clone build is unverified.
2. Extract the whole zip to a writable folder. Keep the folder contents together.
3. Run `ValoHub.exe`.

There is no installer, no setup step, no account and no code signing; Windows SmartScreen
may warn about the unsigned executable. The first window opens on the replay library. If
the library is empty, **Open demo** loads a bundled synthetic match so there is something
to look at before you import anything.

Optional self-check before first use — prints one JSON line and exits 0 when the packaged
backend and the data folder are usable, 2 when they are not:

```
ValoHub.exe --home <folder> --check
```

Windows 11 x64 is the tested target; Windows 10 may work but is untested. Python, .NET and
a browser are not needed on the target machine — the app ships its own runtime and decoder.

## What it does

- **Library.** Search, filters, sort, grid or list. Each replay is a card with a status:
  `ready`, `timeline`, `suspect`, `stale`, `processing` or `failed`.
- **2D viewer.** Agent portraits at recorded positions, movement trails, facing, death
  markers and kill feed, spike plant/defuse rings, round strip and a scrub bar.
- **Recorded utility.** Flight paths, bounce points, landings and effect areas drawn from
  what the replay contains, with layer toggles, a per-player filter and a team filter.
  Nothing is simulated or guessed.
- **Round bookmarks.** `B` or **Bookmark round**; the list lives in the Rounds tab and is
  stored per replay on this PC.
- **Share moment.** Writes a short text block (map, round, clock, replay id) with no
  server address, so it still makes sense later. The viewer URL hash `#r=<round>&t=<s>`
  deep-links to the same moment while the app is open.
- **Analysis.** Heatmaps on the real minimap (player positions, deaths, utility and
  ultimates), a findings table with player/team/round filters, **Export findings**
  (Markdown) and **Save heatmap** (PNG).
- **Coach.** Rules-first findings computed from your own round data, with an optional
  local model narrating the same findings. Works without a model.
- **AI providers.** Local servers on this PC are detected automatically. Cloud providers
  are available only after you enable that provider and consent to the host it uses.
- **Bundled demo.** A synthetic match that works with no imports and no decoder.

## Importing replays

Three ways, all on the library page or its menu:

1. Drag a `.vrf` file onto the library page.
2. Use the page's file picker (**Import**).
3. **File > Import replay…** (`Ctrl+O`).

The app **copies** the file into its own store; it never moves, edits or deletes your
original. Files are keyed by content hash, so importing the same replay twice does not
duplicate it. A full import of a supported replay takes roughly two minutes on the
development machine, and progress is shown while it runs.

Import runs in stages: container index (always, and the only stage a replay from an
unsupported patch reaches), packet decode (supported patches only), normalize, compress.
If the decode fails, the card shows `failed` with the decoder's message instead of a
half-built replay.

## Supported patches

| Patch | Status |
|---|---|
| 13.06 (Haven) | Replay-proven: imports `ready`, 18 rounds. |
| 13.08 (Pearl) | Replay-proven: imports `ready`, 22 rounds. |
| 13.09 | Registered in the decoder and upstream-tested, but **not proven on a real replay**. No 13.09 `.vrf` was available, so the 13.09-specific decode paths are unverified. Treat a 13.09 import as untested. |
| 11.07 | **Not decodable.** The container index still produces the timeline (rounds, deaths, plants, defuses, ultimates, side switch); positions are never shown. The import fails honestly. |

Any patch the decoder has no transform for behaves like 11.07: timeline only, or a
reported failure. VALORANT changes replay encryption on most patches, so a new patch
usually needs a new decoder build; the packaged app cannot update its decoder, so that
means a new release.

Each decoded replay also carries a quality state: `validated` (density matched the
baseline for that replay), `suspect` (decoded far less than expected) or `unvalidated`
(no baseline covered it). The package does not ship a density baseline, so imports made
in the app are `unvalidated` rather than presented as verified.

## Privacy and offline use

ValoHub renders its interface from a local backend that runs inside the app: the Electron
shell starts a hidden Python sidecar that listens on `127.0.0.1` with a random per-launch
token, and the window loads that loopback address. Opening the address in an ordinary
browser returns HTTP 403 — that is by design, not a fault.

- No account, no sign-in and no update check. In recorded runs the app's own code made no
  outbound request while reviewing.
- The window refuses every request that is not the local address, `data:` or `blob:`, and
  logs refusals to `logs\network-denied.jsonl`.
- Importing is a local copy to the local backend. Cloud narration, when enabled, sends
  an anonymized replay summary and coach instructions, not the replay file.
- The only outbound path is an AI provider you explicitly enable in **AI Providers**;
  each cloud provider needs its own consent record naming the host it may reach, and the
  replay file itself is never sent.

Offline behaviour was observed on one machine with connection sampling during imports. It
is strong evidence, not a firewall-isolated guarantee.

## Where your data lives

Default data folder: `%LOCALAPPDATA%\ValoHub`. Override it with `--home <folder>` (wins)
or the `VALOHUB_HOME` environment variable.

| Path | Contents |
|---|---|
| `data\replays\<id>\` | Normalized library, one folder per replay (`id` is a 16-character content hash). |
| `replays\*.vrf` | Copies of the replay files you imported. |
| `cache\` | Small caches; replays live elsewhere. |
| `logs\` | `electron.log` (rotating, 1 MB × 5) and `network-denied.jsonl`. |
| `electron\` | Electron profile: window state and local storage for bookmarks and preferences. |
| `VALOPLANT_WORK\` | Scratch and staging. The folder name is historical; it is not the product name. |

Delete the extracted program folder to remove the app, and the data folder to remove your
library. Your original `.vrf` files are never touched.

## Documentation

- [docs/USER-GUIDE.md](docs/USER-GUIDE.md) — first launch, importing, viewer keys,
  analysis, coach, providers, settings, data folder, troubleshooting, limits.
- [docs/BUILD.md](docs/BUILD.md) — prerequisites, one-time bootstrap, build command,
  outputs and verification.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — desktop version history (not a claim of published downloads).
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — pipeline and module map.
- [COACH_GUIDE.md](COACH_GUIDE.md) — coach rubric and the model prompt.

## Development

With the maintainer-only inputs described in [docs/BUILD.md](docs/BUILD.md), build
the portable app from the repository root:

```
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/build.ps1
```

The build needs Python 3.12 (`py -3.12`), Node 24 with npm, git, a .NET 10 SDK in
`dotnet10-sdk\`, a built `CliReader` and a parser build record — see
[docs/BUILD.md](docs/BUILD.md), which also lists the flags (`-Version`, `-SkipElectron`,
`-NoZip`, `-WheelHouse`, `-BundleDir`, `-AssetsSrc`, `-EmbedZip`, and the decoder source
overrides).

Tests:

```
py -3.12 -m pytest -q -p no:cacheprovider
cd desktop/electron; npm test
```

Riot artwork is not tracked or redistributed by this repository. Cache agent and
ability art locally with
`py -3.12 tools/fetch_agent_assets.py` (network and Pillow) before building if you want
portraits and ability icons; without it the viewer falls back to initials.

## Licenses

- The replay decoder (`vendorValorantReplayParser/`) is a pinned fork of
  [michel-giehl/ValorantReplayParser](https://github.com/michel-giehl/ValorantReplayParser),
  MIT licensed. Some per-patch transforms are ported from other MIT-licensed projects
  (`vrfkit`, `ValCoach`); see `vendorValorantReplayParser/THIRD-PARTY-NOTICES.md`.
- The 2D viewer derives from
  [talhakoek/ValorantWebReplayer](https://github.com/talhakoek/ValorantWebReplayer), MIT
  licensed; see [the viewer licence](bot/web/static/replays_app/LICENSE-ValorantWebReplayer).
- Agent, ability and map artwork comes from `valorant-api.com`, a third-party mirror of
  Riot-owned assets. It is used locally for personal, non-commercial viewing and is not
  tracked or redistributed by this repository. `tools/fetch_agent_assets.py` caches
  agent and ability art locally; locally built packages may include fetched art.
- The packaged app ships a generated `LICENSES\` folder beside `ValoHub.exe` with the
  third-party notices for the bundled runtime, decoder and Electron dependencies.

### Riot Games notice

ValoHub was created under Riot Games' "Legal Jibber Jabber" policy using assets owned by
Riot Games. Riot Games does not endorse or sponsor this project. The agent and map art is
Riot IP; this tool is personal, local and non-commercial. This repository does not track
or redistribute Riot art; locally built packages may contain locally fetched art.

The ValoHub source code is released under the MIT License; see [LICENSE](LICENSE).
That licence covers this repository's own code only. Riot Games assets are not included
and are not covered by it.
