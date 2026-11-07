# tools/desktop_qa - data / offline / storage QA (lane Q1)

Four stdlib-only scripts (Python 3.12) for the ValoHub desktop migration. Each
prints **one JSON report** and exits non-zero on a failed assertion (0 ok, 1
assertion/byte difference, 2 usage/environment). They are re-runnable by later
cards (I1/R1/F1) and never write inside the repo or the live library.

Owner: `data-bot` (lane Q1). Lane L5 owns `tools/desktop_qa/cdp/**` and
`window_smoke.py`; this README covers only the four data scripts and their tests.

## Guard - `guard.py`

```bash
py -3.12 tools/desktop_qa/guard.py snapshot --root C:/Users/johnq/Documents/test/ValorantBot \
    --out <scratch>/guard-before.json --allow-live-read
py -3.12 tools/desktop_qa/guard.py verify  --root C:/Users/johnq/Documents/test/ValorantBot \
    --against <scratch>/guard-before.json --allow-live-read
```

Pins SHA-256 + size + mtime for: `data/replays/**` (59 files at baseline, 58 in
the `data_replays` group +
`data/replays/bad1543033d11396/viewer/me.json` pinned under `records` as the
identity tripwire, so the groups always sum to `total_files`), `replays/*.vrf`
(3), the decoder `vendorValorantReplayParser/src/CliReader/bin/Release/net10.0`
tree (43 files), `VALOPLANT_WORK/parser-commit.txt`, `dotnet10-sdk/dotnet.exe`,
the `dotnet10-sdk` top-level listing, and any `--extra-dir label=path`.

* Thread-pool hashing (`--workers`, default 8).
* `--fast` re-uses the previous manifest hash when `(size, mtime_ns)` match; a
  plain run hashes everything (a same-size/same-mtime edit is only caught then).
* `verify` prints `added` / `changed` / `removed` / `listings` differences and
  exits 1 on any of them.
* A root containing `data/replays/bad1543033d11396` is *live* and needs
  `--allow-live-read` (read-only acknowledgement; the guard never writes to it).
* Baseline counts are reported under `counts` and mismatches are `warnings`,
  not failures - importing another replay must not turn the guard red.

## Offline - `offline_check.py`

```bash
py -3.12 tools/desktop_qa/offline_check.py scan --root <worktree>
py -3.12 tools/desktop_qa/offline_check.py selftest
py -3.12 tools/desktop_qa/offline_check.py sample --pid <pid> --duration 30 --interval 0.25 --tree
```

`scan` reads the desktop-reachable code only: `bot/replay/*.py` (no tests/tools),
`bot/web/desktop.py`, `bot/sidecar.py`, `desktop/**/*.js|mjs` (no
`node_modules`), and `bot/web/static/replays_app/*.{js,html,css}`. It matches
`http(s)://` / `ws(s)://` and the JS-escaped `https:\/\/` form. Loopback hosts
pass; every other literal must match `ALLOWLIST` (each entry carries a
justification); the rest are `violations` (exit 1).

`sample` polls `netstat -ano` (TCP and UDP) for a PID - and with `--tree`, all
descendants from one `Win32_Process` snapshot - and fails on any non-loopback
remote peer. `LISTENING` rows, `0.0.0.0`/`*`/port-0 remotes are ignored.
Distinct peers are reported per PID with the observed TCP states.

`selftest` is the proof the sampler works: a real loopback connection must pass,
then a real non-blocking connect to TEST-NET (`192.0.2.1:9`, falling back to
`198.51.100.1`/`203.0.113.1`) must be observed as a non-loopback `SYN_SENT`
peer and fail the sampler. If the host refuses to let a SYN leave, the negative
control reports `environment_blocked` (exit 1) rather than a false pass.

## Security matrix - `api_matrix.py`

```bash
py -3.12 tools/desktop_qa/api_matrix.py --base-url http://127.0.0.1:18792 --token <t> --expect-protected
py -3.12 tools/desktop_qa/api_matrix.py --base-url http://127.0.0.1:18851        # legacy baseline
```

`http.client` only, explicit headers (no proxy env, no cookie jar, no redirect
following). Cases: no token -> 401/403; wrong `Host` -> 403 (also on
`/api/app/health`); foreign `Origin` on POST/PUT -> 403;
`X-ValoHub-Token` -> 200; `Cookie: vh_session=...` -> 200;
`/api/decoder/update`, `/api/lobby*`, `/players/*` -> 403/404; shutdown without
token -> 401/403 and with a foreign Origin -> 403. The authorized shutdown is
destructive and only probed with `--allow-shutdown`.

Without `--expect-protected` every status is reported and the run exits 0
(baseline mode, e.g. against the legacy server) — except that a case which could
not be probed at all (connection refused/timeout) is **always** a failure
(`counts.unreachable`, `ok: false`, exit 1): a matrix that reached nothing proves
nothing. With `--expect-protected`, any status outside `expect` fails the run.
`POST /api/decoder/update` is a *real* mutating call on
the legacy server (it queues an updater job that would touch the read-only
vendored tree), so baseline mode skips it and lists it under
`skippedMutating`; it is only probed when the server is expected to refuse it.

