# ValoHub user guide

ValoHub is a portable Windows app for reviewing your own VALORANT `.vrf` replays. This
guide covers the packaged app (0.1.0). Build instructions are in
[BUILD.md](BUILD.md); architecture notes in [ARCHITECTURE.md](ARCHITECTURE.md).

## 1. First launch

Extract the portable package zip to a writable folder and run `ValoHub.exe`. There is no installer
and no first-run wizard.

A window titled `ValoHub` opens at 1440×900 (minimum 900×560), shows `starting…` while
the internal backend starts (up to 20 seconds), then loads the library. An empty library
offers **Open demo**, a bundled synthetic match you can open without importing anything.
The menu bar is hidden. `Ctrl+O` imports a replay; `Alt+F4` quits. `Ctrl+K` or `Ctrl+P`
opens the command palette, `Ctrl+,` opens Settings, `Ctrl+B` collapses the sidebar.

Everything the app writes goes to its data folder (section 9), not to the extracted folder.

## 2. Import a replay

Drag a `.vrf` file onto the library page, click **Import** and pick a file, or press
`Ctrl+O` (File > Import replay…) for a native dialog filtered to `*.vrf`.

The app copies the file into its store, keyed by a hash of the content. Importing the same
file twice does not create a second copy, and your original file is never moved or
deleted. A full import of a supported replay takes about two minutes; the card shows
progress and then a status.

If the file is not a `.vrf`, or its container is unsupported, the import fails with a
message instead of producing a partial replay.

## 3. Library

Each replay is a card with a status:

| Status | Meaning |
|---|---|
| `ready` | Decoded: positions, events and utility are available. |
| `timeline` | Only the container index was read: rounds, deaths, plants, defuses, ultimates and side switch, with no positions. |
| `suspect` | Decoded far less data than expected; treat the replay as unreliable. |
| `stale` | Decoded by an older decoder build; readable, but re-import to refresh. |
| `processing` | An import or job is running. |
| `failed` | The import failed; the card carries the reason. |

Use the search box (`/` focuses it), the filter chips, the sort control and the grid/list
toggle. **Analyze** and **Coach** open the same replay in those workspaces; the replay
page also has a Replay / Analysis / Coach switcher in its header.

## 4. Viewer

The replay page has the map, a round strip, a scrub bar with playback speed, and an
inspector with **Rounds**, **Events**, **Players** and **Coach** tabs.

| Key | Action |
|---|---|
| `Space` or `K` | Play or pause. |
| `←` / `→` | Seek 1 second. |
| `Shift+←` / `Shift+→`, `J` / `L` | Seek 5 seconds back / forward. |
| `[` / `]` | Previous / next round. |
| `B` | Bookmark or un-bookmark the current round. |
| `1` `2` `3` `4` | Rounds / Events / Players / Coach tab. |
| `Ctrl+I` | Collapse or expand the inspector. |
| `F11` | Maximise the map. |
| `Esc` | Close a popover or an enlarged map, then return to the library. |

**Markers.** Each player is their agent's portrait in a ring that identifies the team.
Facing is a chevron outside the ring; **Rotate icons** turns the portrait with the facing.
A dead player fades and is replaced by a team-coloured ✕ at the death position. Overlapping
markers are nudged apart with a hairline back to the true position. If the art is missing,
the marker shows initials on a team-coloured disc.

**Utility.** Only recorded utility is drawn: a dashed flight path in the thrower's team
colour, ✕ marks at bounces, the ability icon and an effect area at the landing, a solid
line for walls, and no flight line for landing-only items. Unresolvable owners are grey and
labelled `unowned`. **Utility paths** and **Landings & areas** toggle the layer; the player
and team filters narrow it. Click a utility mark, feed row or timeline tick to seek there.

**Bookmarks.** **Bookmark round** or `B` marks the current round; the list is under
**Bookmarks** in the Rounds tab, newest first, and each row jumps or removes. Bookmarks
are stored per replay in local browser storage on this PC (limit 200 per replay) and
survive restarts.

**Share moment.** **Share moment** writes a short text block with the map, round, clock,
replay id and time. It contains no address or port, so it stays meaningful later. Copy it
from the window; Electron denies clipboard writes, so the app does not copy it for you.
While the app is open, the URL hash `#r=<round>&t=<seconds>` targets the same moment.

## 5. Analysis

The Analysis page draws a heatmap on the real minimap with three layers: **Player
positions**, **Deaths**, and **Utility / ultimates**. Filter by round, player or team;
the findings table below the map follows the same filters. **Export findings** saves a
Markdown file; **Save heatmap** saves a 1024×1152 PNG with the layer, filters, peak sample
count, replay id and map name. Both are browser downloads and work offline.

Findings come from the decoded events: first contact and trades, positioning, utility use
and round outcomes, each row naming a round and, where the data supports it, a player.

## 6. Coach

The coach reviews one player — you — and never scouts an opponent.

