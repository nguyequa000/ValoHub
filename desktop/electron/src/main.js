'use strict';
// ValoHub Electron main = shell + sidecar supervisor. Order follows PLAN REVISION 2 / C5'.
const { app, BrowserWindow, Menu, dialog, session, screen } = require('electron');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const crypto = require('node:crypto');
const { Supervisor, SupervisorError, request } = require('./supervisor');
const { RotatingLog } = require('./logger');
const { isAllowedUrl, isAppOrigin, isAllowedNavigation } = require('./policy');
const W = require('./window');

const argv = process.argv.slice(1);
function flag(name) { const i = argv.indexOf(name); return i >= 0 ? (argv[i + 1] === undefined || argv[i + 1].startsWith('--') ? true : argv[i + 1]) : undefined; }

const QA = process.env.VALOHUB_QA === '1';
const PREFERRED_PORT = Number(process.env.VALOHUB_PORT || 18792);
const READY_TIMEOUT_MS = Number(process.env.VALOHUB_READY_TIMEOUT_MS || 20000);
const smokeSeconds = flag('--smoke-seconds') ? Number(flag('--smoke-seconds')) : null;
const reportFile = flag('--report') || null;
const checkMode = flag('--check') === true || flag('--check') === 'true';

// 1. sandbox before anything else; QA switches before ready
app.enableSandbox();
app.setAppUserModelId(W.APP_USER_MODEL_ID); // Windows taskbar grouping / notifications identity
if (process.env.VALOHUB_QA_DEBUG_PORT) {
  app.commandLine.appendSwitch('remote-debugging-port', String(Number(process.env.VALOHUB_QA_DEBUG_PORT)));
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
}

// 2. data root, userData/sessionData before ready
const homeArg = flag('--home');
const dataRoot = path.resolve(
  typeof homeArg === 'string' ? homeArg
    : process.env.VALOHUB_HOME || path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'ValoHub'));

// One-time rename migration: a pre-rename install keeps its replays, bookmarks and settings.
if (typeof homeArg !== 'string' && !process.env.VALOHUB_HOME) {
  const legacyRoot = path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), 'AppData', 'Local'), 'ValoPlant');
  try {
    if (!fs.existsSync(dataRoot) && fs.existsSync(legacyRoot)) fs.renameSync(legacyRoot, dataRoot);
  } catch { /* leave both folders in place; a failed move must not block startup */ }
}

function resolveSidecar() {
  const override = !app.isPackaged && process.env.VALOHUB_SIDECAR_CMD;
  if (override) {
    const arr = JSON.parse(override); // JSON array: [command, ...prefixArgs]
    return { command: arr[0], prefix: arr.slice(1), exists: fs.existsSync(arr[0]) || true, source: 'VALOHUB_SIDECAR_CMD' };
  }
  const bundle = app.isPackaged ? process.resourcesPath : path.resolve(__dirname, '..', '..', 'build', 'resources');
  const exe = path.join(bundle, 'backend', 'python.exe');
  return { command: exe, prefix: ['-m', 'bot.sidecar'], exists: fs.existsSync(exe), source: app.isPackaged ? 'packaged' : 'dev-build-dir', bundle };
}

const { dataWritable: dataWritableAt, openLog, unwritableText } = require('./preflight');
function dataWritable() { return dataWritableAt(dataRoot); }

