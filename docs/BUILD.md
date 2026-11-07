# ValoHub build guide

This builds the Windows package: a portable `win-unpacked` folder and a zip. There is no
installer, no code signing and no publishing step.

**Maintainer-only for now:** the decoder bootstrap depends on untracked pin, overlay,
baseline and reference-replay inputs. Until their acquisition and provenance are
documented or the required inputs are shipped, this is not a self-contained contributor
build recipe. A clean-clone desktop build is unverified. Public releases are not assumed.

```
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/build.ps1
```

## 1. Prerequisites

| Need | Version / notes |
|---|---|
| OS | Windows 11 x64. The build and the packaged app have only been run there; Windows 10 is untested. |
| PowerShell | `powershell -NoProfile -ExecutionPolicy Bypass -File desktop/build.ps1`. No admin rights needed; the script writes no registry values and creates no shortcuts. |
| Python | 3.12 through the launcher: `py -3.12`. The script fails closed if the launcher or 3.12 is missing. |
| Node / npm | Node 24 with npm 11, needed unless you pass `-SkipElectron`. |
| git | Used for `git_commit` and `git_dirty` in `BUILD-INFO.json`. |
| .NET SDK | A .NET 10 SDK in `dotnet10-sdk\` at the repository root (see step 3). |
| Network | For `pip`, `npm ci`, the .NET restore, the CPython embed ZIP and the one-time asset fetch. `-WheelHouse` removes the pip download; npm still needs its registry. |

## 2. Maintainer bootstrap (not a clean-clone recipe)

The following describes the existing maintainer setup. Do not run step 4 merely to
package an already verified decoder: use its existing binaries and matching build record.
The SDK, generated outputs and updater inputs below are not tracked in git.

1. **Python 3.12.** Confirm `py -3.12 --version` works.
2. **Node 24 and npm.** Confirm `node --version` and `npm --version`. In
   `desktop\electron`, run `npm ci`; if `node_modules\electron\dist` is missing afterwards
   (npm 11 skips unlisted install scripts), run `node node_modules/electron/install.js`.
3. **.NET 10 SDK.** Put a .NET 10 SDK in `dotnet10-sdk\` so that
   `dotnet10-sdk\dotnet.exe` exists. The folder is gitignored and nothing in the build
   downloads it; placing it is a manual step. The package build copies only the runtime
   parts of that SDK.
4. **Decoder build and build record.** First obtain the maintainer inputs:

   - `VALOPLANT_WORK/parser-pin.txt`, or an explicit `--pin <SHA>` identifying the
     intended upstream commit;
   - `VALOPLANT_WORK/parser-overlay.patch`;
   - `VALOPLANT_WORK/parser-baseline.json`, a valid decode-density baseline;
   - `replays/bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf`, the reference replay used
     by the updater self-test.

   **None of these files is tracked.** This repository does not yet document a complete
   way for a clean-clone contributor to obtain or recreate them. Stop if they are
   unavailable; do not substitute an arbitrary replay or generate a baseline merely to
   make the gate pass. Existing local inputs were found by the maintainer preflight,
   which is not clean-clone verification. Only with these inputs, from the repository root:

   ```
   py -3.12 bot/replay/tools/update_parser.py
   # Alternatively, supply the intended commit explicitly:
   py -3.12 bot/replay/tools/update_parser.py --pin <SHA>
   ```

   The updater checks out the upstream parser commit pinned in
   `VALOPLANT_WORK\parser-pin.txt`, applies `VALOPLANT_WORK\parser-overlay.patch`, builds
   `vendorValorantReplayParser\src\CliReader\CliReader.csproj` in Release into
   `vendorValorantReplayParser\src\CliReader\bin\Release\net10.0`, and writes
   `VALOPLANT_WORK\parser-commit.txt` with the assembly hashes. It refuses to publish a
   build that decodes suspiciously little of a known replay and keeps the previous build.
   A missing or invalid baseline or missing reference replay blocks publication.
   It needs network and git, and it does not rewrite `parser-pin.txt` itself.
   It can replace the active parser and requeue stale stores: this is a maintainer
   update operation, not a harmless packaging prerequisite.

   Building `CliReader` by hand with
   `dotnet10-sdk\dotnet.exe build vendorValorantReplayParser\src\CliReader\CliReader.csproj -c Release`
   produces the binaries but not the record. `tools/bundle_decoder.py` verifies every
   `assembly.*` hash in the record against the files and fails closed
   (`SHA-256 mismatch for <name>: record <hash> != actual <hash>`), so the build needs a
   record that matches the binaries you built.

5. **Agent and ability art.** Not tracked or redistributed by this repository:

   ```
   py -3.12 tools/fetch_agent_assets.py
   ```

   Needs network and Pillow (`py -3.12 -m pip install "Pillow>=10.0"`). It writes the
   marker portraits, roster portraits, ability icons, their manifests and
   `bot/replay/agent_ids.json`. Without it the build still succeeds and the viewer falls
   back to initials; pass `-AssetsSrc <dir>` if the art lives outside the checkout.

The direct package inputs outside git are `dotnet10-sdk\`, the built
`CliReader\bin\Release\net10.0` tree and `VALOPLANT_WORK\parser-commit.txt`.
Rebuilding that decoder additionally requires all four updater inputs in step 2.4.
The art cache is gitignored; locally built packages may contain fetched Riot art.
See the [Riot Games notice and licences](../README.md#licenses).

## 3. Build

The bracketed options below are a syntax summary, not a command to paste verbatim.
For a normal build, use the one-line command at the top after preparing the inputs.
The build replaces its staging and unpacked output directories; close running copies
first. It launches `--check` with `desktop/build/check-home`, not your live library.

From the repository root. Replace angle-bracket placeholders with real values; quote
paths that contain spaces. Commands use PowerShell unless noted.

```
powershell -NoProfile -ExecutionPolicy Bypass -File desktop/build.ps1 [-Version <ver>] [-SkipElectron] [-NoZip]
    [-WheelHouse <dir>] [-DotnetSrc <dir>] [-DecoderSrc <dir>] [-Record <file>] [-TransformsSrc <dir>]
    [-BundleDir <dir>] [-AssetsSrc <dir>] [-EmbedZip <file>]
