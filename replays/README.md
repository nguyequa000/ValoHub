# Replays — `.vrf` originals

The two Valorant match replays kept for local testing live here. They are gitignored
(only this README is tracked) and the app never moves or rewrites them: an import
copies through `VALOPLANT_WORK/tmp/incoming/` and the library keeps referencing
`replays/<name>.vrf` as the source.

| File | Patch | Map | Import result |
|---|---|---|---|
| `bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf` | `++Ares-Core+release-13.06` | Haven (`/Game/Maps/Triad/Triad`) | full decode: CliReader `--profile viewer` + stdlib normalizer (`data/replays/bad1543033d11396`, status `ready`, 18 rounds) |
| `2f7c79fb-5fda-4a8f-89a1-7fb27d9ac587.vrf` | `++Ares-Core+release-11.07` | Sunset (`/Game/Maps/Juliett/Juliett`) | container index only — the pinned decoder cannot parse the pre-12.x header (`InvalidReplayHeaderException … InvalidSeek. Position=144`), so the import ends as an honest `failed` (`data/replays/c5809e86257adfb0`); 11.07 decode is dropped by operator decision (2026-10-13) |

## Import them

```powershell
$env:PORT = "18790"; py -3.12 -m bot.web     # P3 lane port; without PORT the app uses 8787
```

Open `/replays` and import a `.vrf` — either upload the file, or point
`VALOHUB_DEMOS_DIR` at this folder and use the local-demos picker. Both routes
copy (`POST /api/replays/import-path` copies, never moves), then open the replay
for the 2D viewer.

## Index without a decoder — `bot/replay/container_index.py`

`index_replay(path)` reads the container prefix + chunk index with the standard
library only, read-only, and returns `rounds` (start/end ms, deaths, plant and
defuse times, `defenderWin` — a defuse is a defender win), event counts, ultimate
and side-switch times, and the container block (`branch`, `mapPath`, `durationMs`)
from `bot/replay/pipeline.peek_container`. It never reads movement packets, so it
also works on the 11.07 file above — the P2 winner checks use `defenderWin` for
their "every defuse round is a defender win" rule.

Tests (they read these two files in place, so the `.vrf` files must be present):

```powershell
py -3.12 -m pytest bot/replay/test_container_index.py -q
```

## Packet decoder (vendored .NET CLI)

`bot/replay/pipeline.py` runs the pinned
[ValorantReplayParser](https://github.com/michel-giehl/ValorantReplayParser) CLI
(`dotnet10-sdk/dotnet.exe` + `vendorValorantReplayParser/src/CliReader/bin/Release/net10.0/CliReader.dll`).
Which branches the vendored registry knows is decided by
`vendorValorantReplayParser/src/Replay.Encoding/PayloadEncryption/VersionedTransforms/`
and is listed by `GET /api/decoder`; a known branch still needs the file's header
to parse, which is exactly what fails for 11.07. No fake transform or
patch-version substitution is used anywhere. Rebuild after updating the vendored
source:

```powershell
$env:DOTNET_CLI_HOME = Join-Path (Get-Location) 'dotnet10-sdk/cli-home'
$env:NUGET_PACKAGES = Join-Path (Get-Location) 'dotnet10-sdk/packages'
$env:DOTNET_CLI_TELEMETRY_OPTOUT = '1'
./dotnet10-sdk/dotnet.exe build vendorValorantReplayParser/src/CliReader/CliReader.csproj -c Release
```

## Legacy extractor (retired 2026-10-13)

The old root extractor scripts, their ~960 MB of raw JSON dumps (at the repo root
and under `replays/`) and `replays/parser-run.log` were deleted in P3/P0-D. The
stdlib container-index logic worth keeping now lives in
`bot/replay/container_index.py`; the full delete/keep record (paths, sizes,
what was preserved and why) is in `VALOPLANT_WORK/DELETED.md`.