// Last line of defence: no failure path may leave a windowless process behind.
let fatalHandled = false; let fatalSup = null;
function fatal(what, why, fix, code) {
  if (fatalHandled) return; fatalHandled = true;
  const finish = () => {
    try { if (fatalSup) fatalSup._hardKillSync(); } catch { /* ignore */ }
    app.exit(code);
  };
  const show = () => {
    try {
      if (!smokeSeconds) {
        dialog.showMessageBoxSync({ type: 'error', title: 'ValoHub', message: what,
          detail: `Why: ${why}\n\nFix: ${fix}\n\nLog: ${path.join(dataRoot, 'logs', 'electron.log')}`, buttons: ['OK'] });
      } else if (reportFile && reportFile !== true) {
        fs.writeFileSync(reportFile, JSON.stringify({ pid: process.pid, error: what, why }));
      }
    } catch { /* ignore */ }
    finish();
  };
  if (app.isReady()) show(); else app.whenReady().then(show, finish);
}
process.on('uncaughtException', (e) => {
  try { fs.appendFileSync(path.join(dataRoot, 'logs', 'electron.log'), `${new Date().toISOString()} ERROR uncaught: ${e && e.stack}\n`); } catch { /* ignore */ }
  fatal('ValoHub hit an unexpected error and must close.', String(e && e.message), 'Start ValoHub again; see the log if it repeats.', 1);
});
process.on('unhandledRejection', (e) => {
  try { fs.appendFileSync(path.join(dataRoot, 'logs', 'electron.log'), `${new Date().toISOString()} ERROR unhandled rejection: ${e && e.stack || e}\n`); } catch { /* ignore */ }
  fatal('ValoHub hit an unexpected error and must close.', String(e && e.message || e), 'Start ValoHub again; see the log if it repeats.', 1);
});

if (checkMode) {
  const sc = resolveSidecar();
  const werr = dataWritable();
  const out = { dataRoot, userData: path.join(dataRoot, 'electron'), sidecar: sc.command, sidecarSource: sc.source,
    sidecarPresent: !!sc.exists && (sc.source === 'VALOHUB_SIDECAR_CMD' ? true : fs.existsSync(sc.command)),
    bundle: sc.bundle || null, dataWritable: werr === null, dataError: werr, packaged: app.isPackaged, electron: process.versions.electron };
  const ok = out.sidecarPresent && out.dataWritable;
  const text = JSON.stringify(out);
  process.stdout.write(text + '\n');
  if (reportFile && reportFile !== true) { try { fs.writeFileSync(reportFile, text); } catch { /* ignore */ } }
  app.exit(ok ? 0 : 2);
} else {
  run();
}

