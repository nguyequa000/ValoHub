'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const W = require('../src/window');

function fakeWin({ throwMaterial = false } = {}) {
  const calls = [];
  return {
    calls,
    setMenuBarVisibility(v) { calls.push(['setMenuBarVisibility', v]); },
    setBackgroundMaterial(m) { if (throwMaterial) throw new Error('unsupported'); calls.push(['setBackgroundMaterial', m]); },
    setTransparent() { calls.push(['setTransparent']); },
    setOpacity() { calls.push(['setOpacity']); },
  };
}

test('window options: overlay titlebar, no frame:false, 900x560 min, 1440x900 default', () => {
  const o = W.windowOptions();
  assert.strictEqual(o.titleBarStyle, 'hidden');
  assert.deepStrictEqual(o.titleBarOverlay, { color: '#0B0F16', symbolColor: '#E7ECF5', height: 40 });
  assert.strictEqual(o.minWidth, 900);
  assert.strictEqual(o.minHeight, 560);
  assert.strictEqual(o.width, 1440);
  assert.strictEqual(o.height, 900);
  assert.strictEqual(o.backgroundColor, '#0B0F16');
  assert.strictEqual(o.autoHideMenuBar, true);
  assert.ok(!('frame' in o), 'frame must not be set');
  assert.ok(!('transparent' in o), 'window must not be transparent');
});

test('menu bar is hidden on every platform path (Alt still reveals it)', () => {
  const w = fakeWin();
  W.applyChrome(w, { platform: 'win32', release: '10.0.19045', env: {} });
  assert.deepStrictEqual(w.calls[0], ['setMenuBarVisibility', false]);
});

test('mica: applied only on win32 build >= 22000', () => {
  const env = {};
  assert.strictEqual(W.micaAllowed({ platform: 'win32', release: '10.0.22000', env }), true);
  assert.strictEqual(W.micaAllowed({ platform: 'win32', release: '10.0.26200', env }), true);
  assert.strictEqual(W.micaAllowed({ platform: 'win32', release: '10.0.21999', env }), false);
  assert.strictEqual(W.micaAllowed({ platform: 'linux', release: '10.0.26200', env }), false);
  assert.strictEqual(W.micaAllowed({ platform: 'win32', release: 'garbage', env }), false);
});

test('mica: VALOHUB_NO_MICA=1 opts out', () => {
  assert.strictEqual(W.micaAllowed({ platform: 'win32', release: '10.0.26200', env: { VALOHUB_NO_MICA: '1' } }), false);
  const w = fakeWin();
  assert.strictEqual(W.applyChrome(w, { platform: 'win32', release: '10.0.26200', env: { VALOHUB_NO_MICA: '1' } }), 'solid');
  assert.ok(!w.calls.some((c) => c[0] === 'setBackgroundMaterial'));
});

test('mica: applied on Win11 and never makes the window transparent', () => {
  const w = fakeWin();
  assert.strictEqual(W.applyChrome(w, { platform: 'win32', release: '10.0.26200', env: {} }), 'mica');
  assert.deepStrictEqual(w.calls.find((c) => c[0] === 'setBackgroundMaterial'), ['setBackgroundMaterial', 'mica']);
  assert.ok(!w.calls.some((c) => c[0] === 'setTransparent' || c[0] === 'setOpacity'));
});

test('mica: setBackgroundMaterial throwing -> silent solid fallback', () => {
  const w = fakeWin({ throwMaterial: true });
  assert.strictEqual(W.applyChrome(w, { platform: 'win32', release: '10.0.26200', env: {} }), 'solid');
  assert.deepStrictEqual(w.calls[0], ['setMenuBarVisibility', false]);
});

test('menu: non-QA has File>Import (CmdOrCtrl+O) and Quit only, no View', () => {
  const t = W.menuTemplate({ qa: false, onImport() {}, onQuit() {} });
  assert.deepStrictEqual(t.map((m) => m.label), ['File']);
  const items = t[0].submenu.filter((i) => i.label);
  assert.deepStrictEqual(items.map((i) => i.label), ['Import replay\u2026', 'Quit']);
  assert.strictEqual(items[0].accelerator, 'CmdOrCtrl+O');
  assert.ok(JSON.stringify(t).indexOf('toggleDevTools') === -1);
});

