# ValoHub Electron shell (lane E1)

Electron 45.2.1 main = shell + sidecar supervisor. Spawns `resources/backend/python.exe -m bot.sidecar serve --port 18792 --data-dir <data>`
(token via env `VRB_TOKEN`, never argv/logs), waits for the `{"event":"ready",...}` line, then loads `http://127.0.0.1:<port>/replays`.

- `npm ci` then, if `node_modules/electron/dist` is missing (npm 11 skips unlisted install scripts): `node node_modules/electron/install.js`.
- `npm start` / `npm test` / `npm run pack` (-> `dist/win-unpacked/ValoHub.exe`; needs `../build/resources/{backend,decoder,runtime}` staged by `desktop/build.ps1`).
- `node scripts/verify-fuses.js dist/win-unpacked/ValoHub.exe` prints the fuse wire; `node scripts/asar-tamper-check.mjs <scratch-dir>` proves ASAR integrity is enforced.
- Data root: `--home` > `VALOHUB_HOME` > `%LOCALAPPDATA%\ValoHub`. Logs: `<data>/logs/electron.log` (1 MB x 5), `network-denied.jsonl`.
- QA hooks (opt-in): `VALOHUB_QA=1` DevTools, `VALOHUB_QA_DEBUG_PORT=N`, `--smoke-seconds N --report FILE`, `--check`; `VALOHUB_PORT` overrides 18792; `VALOHUB_COOKIE_FALLBACK=1` also sets cookie `vh_session`.
  `VALOHUB_SIDECAR_CMD='["python","fake.py"]'` replaces the sidecar only when not packaged.
- Token injection: `webRequest.onBeforeSendHeaders` adds `X-ValoHub-Token`; verified in real Electron for page, static assets, fetch, EventSource and WebSocket (cookie fallback not needed).
- Inline `<script>` blocks in `bot/web/static/replays_app/*.html` use no localStorage/sessionStorage/indexedDB (grep: 0 hits).
