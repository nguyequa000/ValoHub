# Changelog

Desktop version notes for ValoHub. These describe development/package milestones,
not verified public releases or available downloads.

## 0.1.0

First public version. Supersedes the internal 0.5.x development builds; the public line
starts at 0.1.0.

- Desktop replay reviewer for your own `.vrf` matches: import, replay library, 2D minimap
  with player positions, utility and deaths, round scrubber and kill feed.
- Analysis workspace: round/side summaries, heatmaps, findings export and bookmarks.
- Rules-based self-review ("coach") over the decoded replay, with optional local-model
  narration; no account and no network needed to review.
- Portable Windows package: no installer, no code signing, data under
  `%LOCALAPPDATA%\ValoHub`.
- App-only repository: Riot agent and ability art is not tracked or redistributed; the
  bundled fetcher caches it locally. The bundled web replayer keeps its upstream MIT
  notice (`LICENSES/viewer-ValorantWebReplayer-LICENSE.txt`).
- Maintainer-only desktop build until the untracked parser inputs are documented or
  shipped; a clean-clone build is unverified.

## 0.5.1

Patch on top of 0.5.0, from the 0.5.0 review.

- Program icon: the hub slab keeps square left corners where the three feed bars join it,
  removing a 1 px plate notch at 16 px. No other geometry changed.

## 0.5.0

- ValoHub desktop branding and Windows executable metadata.
- App mark: three feed bars ending flush against a hub slab, with program icons,
  a brand PNG and a monochrome mask for the titlebar, splash and About.
- Local backend authentication uses a per-launch token; desktop integration uses
  `VALOHUB_*` variables. Default app data folder: `%LOCALAPPDATA%\ValoHub`.

## 0.4.1

Fixes on top of 0.4.0.

- About shows the correct version and the build commit from `BUILD-INFO.json`, instead of
  a possibly stale value.
- Executable metadata: FileVersion and ProductVersion follow the release, with ProductName,
  CompanyName and FileDescription set to ValoHub.
- Findings export fills the Player column from the finding text (player name, else roster
  id, else `—`); round-result rows keep `—`.
- Share moment explains when the clipboard is unavailable: "Clipboard blocked — copy the
  text below".
- Polish pass: coach empty card no longer overlaps the context column at narrow sizes,
  analysis uses a single scroll axis, and replay kill ticks get their own lane with
  clustered counts.

## 0.4.0

- New app mark (V in a broken ring, dot above the apex) in the titlebar, splash, About and
  the executable icon.
- Round bookmarks: **Bookmark round** or `B`, list in the Rounds tab, stored per replay on
  this PC.
- Share moment: a text block with map, round, clock and replay id, and no server address.
- Analysis exports: **Export findings** (Markdown) and **Save heatmap** (PNG).
- Visual polish pass in `ui/polish.css` only: spacing, type scale, states, focus and
  reduced motion. No markup or design-token changes.

Known limits in that build: 13.09 untested on a real replay; unsigned build, no installer;
the findings Player column read `—` on every row; About's build commit could be stale; the
executable reported ProductVersion 0.1.0.0; first-frame layout shift on a cold load.

## 0.1.0

First portable desktop package.

- Electron shell that starts a hidden local backend and loads the replay library in its own
  window; the window only talks to `127.0.0.1` with a per-launch token.
- Import of `.vrf` replays into a hash-keyed local store, with the container-index timeline
  for patches the decoder cannot read.
- 2D viewer with player markers, trails, deaths, spike events and recorded utility;
  analysis page with heatmaps and findings; rules-first coach with optional local model
  narration.
- Packaged as a `win-unpacked` folder and a zip. No installer, no code signing, no
  account, no network needed to run.

## Versions between 0.1.0 and 0.4.0

0.2.x and 0.3.x were development builds. No version notes for them are kept in this
repository, so they are not reconstructed here.