test('menu: QA adds View with reload/zoom/devtools', () => {
  const t = W.menuTemplate({ qa: true, onImport() {}, onQuit() {} });
  assert.deepStrictEqual(t.map((m) => m.label), ['File', 'View']);
  const roles = t[1].submenu.map((i) => i.role);
  assert.deepStrictEqual(roles, ['reload', 'zoomIn', 'zoomOut', 'resetZoom', 'toggleDevTools']);
});

test('state file: corrupt / missing / implausible -> null, valid -> parsed', () => {
  const dir = fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), 'vp-winstate-'));
  const f = path.join(dir, 'window-state.json');
  assert.strictEqual(W.readWindowState(f), null, 'missing');
  fs.writeFileSync(f, '{not json');
  assert.strictEqual(W.readWindowState(f), null, 'corrupt');
  fs.writeFileSync(f, JSON.stringify({ width: 'big', height: 800 }));
  assert.strictEqual(W.readWindowState(f), null, 'non-number width');
  fs.writeFileSync(f, JSON.stringify({ width: 0, height: 800 }));
  assert.strictEqual(W.readWindowState(f), null, 'zero width');
  fs.writeFileSync(f, 'null');
  assert.strictEqual(W.readWindowState(f), null, 'null json');
  assert.ok(W.writeWindowState(f, { width: 1200, height: 800, x: 100.4, y: 50, maximized: true }));
  assert.deepStrictEqual(W.readWindowState(f), { width: 1200, height: 800, x: 100, y: 50, maximized: true });
  assert.deepStrictEqual(fs.readdirSync(dir).sort(), ['window-state.json'], 'no tmp leftovers');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('state file path lives in the data root', () => {
  assert.strictEqual(W.windowStatePath(path.join('x', 'home')), path.join('x', 'home', 'window-state.json'));
});

test('resolveBounds: null state -> default size, centred (no x/y)', () => {
  const b = W.resolveBounds(null, [{ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }]);
  assert.deepStrictEqual(b, { width: 1440, height: 900, maximized: false });
});

test('resolveBounds: keeps a visible saved position', () => {
  const b = W.resolveBounds({ width: 1200, height: 800, x: 200, y: 100, maximized: false },
    [{ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }]);
  assert.deepStrictEqual(b, { width: 1200, height: 800, maximized: false, x: 200, y: 100 });
});

test('resolveBounds: position on an unplugged monitor -> centred', () => {
  const b = W.resolveBounds({ width: 1200, height: 800, x: 5000, y: 100, maximized: false },
    [{ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }]);
  assert.strictEqual(b.x, undefined);
  assert.strictEqual(b.y, undefined);
  assert.strictEqual(b.width, 1200);
});

test('resolveBounds: partly off-screen window is pulled back inside the work area', () => {
  const b = W.resolveBounds({ width: 1200, height: 800, x: 1800, y: 900, maximized: false },
    [{ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }]);
  assert.strictEqual(b.x, 1920 - 1200);
  assert.strictEqual(b.y, 1040 - 800);
  // less than 100px overlap on an axis counts as off-screen -> centred
  const c = W.resolveBounds({ width: 1200, height: 800, x: 1800, y: 1000, maximized: false },
    [{ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }]);
  assert.strictEqual(c.y, undefined);
});

test('resolveBounds: size clamped to min 900x560 and to largest display', () => {
  const small = W.resolveBounds({ width: 300, height: 200, x: null, y: null, maximized: false }, [{ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }]);
  assert.deepStrictEqual([small.width, small.height], [900, 560]);
  const big = W.resolveBounds({ width: 5000, height: 4000, x: null, y: null, maximized: true }, [{ workArea: { x: 0, y: 0, width: 1920, height: 1040 } }]);
  assert.deepStrictEqual([big.width, big.height, big.maximized], [1920, 1040, true]);
});

test('main.js keeps the security hooks and has no frame:false', () => {
  const src = fs.readFileSync(path.join(__dirname, '..', 'src', 'main.js'), 'utf8');
  for (const needle of ['setWindowOpenHandler', 'setPermissionRequestHandler', 'setPermissionCheckHandler', "will-attach-webview", 'requestSingleInstanceLock', 'webviewTag: false', 'sandbox: true']) {
    assert.ok(src.includes(needle), `missing ${needle}`);
  }
  assert.ok(!/frame\s*:\s*false/.test(src), 'frame:false must not appear');
  assert.ok(src.includes("setAppUserModelId(W.APP_USER_MODEL_ID)"), 'AppUserModelId set');
  assert.ok(src.includes('W.applyChrome('), 'chrome applied in createWindow');
});