function run() {
  app.setPath('userData', path.join(dataRoot, 'electron'));
  app.setPath('sessionData', path.join(dataRoot, 'electron', 'session'));
  app.setName('ValoHub');

  const opened = openLog(RotatingLog, path.join(dataRoot, 'logs', 'electron.log'));
  const log = opened.log;
  const startupWerr = opened.error || dataWritable();
  if (startupWerr) { // data/logs folder unusable: designed box, then leave. No window, no backend.
    const t = unwritableText(dataRoot, startupWerr);
    fatal(t.what, t.why + (opened.error ? ' (the log file cannot be created in that folder)' : ''), t.fix, 2);
    return;
  }
  const deniedLog = path.join(dataRoot, 'logs', 'network-denied.jsonl');
  const denied = [];
  let win = null; let sup = null; let token = null; let port = null;
  let restarts = 0; let quitting = false; let policyInstalled = false;

  // 3. single instance BEFORE spawning
  if (!app.requestSingleInstanceLock()) { app.exit(0); return; }
  app.on('second-instance', () => { if (win) { if (win.isMinimized()) win.restore(); win.focus(); } });

  function recordDenied(kind, url) {
    const rec = { ts: new Date().toISOString(), kind, url: String(url).slice(0, 500) };
    denied.push(rec);
    try { fs.mkdirSync(path.dirname(deniedLog), { recursive: true }); fs.appendFileSync(deniedLog, JSON.stringify(rec) + '\n'); } catch { /* ignore */ }
  }

  async function showError(what, why, fix) {
    log.error(`${what} | ${why}`);
    const logPath = path.join(dataRoot, 'logs', 'electron.log');
    if (smokeSeconds) { // unattended QA: never block on a modal dialog
      if (reportFile && reportFile !== true) { try { fs.writeFileSync(reportFile, JSON.stringify({ pid: process.pid, error: what, why })); } catch { /* ignore */ } }
      return { response: 0 };
    }
    return dialog.showMessageBox(win && !win.isDestroyed() ? win : undefined, {
      type: 'error', title: 'ValoHub', message: what,
      detail: `Why: ${why}\n\nFix: ${fix}\n\nLog: ${logPath}`, buttons: ['OK'],
    });
  }

  function installPolicy() {
    if (policyInstalled) return; policyInstalled = true;
    const ses = session.defaultSession;
    ses.webRequest.onBeforeRequest((details, cb) => {
      const u = details.url;
      if (u.startsWith('devtools:') && QA) return cb({});
      if (port && isAllowedUrl(u, port)) return cb({});
      if (!port && u.startsWith('data:')) return cb({});
      recordDenied('request', u);
      cb({ cancel: true });
    });
    ses.webRequest.onBeforeSendHeaders((details, cb) => {
      const headers = Object.assign({}, details.requestHeaders);
      if (port && token && isAppOrigin(details.url.replace(/^ws:/, 'http:'), port)) headers['X-ValoHub-Token'] = token;
      cb({ requestHeaders: headers });
    });
    ses.setPermissionRequestHandler((wc, perm, cb) => { recordDenied('permission-request', perm); cb(false); });
    ses.setPermissionCheckHandler((wc, perm) => { recordDenied('permission-check', perm); return false; });
    app.on('web-contents-created', (e, wc) => {
      wc.setWindowOpenHandler(({ url }) => { recordDenied('window-open', url); return { action: 'deny' }; });
      const nav = (ev, url) => { if (!(port && isAllowedNavigation(url, port)) && !url.startsWith('data:text/html')) { ev.preventDefault(); recordDenied('navigate', url); } };
      wc.on('will-navigate', nav);
      wc.on('will-redirect', nav);
      wc.on('will-frame-navigate', nav);
      wc.on('will-attach-webview', (ev) => { ev.preventDefault(); recordDenied('attach-webview', ''); });
    });
  }

  function splash(msg) {
    // Inline the authored SVG: the startup data: document cannot fetch local assets.
    const mark = fs.readFileSync(path.join(__dirname, '..', 'build', 'mark.svg'), 'utf8').replace('<svg ', '<svg width="64" height="64" ');
    const html = `<!doctype html><meta charset=utf-8><title>ValoHub</title><body style="background:#0B0F16;color:#E7ECF5;font:16px Segoe UI,sans-serif;display:flex;flex-direction:column;gap:24px;align-items:center;justify-content:center;height:100vh;margin:0"><div aria-hidden="true" style="width:64px;height:64px">${mark}</div><div role="status">${msg}</div></body>`;
    return 'data:text/html;charset=utf-8,' + encodeURIComponent(html);
  }

  // Window geometry persists only for real users: QA and unattended smoke runs keep fixed geometry.
  const persistState = !QA && !smokeSeconds;
  const statePath = W.windowStatePath(dataRoot);
  let saveTimer = null;
  function saveState() {
    if (!persistState || !win || win.isDestroyed()) return;
    W.writeWindowState(statePath, { ...win.getNormalBounds(), maximized: win.isMaximized() });
  }
  function scheduleSave() {
    if (!persistState) return;
    clearTimeout(saveTimer); saveTimer = setTimeout(saveState, 500);
  }

  function createWindow() {
    let restore = {};
    if (persistState) {
      const st = W.readWindowState(statePath); // null when missing or corrupt -> defaults
      restore = W.resolveBounds(st, screen.getAllDisplays().map((d) => ({ workArea: d.workArea })));
    }
    const { maximized, ...geometry } = restore;
    win = new BrowserWindow({
      ...W.windowOptions(), ...geometry, title: 'ValoHub', show: true,
      icon: path.join(__dirname, '..', 'build', 'icon.ico'),
      webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false, devTools: QA },
    });
    const backdrop = W.applyChrome(win, { platform: process.platform, release: os.release(), env: process.env });
    log.info(`window chrome: menuBar=hidden backdrop=${backdrop} persistState=${persistState}`);
    if (maximized) win.maximize();
    win.on('resize', scheduleSave);
    win.on('move', scheduleSave);
    win.on('close', saveState);
    win.on('closed', () => { clearTimeout(saveTimer); win = null; });
    // Ctrl+O fallback in case the hidden-menu accelerator does not fire; importReplayOnce guards against a double dialog.
    win.webContents.on('before-input-event', (_e, input) => {
      if (input.type === 'keyDown' && input.control && !input.alt && !input.shift && !input.meta && String(input.key).toLowerCase() === 'o') importReplayOnce();
    });
    win.loadURL(splash('starting\u2026'));
  }

  // The packaged build writes BUILD-INFO.json next to the exe; package.json keeps the dev version.
  function buildInfo() {
    try {
      const p = path.join(path.dirname(app.getPath('exe')), 'BUILD-INFO.json');
      return JSON.parse(fs.readFileSync(p, 'utf8'));
    } catch { /* dev run or missing file */
      return null;
    }
  }

  function productVersion() {
    const info = buildInfo();
    if (info && typeof info.version === 'string' && /^\d+\.\d+\.\d+/.test(info.version)) return info.version;
    return app.getVersion();
  }

  async function startBackend() {
    const sc = resolveSidecar();
    if (sc.source !== 'VALOHUB_SIDECAR_CMD' && !fs.existsSync(sc.command)) {
      await showError('The ValoHub backend is missing.', `${sc.command} was not found.`, 'Reinstall ValoHub or rebuild the package (desktop/build.ps1).');
      return false;
    }
    const werr = dataWritable();
    if (werr) {
      await showError('ValoHub cannot write its data folder.', `${dataRoot}: ${werr}`, 'Free disk space / fix permissions, or start with --home <writable folder>.');
      return false;
    }
    token = crypto.randomBytes(32).toString('base64url');
    log.addSecret(token);
    port = null;
    const env = { VALOHUB_BUNDLE: sc.bundle || path.resolve(__dirname, '..', '..', 'build', 'resources'), VALO_OFFLINE: '1', VALOHUB_VERSION: productVersion(), VALOHUB_BUILD_COMMIT: (buildInfo()?.git_commit || ''), PYTHONDONTWRITEBYTECODE: '1' };
    if (sc.source === 'VALOHUB_SIDECAR_CMD') for (const k of Object.keys(process.env)) if (k.startsWith('FAKE_')) env[k] = process.env[k]; // test-only passthrough
    sup = new Supervisor({
      command: sc.command, args: [...sc.prefix, 'serve', '--port', String(PREFERRED_PORT), '--data-dir', dataRoot],
      token, env, readyTimeoutMs: READY_TIMEOUT_MS, log,
    });
    fatalSup = sup;
    sup.on('crashed', (info) => onCrash(info));
    try {
      const r = await sup.start();
      port = r.port;
      if (port !== PREFERRED_PORT) log.info(`preferred port ${PREFERRED_PORT} busy; using ${port}`);
      if (process.env.VALOHUB_COOKIE_FALLBACK === '1') {
        await session.defaultSession.cookies.set({ url: `http://127.0.0.1:${port}/`, name: 'vh_session', value: token, httpOnly: true, sameSite: 'strict' });
      }
      return true;
    } catch (e) {
      const tail = e.stderrTail ? `\n\nbackend stderr:\n${e.stderrTail}` : '';
      const msgs = {
        READY_TIMEOUT: ['ValoHub backend did not start in time.', 'No ready signal within the timeout.', 'Close other copies, check antivirus, then start again.'],
        BAD_READY: ['ValoHub backend printed an unexpected start-up line.', e.message, 'The backend and the shell versions probably do not match; reinstall.'],
        EARLY_EXIT: ['ValoHub backend stopped while starting.', e.message, 'See the log for the backend error.'],
        SPAWN_FAILED: ['ValoHub backend could not be launched.', e.message, 'Reinstall ValoHub; check antivirus quarantine.'],
        HEALTH_FAILED: ['ValoHub backend started but is not healthy.', e.message, 'See the log, then start again.'],
      }[e.code] || ['ValoHub backend failed.', e.message, 'See the log.'];
      await showError(msgs[0], msgs[1] + tail, msgs[2]);
      return false;
    }
  }

  async function onCrash(info) {
    if (quitting) return;
    log.error(`backend crashed code=${info.code}`);
    const canRestart = restarts < 1;
    const r = await dialog.showMessageBox(win && !win.isDestroyed() ? win : undefined, {
      type: 'error', title: 'ValoHub', message: 'The ValoHub backend stopped.',
      detail: `Exit code: ${info.code}\n\nLast backend output:\n${info.stderrTail || '(none)'}\n\nLog: ${path.join(dataRoot, 'logs', 'electron.log')}`,
      buttons: canRestart ? ['Restart backend', 'Quit'] : ['Quit'], defaultId: 0, cancelId: canRestart ? 1 : 0,
    });
    if (canRestart && r.response === 0) {
      restarts += 1;
      if (win) win.loadURL(splash('restarting\u2026'));
      if (await startBackend()) { if (win) win.loadURL(`http://127.0.0.1:${port}/replays`); else openMain(); } else quit();
    } else quit();
  }

  function openMain() {
    if (!win) createWindow();
    win.loadURL(`http://127.0.0.1:${port}/replays`);
  }

  async function importReplay() {
    if (!port) return;
    const r = await dialog.showOpenDialog(win, { title: 'Import replay', properties: ['openFile'], filters: [{ name: 'VALORANT replay', extensions: ['vrf'] }] });
    if (r.canceled || !r.filePaths.length) return;
    try {
      const res = await request('POST', port, '/api/replays/import-path', token, 30000, JSON.stringify({ path: r.filePaths[0] }));
      await dialog.showMessageBox(win, { type: res.status < 300 ? 'info' : 'error', title: 'Import replay', message: res.status < 300 ? 'Import started.' : `Import failed (HTTP ${res.status}).`, detail: res.body.slice(0, 800) });
      if (res.status < 300 && win) win.webContents.reload();
    } catch (e) {
      await showError('Import failed.', e.message, 'Check that the backend is running.');
    }
  }

  let importing = false;
  async function importReplayOnce() {
    if (importing) return;
    importing = true;
    try { await importReplay(); } finally { importing = false; }
  }

  function buildMenu() {
    Menu.setApplicationMenu(Menu.buildFromTemplate(W.menuTemplate({ qa: QA, onImport: importReplayOnce, onQuit: () => quit() })));
  }

  let stopping = null;
  function quit() { app.quit(); }
  app.on('window-all-closed', () => quit());
  app.on('before-quit', (e) => {
    quitting = true;
    if (stopping === 'done') return;
    e.preventDefault();
    if (!stopping) {
      stopping = (async () => {
        let how = 'none';
        try { if (sup) how = await sup.stop(5000); } catch (err) { log.error(`stop failed: ${err.message}`); }
        log.info(`shutdown: ${how}`);
        stopping = 'done';
        app.exit(0);
      })();
    }
  });

  async function smokeReport() {
    let title = null; let url = null;
    try { if (win) { url = win.webContents.getURL(); title = win.getTitle(); } } catch { /* ignore */ }
    const rep = { pid: process.pid, url, port, title, loaded: !!(url && url.includes('/replays')), deniedRequests: denied.length, denied };
    if (reportFile && reportFile !== true) fs.writeFileSync(reportFile, JSON.stringify(rep));
    log.info(`smoke report: ${JSON.stringify(rep)}`);
    quit();
  }

  app.whenReady().then(async () => {
    log.info(`start v${productVersion()} electron=${process.versions.electron} packaged=${app.isPackaged} data=${dataRoot}`);
    buildMenu();
    installPolicy();
    createWindow();
    const ok = await startBackend();
    if (!ok) { quit(); return; }
    openMain();
    if (smokeSeconds) setTimeout(smokeReport, smokeSeconds * 1000);
  }).catch(async (e) => { log.error(`fatal: ${e && e.stack}`); try { await showError('ValoHub failed to start.', String(e && e.message), 'See the log.'); } finally { app.exit(1); } });
}

module.exports = {};