Measured baseline against a live isolated legacy server (test port 18852,
evidence `09-api-matrix-live-legacy.json`): legacy has no token, no Host
allow-list and no Origin check — `no_token` 200, `wrong_host` 200,
`foreign_origin_post` 404 (no such route), `players` 200, `shutdown_*` 404, and
`lobby_get` 405. The desktop app is expected to turn those into 401/403; run with
`--expect-protected --token <per-launch token>` once the sidecar exists.

## Isolated import - `isolated_import.py`

```bash
py -3.12 tools/desktop_qa/isolated_import.py --repo C:/Users/johnq/Documents/test/ValorantBot \
    --home <scratch>/acc-<ts> --server legacy --replay bdb6b4ab \
    --port 18851 --timeout 900 --allow-live-read
py -3.12 tools/desktop_qa/isolated_import.py --repo <repo> --home <app home> \
    --server http://127.0.0.1:18792 --token <t> --replay 2f7c79fb
```

`--server legacy` builds the DATA-BASELINE §11 isolated root in `--home`: copies
`bot/`, creates `VALOPLANT_WORK/ replays/ data/replays/ cache/`, copies
`parser-commit.txt`, junctions `dotnet10-sdk` and `vendorValorantReplayParser`
**READ-ONLY**, copies the `.vrf`, runs `py -3.12 -m bot.web` with
`HOST=127.0.0.1 PORT=18851 VALO_OFFLINE=1` (DOTNET_CLI_HOME/NUGET_PACKAGES in
scratch so dotnet never writes into the junction), POSTs
`/api/replays/import-path`, polls `/api/jobs` (default timeout 900 s) and
finally removes only the junctions it created (`cmd /c rmdir <junction>`, no
`/s`).

Expected contract (DATA-BASELINE §12):

| replay | id | outcome | rounds | players | kills | plants | defuses | utility total |
|---|---|---|---|---|---|---|---|---|
| `bdb6b4ab` (13.06 Haven) | `bad1543033d11396` | ready | 18 | 10 | 128 | 11 | 1 | 362 |
| `c428d9d5` (13.08 Pearl) | `87976b8cd193496b` | ready | 22 | 10 | 175 | 14 | 6 | 323 |
| `2f7c79fb` (11.07 Sunset) | `c5809e86257adfb0` | failed | - | - | - | - | - | - |

For a ready import it also checks decoder identity (`parserCommit
09a8e1e1...`, `buildState verified`, no changed assemblies), contiguous round
numbers, and the §12-4 round-1 payload (`valoplant_round/v2`, tickMs 100,
startMs 579, endMs 83495, frames 830, 9 events, 20 utility rows). For 11.07 it
requires an honest single failure: job `error`, library entry `failed`/rounds
null, no `viewer/match.json`, a failure recorded in the manifest, exactly one
job (no retry loop), and `index_replay` still reporting 21 rounds (the container
index returns 21 round dicts; the check compares its **count**, `roundCount`,
against the 21 in DATA-BASELINE §12-8 and records `roundNumbers` for evidence).

**Utility count note (measured 2026-11-02).** DATA-BASELINE §12-5 records
`utilityCounts` sum 359 for 13.06, but that number comes from the *stale* live
store decoded on 2026-10-20 with parser `68a248da`. A fresh decode with the
shipped parser `09a8e1e1` classifies 3 more Sova Owl Drone rows (P1 +2, P6 +1,
`byKind.drone`, from the current catalog) and totals **362**. The script asserts
362, asserts exactly 3 `drone` rows, and reports `utilityStaleLive: 359`; all
other §12-5/7 numbers (18 rounds, 10 players, 128 kills, 11 plants, 1 defuse)
match the fresh decode byte-for-byte.

`--allow-live-read` adds a full guard snapshot of `--repo` before and after the
run; the diff must be empty (this is what proves the real data is untouched).

`--server http://127.0.0.1:P` is the desktop-app mode: it only drives HTTP
(with `X-ValoHub-Token`) against an already-running app; `--home` is used to
read local artifacts when present. It does not start, build or clean anything.

## Tests

```bash
py -3.12 -m pytest tools/desktop_qa/ -q -p no:cacheprovider    # 31 tests, ~25 s
```

`test_guard.py`, `test_offline_check.py`, `test_api_matrix.py`,
`test_isolated_import.py`. The long parts (real decode, TEST-NET control) are
evidence runs, not unit tests.

## Evidence runs (2026-11-02, lane Q1)

`VALOPLANT_WORK/tmp/desktop-evidence/q1/`:

| file | what it proves |
|---|---|
| `01-guard-snapshot.json` | 107 pinned files (59 `data/replays`, 3 `.vrf`, 43 decoder, 2 records) |
| `02-offline-scan.json` | static scan of this worktree: 0 violations |
| `03-offline-selftest.json` | loopback pass + real TEST-NET `SYN_SENT` negative control |
| `05b-import-1306.json` | full 13.06 import, exit 0, **131.56 s** decode+poll, 23/23 checks |
| `08-import-1107-fixed.json` | 11.07 honest single failure, 10/10 checks, index 21 rounds |
| `09-api-matrix-live-legacy.json` | matrix vs a live legacy server: 12/12 reached, 0 unreachable |
| `10-guard-verify-after.json` | guard re-verified against `01`: 0 added/changed/removed |
| `11-data-replays-count.txt` | 28+25+1+5 = 59 files in `data/replays/**` |
