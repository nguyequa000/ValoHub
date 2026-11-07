# desktop_qa — CDP + native-window QA harness (lane L5)

Target: the Electron ValoHub app (window class `Chrome_WidgetWin_1`, title `ValoHub`). CDP is only
available when the app is started with env `VALOHUB_QA_DEBUG_PORT=N` (remote debugging on 127.0.0.1).
Needs Node >= 22 (global WebSocket) and Python 3.12 (stdlib + ctypes only).

## cdp/run_checks.mjs
```
node tools/desktop_qa/cdp/run_checks.mjs --debug-port 19241 --base-url http://127.0.0.1:18792 \
     --replay-ids a,b,c --expect-cards 4 --out DIR [--min-painted 0.01] [--settle-ms 2500] [--card-selector .replay-card]
```
Checks: `/replays` card count + console errors; per id `/replays/<id>/view` (canvas exists and painted: share of
pixels that are neither transparent nor the dominant colour >= `--min-painted`; `<img>` broken-image check) and
`/replays/<id>/analysis`; `window.require`/`window.process` undefined, `window.open` returns null.
Navigation never leaves the base origin. Network events are recorded; `report.json` =
`{checks, requests, nonLoopback, blocked, screenshots, consoleErrors}`. Exit 1 on any failed check or any
non-loopback request that was not blocked ("blocked" = `loadingFailed` with a blockedReason / ERR_BLOCKED*,
i.e. refused by app policy; a DNS failure is NOT blocked). `cdp/cdp.mjs` is the reusable client.

## window_smoke.py
```
py -3.12 tools/desktop_qa/window_smoke.py --cmd "<exe args>" --title ValoHub [--expect-image X.exe] \
    [--expect-class Chrome_WidgetWin_1] [--expect-chromium-children] [--timeout 60] [--hold S] \
    [--close-mode wm_close|kill|none] [--screenshot out.png] [--json out.json] [--title-match equals|contains] [--env K=V]
```
Launches, finds a visible top-level window owned by the launched process tree, reports hwnd/class/rect/pid/image/
visible/cloaked, rejects browser images (msedge/chrome/firefox...), checks image identity, optional
`--type=renderer` + `--type=gpu-process` same-image children (command lines via PowerShell CIM), PrintWindow
screenshot (PNG via zlib), closes by WM_CLOSE (or terminates recorded PIDs), then requires the whole tree plus any
late descendants (vrb.exe, dotnet.exe) to be gone within 10 s; survivors are listed and then terminated by recorded
PID only. Never kills by image name. Typical Electron run: start window_smoke with `--hold 60 --env
VALOHUB_QA_DEBUG_PORT=19241` and run run_checks.mjs meanwhile.

## Self-tests
`py -3.12 -m unittest tools.desktop_qa.cdp.tests.test_selftests -v` (env `L5_OUT` = evidence dir,
`L5_SCRATCH`, `L5_SPIKECON`, `L5_ELECTRON_STUB_DIR` = dir with `npm i electron@45.2.1` + `node install.js`).
Covers headless Edge against a local page on 18841 (positive, blank canvas, non-loopback fetch, broken image, wrong
card count), WebView2 stand-in window (positive, wrong image, missing window, browser rejected) and an Electron
stub (`tests/electron_stub/main.js`) asserting Chrome_WidgetWin_1 + renderer/GPU children + CDP run.