```

| Parameter | Meaning |
|---|---|
| `-Version` | Defaults to the `version` in `desktop/electron/package.json`. Used in the zip name and in `BUILD-INFO.json`. |
| `-SkipElectron` | Stop after the backend assembly, staging, licences and `desktop/build/BUILD-INFO.json`. No Electron pack, no zip, no smoke. |
| `-NoZip` | Pack but do not zip or refresh `SHA256SUMS.txt`; any existing zip/checksum may be stale. |
| `-WheelHouse` | Local wheel folder; pip runs with `--no-index --find-links`. |
| `-BundleDir` | Reuse an assembled decoder bundle (must contain `runtime/dotnet/dotnet.exe` and `decoder/net10.0/CliReader.dll`) instead of running `tools/bundle_decoder.py`. |
| `-DotnetSrc`, `-DecoderSrc`, `-Record`, `-TransformsSrc` | Passed to `tools/bundle_decoder.py`; use them when the untracked inputs live outside the checkout, for example in a worktree. |
| `-AssetsSrc` | Alternative art folder for `make_backend.py`. |
| `-EmbedZip` | Local `python-3.12.10-embed-amd64.zip`; otherwise it is downloaded from python.org and its MD5 is checked. |

What the script does, in order: create `desktop/.venv` and install the sidecar
requirements into it; build the decoder bundle into `desktop/build/bundle`; assemble the
backend with `desktop/sidecar/make_backend.py` (embeddable CPython 3.12.10, `aiohttp` and
its dependencies, the `bot` package without tests, the viewer static files, precompiled
`.pyc`); stage `desktop/build/resources/{backend,decoder,runtime}`; generate
`desktop/build/LICENSES`; run `npm ci` and `npm run pack`; copy the licences and
`BUILD-INFO.json` into the package; zip; then run the smoke check.

## 4. Outputs

| Path | Contents |
|---|---|
| `desktop/build/resources/backend/` | `python.exe`, the embeddable runtime, `site-packages`, `app/bot`. |
| `desktop/build/resources/decoder/` | `net10.0/CliReader.dll`, `parser-commit.txt`, `registered-branches.json`, `bundle-manifest.json`. |
| `desktop/build/resources/runtime/dotnet/` | Private .NET runtime. |
| `desktop/build/BUILD-INFO.json` | Product, version, `git_commit`, `git_dirty`, Python, aiohttp, Electron and electron-builder versions, `built_utc`, backend assembly seconds, and SHA-256 of `ValoHub.exe` and `backend/python.exe`. |
| `desktop/electron/dist/win-unpacked/` | `ValoHub.exe`, `resources/app.asar`, `resources/{backend,decoder,runtime}`, `LICENSES/`, `BUILD-INFO.json`. |
| `desktop/dist/ValoHub-<version>-win64.zip` | The portable release. |
| `desktop/dist/SHA256SUMS.txt` | One line: `<hash>  ValoHub-<version>-win64.zip`. |

`desktop/electron/electron-builder.yml` sets `asar: true`, target `dir` for x64,
`signAndEditExecutable: false` and `publish: null`, and takes `extraResources` from
`desktop/build/resources`. `scripts/afterPack.js` sets the Electron fuses.

## 5. Verify a build

1. **Zip hash.**

   ```
   Get-FileHash -Algorithm SHA256 -LiteralPath desktop\dist\ValoHub-<version>-win64.zip
   ```

   The lower-case value must equal the line in `SHA256SUMS.txt`.

2. **Package self-check.** The build already runs this as its smoke step; you can repeat
   it any time:

   ```
   .\desktop\electron\dist\win-unpacked\ValoHub.exe --home "<scratch folder>" --check
   ```

   It prints one JSON line with `dataRoot`, `userData`, `sidecar`, `sidecarSource`,
   `sidecarPresent`, `bundle`, `dataWritable`, `dataError`, `packaged` and `electron`, then
   exits 0 when the sidecar is present and the data folder is writable, 2 otherwise.

3. **Hashes and build record.** `BUILD-INFO.json` carries the SHA-256 of `ValoHub.exe` and
   `backend/python.exe`. `resources\decoder\bundle-manifest.json` maps every bundled
   decoder file to its SHA-256 and size; `resources\decoder\parser-commit.txt` holds the
   `assembly.*` hashes the app compares at runtime. The app reports the result as
   `buildState` on `/api/decoder` (`verified` when the record matches the files on disk).

4. **Fuses and ASAR integrity.** From `desktop/electron`:

   ```
   node scripts/verify-fuses.js dist/win-unpacked/ValoHub.exe
   node scripts/asar-tamper-check.mjs <new-empty-scratch-folder>
   ```

   The tamper helper copies and modifies a package in the scratch destination; never
   point it at an existing package or data folder. Its `RESULT` is informational,
   not an exit-code gate; inspect both pristine and tampered outcomes.

   The packaged build disables RunAsNode, the `NODE_OPTIONS` environment variable, Node CLI
   inspect and the file-protocol privilege, and enables ASAR integrity and
   only-load-from-ASAR.

5. **Test suites.**

   | Suite | Command | Last recorded |
   |---|---|---|
   | Python | `py -3.12 -m pytest -q -p no:cacheprovider` | 719 passed, 6 skipped; 43 subtests passed on 2026-11-06 |
   | Electron shell (PowerShell) | `cd desktop/electron; npm test` (`node --test test/*.test.js`) | 56 passed, 3 skipped (59 total) on 2026-11-06 |
   | Decoder (vendor) | see below | Historical: 3,285 passed / 9 skipped; not rerun for this docs update |

   Some Python tests need the real replay store or the decoder and fail without them; run
   them in a checkout that has `replays\`, `data\` and the decoder build, or compare the
   failing ids against a run on the base commit before treating them as regressions.

   **Decoder tests are destructive if run naively.** `dotnet test` against
   `vendorValorantReplayParser/` writes into the same `bin/Release/net10.0` folder the app
   loads its live decoder from, so it silently replaces the published binaries and leaves
   `parser-commit.txt` describing a build that is no longer on disk. Always redirect the
   test output:

   ```
   ./dotnet10-sdk/dotnet.exe test vendorValorantReplayParser/ValorantReplayParser.sln -c Release --artifacts-path VALOPLANT_WORK/tmp/<lane>/testout
   ```

   With `--artifacts-path` the live `.dll` files stay byte-identical across a full run.

6. **Packaged imports.** Install the zip into a scratch folder and import one replay from
   each proven patch (13.06 Haven, 13.08 Pearl); both should reach `ready` with positions
   and utility. A 11.07 replay should fail with a decoder message and a `failed` card, not
   a half-built replay.

## 6. Not verified from a clean clone

The bootstrap in section 2 has **not** been run end to end from a clean clone. It is
assembled from `desktop/build.ps1`, `tools/bundle_decoder.py`,
`bot/replay/tools/update_parser.py` and `docs/ARCHITECTURE.md`, but no recorded run starts from an empty checkout
with no `dotnet10-sdk\`, no `CliReader` build and no parser record.

Concretely, a new contributor must supply, and may have to debug:

- a .NET 10 SDK placed at `dotnet10-sdk\` (the repository does not fetch or place it);
- the pin (or explicit `--pin <SHA>`), overlay, valid baseline and exact reference
  replay listed in step 2.4, followed by a successful `update_parser.py` run; network,
  git and the intended upstream commit must be available. The updater writes
  `VALOPLANT_WORK\parser-commit.txt`;
- the agent and map art, or accept initials-only markers;
- Node 24 and npm 11, including the `node_modules/electron/install.js` workaround if
  `npm ci` skips Electron's install script.

Treat the first clean-clone build as unproven until someone completes it and records the
result.

## 7. Build troubleshooting

| Symptom | Cause / fix |
|---|---|
| `Python launcher 'py' not found` or `Python 3.12 not available` | Install Python 3.12 and confirm `py -3.12 --version`. |
| `npm not found on PATH` | Install Node 24, or pass `-SkipElectron` for a backend-only build. |
| `decoder not built: searched env VALO_DOTNET/VALO_DECODER_DIR; bundle ...` at runtime | The package is missing its decoder or .NET runtime; rebuild, or re-extract the zip. |
| `SHA-256 mismatch for <name>` from `bundle_decoder.py` | `parser-commit.txt` does not describe the `CliReader` build you have. Rebuild both with `update_parser.py`. |
| `working tree is dirty` warning | `BUILD-INFO.json` records `git_dirty: true` and the artifact is not reproducible from a commit. Preserve unrelated work; only commit or stash with the owner's approval. |
| `npm ci` finishes but Electron will not start | `node node_modules/electron/install.js` in `desktop/electron`. |
| Build works, imports fail to decode | Python must be 3.12: the wheels and `.pyc` files are built for 3.12.10. |