Choose yourself under **This is me** in the Coach tab, then press **Generate review**; the
choice is remembered for that replay. Rules run first; model narration may take a minute
longer. Every finding cites a moment, and **Jump to moment** opens the replay at that round
and time.

Without a model the report is still produced and labelled **Rules-only (no model)**. With
a model, narration may reorder and rephrase the findings but cannot add a finding that has
no matching rule reference; anything uncited is dropped. A running review can be stopped.

## 7. AI providers

**AI Providers** has two groups.

- **On this PC.** llama.cpp (including the managed descriptor at
  `%LOCALAPPDATA%\hermes\runtimes\llamacpp\server.json`), LM Studio on `127.0.0.1:1234`
  and Ollama on `127.0.0.1:11434` are detected automatically. No key is needed; start the
  server, then **Refresh**.
- **Cloud.** OpenRouter, OpenAI, Anthropic, Google Gemini and a custom OpenAI-compatible
  endpoint. OpenRouter offers OAuth or an API key; the others use an API key. A custom
  endpoint must be loopback or a private network address.

The first time you open a cloud provider, a consent sheet names the host and what is sent
(the replay summary with player names replaced, plus your coach instructions). Nothing is
sent until you press **Enable**, and **Verify & save** checks a key before storing it; a key
that fails is not saved. Keys stay in Windows protected storage held by the backend — the
page never receives one. **Default model** picks what narration uses and marks each model
LOCAL or CLOUD; disconnecting deletes the stored credential and falls back to a local model.

## 8. Settings

Settings has five sections: **AI Providers** (default model, connected and consented cloud
providers, local servers answering), **Appearance** (density, reduce motion — applied
immediately and remembered), **Parser** (decoder status, parser commit, build state,
registered and replay-proven branches, last check; the update control is disabled in the
packaged app), **Data & Privacy** (data, cache and log folders with copy buttons; **Open
folder**, **Clear cache** and **Open egress log** are disabled in this build) and **About**
(version, mode, build commit from `BUILD-INFO.json`, backend health, decoder status).

## 9. Data folder and removal

Default data folder: `%LOCALAPPDATA%\ValoHub`. `--home <folder>` overrides it; the
`VALOHUB_HOME` environment variable does the same when no flag is given.

| Path | Contents |
|---|---|
| `data\replays\<id>\` | Normalized replay library. |
| `replays\*.vrf` | Copies of imported replay files. |
| `cache\` | Small caches; safe to delete while the app is closed. |
| `logs\` | `electron.log` and `network-denied.jsonl`. |
| `electron\` | Window state and local storage (bookmarks, preferences). |
| `VALOPLANT_WORK\` | Scratch, staging and records. The name is historical. |

**Removal:** close the app, delete the extracted program folder, and delete the data
folder only if you want to remove the library and imported copies. Your original `.vrf`
files are untouched. Back up the data folder before removing it.

## 10. Troubleshooting

| What you see | What to do |
|---|---|
| `The ValoHub backend is missing.` | The package is incomplete or antivirus removed a file. Extract the zip again, check its hash, check quarantine. |
| `ValoHub cannot write its data folder.` | Free disk space or fix permissions, or start with `--home <writable folder>`. |
| `ValoHub backend did not start in time.` | Close other copies, check antivirus, start again. |
| `The ValoHub backend stopped.` | Choose **Restart backend** once. A second crash offers only **Quit**. |
| `Import failed (HTTP <n>).` | The backend refused or was unreachable; the box shows the response text. |
| Card stays `processing` after a crash | Restart the app; leftover partial files are cleared and the card is marked failed. Import the replay again. |
| Card `failed` on patch 11.07 | Expected: that patch is not decodable. |
| Card `suspect` or `stale` | Too little data, or an older decoder. Re-import; if it repeats, treat the replay as unreliable. |
| A browser shows 403 `missing or invalid session token` | By design: the backend only answers the app window. Use the app. |
| SmartScreen or antivirus warning | The executable is unsigned. Check the zip hash before running; do not add an exclusion unless you accept the risk. |
| App opens but the port is not 18792 | The preferred port was busy and another was chosen. No action needed. |

Logs are in `<data folder>\logs\`. The startup and crash dialogs print the log path.

## 11. Limits

- Only patches 13.06 and 13.08 are proven on real replays. 13.09 is registered in the
  decoder but has not been read from a real 13.09 replay; 11.07 is not decodable.
- New imports are marked `unvalidated`: the package ships no decode-density baseline, so
  the app cannot compare a decode against one.
- Stores decoded by an older decoder stay as they were until you re-import.
- No installer, no code signing, no automatic updates: a new app or decoder means a new
  release. Health, round winner and end type appear only when the replay records them.
- The packaged app forces offline mode and drops the `VALO_LOBBY` and `VALO_WATCHER`
  options, so LAN watch-together and folder watching are not available here. Running from
  source is the only way to use them.
- Built and tested on one Windows 11 machine. Windows 10, other PCs and clean virtual
  machines are untested.
