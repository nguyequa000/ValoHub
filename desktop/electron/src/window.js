'use strict';
// Window chrome helpers. Pure node (no Electron import) so they run under `node --test`.
// main.js passes in the real BrowserWindow / screen objects.
const fs = require('node:fs');
const path = require('node:path');

const THEME = { bg: '#0B0F16', symbol: '#E7ECF5', overlayHeight: 40 };
const DEFAULT_SIZE = { width: 1440, height: 900 };
const MIN_SIZE = { width: 900, height: 560 };
const MICA_MIN_BUILD = 22000;
const STATE_FILE = 'window-state.json';
const APP_USER_MODEL_ID = 'Nous.ValoHub';

// Options merged into the BrowserWindow constructor (main.js adds title/show/webPreferences).
function windowOptions() {
  return {
    width: DEFAULT_SIZE.width,
    height: DEFAULT_SIZE.height,
    minWidth: MIN_SIZE.width,
    minHeight: MIN_SIZE.height,
    // Overlay, not frame:false: Windows keeps the caption buttons and Snap Layouts.
    titleBarStyle: 'hidden',
    titleBarOverlay: { color: THEME.bg, symbolColor: THEME.symbol, height: THEME.overlayHeight },
    backgroundColor: THEME.bg,
    autoHideMenuBar: true,
  };
}

function windowStatePath(dataRoot) {
  return path.join(dataRoot, STATE_FILE);
}

// Mica only on Windows 11 (build >= 22000) and only when not opted out via VALOHUB_NO_MICA=1.
function micaAllowed({ platform, release, env }) {
  if (platform !== 'win32') return false;
  if (env && env.VALOHUB_NO_MICA === '1') return false;
  const m = /^\d+\.\d+\.(\d+)/.exec(String(release || ''));
  return !!m && Number(m[1]) >= MICA_MIN_BUILD;
}

// Hide the menu bar (Alt still reveals it) and apply Mica when allowed. Never throws.
// Returns the backdrop actually applied: 'mica' or 'solid'.
function applyChrome(win, opts) {
  try { win.setMenuBarVisibility(false); } catch { /* ignore: cosmetic */ }
  if (!micaAllowed(opts)) return 'solid';
  try {
    if (typeof win.setBackgroundMaterial !== 'function') return 'solid';
    win.setBackgroundMaterial('mica');
    return 'mica';
  } catch {
    return 'solid'; // silent fallback: keep the solid backgroundColor
  }
}

// Minimal menu: File > Import replay (Ctrl+O), Quit. View (reload/zoom/devtools) only in QA.
function menuTemplate({ qa, onImport, onQuit }) {
  const template = [{
    label: 'File',
    submenu: [
      { label: 'Import replay\u2026', accelerator: 'CmdOrCtrl+O', click: onImport },
      { type: 'separator' },
      { label: 'Quit', accelerator: 'Alt+F4', click: onQuit },
    ],
  }];
  if (qa) {
    const view = [{ role: 'reload' }, { role: 'zoomIn' }, { role: 'zoomOut' }, { role: 'resetZoom' }, { role: 'toggleDevTools' }];
    template.push({ label: 'View', submenu: view });
  }
  return template;
}

// Reads <dataRoot>/window-state.json. Corrupt or implausible content -> null (defaults used).
function readWindowState(file) {
  let raw;
  try { raw = JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
  if (!raw || typeof raw !== 'object') return null;
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? Math.round(v) : null);
  const width = num(raw.width);
  const height = num(raw.height);
  if (width === null || height === null || width < 1 || height < 1) return null;
  return { width, height, x: num(raw.x), y: num(raw.y), maximized: raw.maximized === true };
}

// Atomic write (tmp + rename). Returns false on failure instead of throwing.
function writeWindowState(file, state) {
  try {
    const tmp = `${file}.${process.pid}.tmp`;
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(tmp, JSON.stringify({
      width: state.width, height: state.height, x: state.x, y: state.y, maximized: !!state.maximized,
    }));
    fs.renameSync(tmp, file);
    return true;
  } catch { return false; }
}

// Fit a saved state onto the current displays. displays: [{ workArea: {x,y,width,height} }].
// A saved position is kept only if it overlaps some work area by >= 100px on both axes;
// otherwise the window is centred by Electron (x/y omitted). Size is clamped to min and to the
// largest work area.
function resolveBounds(state, displays) {
  const areas = (displays || []).map((d) => d && d.workArea).filter((a) => a && a.width > 0 && a.height > 0);
  const maxW = areas.length ? Math.max(...areas.map((a) => a.width)) : DEFAULT_SIZE.width;
  const maxH = areas.length ? Math.max(...areas.map((a) => a.height)) : DEFAULT_SIZE.height;
  const src = state || { ...DEFAULT_SIZE, x: null, y: null, maximized: false };
  const width = Math.max(MIN_SIZE.width, Math.min(src.width, maxW));
  const height = Math.max(MIN_SIZE.height, Math.min(src.height, maxH));
  const out = { width, height, maximized: !!src.maximized };
  if (typeof src.x !== 'number' || typeof src.y !== 'number') return out;
  const hit = areas.find((a) => {
    const ox = Math.min(src.x + width, a.x + a.width) - Math.max(src.x, a.x);
    const oy = Math.min(src.y + height, a.y + a.height) - Math.max(src.y, a.y);
    return ox >= 100 && oy >= 100;
  });
  if (!hit) return out; // off-screen (monitor unplugged): let Electron centre it
  out.x = Math.max(hit.x, Math.min(src.x, hit.x + hit.width - width));
  out.y = Math.max(hit.y, Math.min(src.y, hit.y + hit.height - height));
  return out;
}

module.exports = {
  THEME, DEFAULT_SIZE, MIN_SIZE, APP_USER_MODEL_ID, STATE_FILE,
  windowOptions, windowStatePath, micaAllowed, applyChrome, menuTemplate,
  readWindowState, writeWindowState, resolveBounds,
};
