'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');
const { Supervisor, request } = require('../src/supervisor');
const { RotatingLog } = require('../src/logger');

const PY = process.env.FAKE_PYTHON || path.join(process.env.LOCALAPPDATA || '', 'Programs/Python/Python312/python.exe');
const FAKE = path.join(__dirname, 'fake_sidecar.py');
const ROOT = path.resolve(__dirname, '..');
const ELECTRON = path.join(ROOT, 'node_modules', 'electron', 'dist', 'electron.exe');

function tmp(name) { return fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), `vp-${name}-`)); }
function mk(port, mode, extraEnv = {}, opts = {}) {
  const dir = tmp('sup');
  const token = 'TOK' + Math.random().toString(36).slice(2) + 'ZZ' + Date.now();
  const log = new RotatingLog(path.join(dir, 'electron.log'), 1024 * 1024, 5, [token]);
  const sup = new Supervisor(Object.assign({
    command: PY, args: [FAKE, 'serve', '--port', String(port), '--data-dir', dir], token, log,
    env: Object.assign({ FAKE_MODE: mode }, extraEnv), readyTimeoutMs: 8000,
  }, opts));
  return { sup, dir, token, log };
}
const alive = (pid) => { try { process.kill(pid, 0); return true; } catch { return false; } };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
async function waitGone(pid, ms = 4000) { const end = Date.now() + ms; while (Date.now() < end) { if (!alive(pid)) return true; await sleep(100); } return !alive(pid); }

test('prerequisite: python and electron present', () => {
  assert.ok(fs.existsSync(PY), `python not found: ${PY}`);
});

test('ready ok; token enforced by fake (401 without, 200 with); token not on argv/log', async () => {
  const { sup, token, dir } = mk(18824, 'normal');
  const r = await sup.start();
  try {
    assert.strictEqual(r.port, 18824);
    const noTok = await request('GET', r.port, '/api/ping', '');
    assert.strictEqual(noTok.status, 401); // negative control
    const withTok = await request('GET', r.port, '/api/ping', token);
    assert.strictEqual(withTok.status, 200);
    assert.ok(!sup.o.args.join(' ').includes(token), 'token on argv list');
    const cl = spawnSync('powershell', ['-NoProfile', '-Command', `(Get-CimInstance Win32_Process -Filter "ProcessId=${r.pid}").CommandLine`], { encoding: 'utf8' });
    assert.ok(cl.stdout.includes('fake_sidecar.py'), 'could not read child command line: ' + cl.stdout + cl.stderr);
    assert.ok(!cl.stdout.includes(token), 'token present on real process command line');
    sup.o.log.info('token=' + token); // scrubber must redact even deliberate leaks
  } finally { assert.strictEqual(await sup.stop(3000), 'graceful'); }
  assert.ok(!fs.readFileSync(path.join(dir, 'electron.log'), 'utf8').includes(token), 'token leaked to log');
});

test('ready timeout (hang) -> READY_TIMEOUT and child is gone', async () => {
  const { sup } = mk(18825, 'hang', {}, { readyTimeoutMs: 1500 });
  await assert.rejects(sup.start(), (e) => e.code === 'READY_TIMEOUT');
  assert.ok(await waitGone(sup.child.pid), 'hung child still alive');
});

test('early exit -> EARLY_EXIT with stderr tail', async () => {
  const { sup } = mk(18826, 'early_exit');
  await assert.rejects(sup.start(), (e) => e.code === 'EARLY_EXIT' && /early exit/.test(e.stderrTail));
});

test('bad ready line -> BAD_READY and child killed', async () => {
  const { sup } = mk(18826, 'bad_ready');
  await assert.rejects(sup.start(), (e) => e.code === 'BAD_READY');
  assert.ok(await waitGone(sup.child.pid));
});

test('missing executable -> SPAWN_FAILED', async () => {
  const sup = new Supervisor({ command: path.join(os.tmpdir(), 'nope', 'vrb.exe'), args: [], token: 't', readyTimeoutMs: 2000 });
  await assert.rejects(sup.start(), (e) => e.code === 'SPAWN_FAILED');
});

test('escalation: shutdown ignored -> taskkill /t /f kills sidecar AND grandchild', async () => {
  const dir = tmp('gc'); const pidfile = path.join(dir, 'gc.pid');
  const { sup } = mk(18827, 'ignore_shutdown,grandchild', { FAKE_PIDFILE: pidfile });
  const r = await sup.start();
  const gc = Number(fs.readFileSync(pidfile, 'utf8'));
  assert.ok(alive(gc) && alive(r.pid), 'precondition: both alive');
  const how = await sup.stop(1200);
  assert.strictEqual(how, 'taskkill');
  assert.ok(await waitGone(r.pid), 'sidecar alive');
  assert.ok(await waitGone(gc), 'grandchild orphaned');
});

test('crash after start emits crashed once with stderr tail; restart gets new token + port', async () => {
  const a = mk(18828, 'crash', { FAKE_CRASH_S: '0.8' });
  const ev = new Promise((res) => a.sup.once('crashed', res));
  await a.sup.start();
  const info = await ev;
  assert.strictEqual(info.code, 3);
  assert.match(info.stderrTail, /boom/);
  // restart: new supervisor, new token, ephemeral port (preferred busy fallback)
  const b = mk(18828, 'busy_port');
  const rb = await b.sup.start();
  try {
    assert.notStrictEqual(b.token, a.token);
    assert.notStrictEqual(rb.port, 18828);
    assert.strictEqual((await request('GET', rb.port, '/api/ping', a.token)).status, 401); // old token dead
    assert.strictEqual((await request('GET', rb.port, '/api/ping', b.token)).status, 200);
  } finally { await b.sup.stop(3000); }
});

test('SSE route works through the token', async () => {
  const { sup, token } = mk(18829, 'normal');
  const r = await sup.start();
  try {
    const res = await request('GET', r.port, '/api/events', token);
    assert.strictEqual(res.status, 200);
    assert.match(res.body, /data: hello/);
  } finally { await sup.stop(3000); }
});

function runElectron(home, port, extra, mode, secs, env2 = {}) {
  fs.mkdirSync(home, { recursive: true });
  const report = path.join(home, 'report.json'); const seen = path.join(home, 'seen.jsonl');
  const env = Object.assign({}, process.env, { VALOHUB_PORT: String(port), FAKE_MODE: mode, FAKE_SEEN: seen,
    VALOHUB_SIDECAR_CMD: JSON.stringify([PY, FAKE]) }, env2);
  const child = spawn(ELECTRON, ['.', '--home', home, '--smoke-seconds', String(secs), '--report', report, ...extra], { cwd: ROOT, env, stdio: 'ignore', windowsHide: false });
  const done = new Promise((res) => { const t0 = Date.now(); child.on('exit', (code) => res({ code, ms: Date.now() - t0 })); });
  return { child, done, report, seen };
}
const hasElectron = fs.existsSync(ELECTRON);

test('real Electron: external request denied + logged; header on fetch/SSE/WS; exit 0', { skip: !hasElectron, timeout: 90000 }, async () => {
  const home = tmp('el');
  const e = runElectron(home, 18821, [], 'external', 6);
  const r = await e.done;
  assert.strictEqual(r.code, 0);
  const rep = JSON.parse(fs.readFileSync(e.report, 'utf8'));
  assert.ok(rep.loaded && rep.port === 18821);
  assert.strictEqual(rep.title, 'fetch:200|es-ok|ws-ok'); // 200 => token header reached /api/*; es/ws ok
  const denied = fs.readFileSync(path.join(home, 'logs', 'network-denied.jsonl'), 'utf8');
  assert.match(denied, /evil\.invalid\/leak\.png/);
  const seen = fs.readFileSync(e.seen, 'utf8').trim().split('\n').map(JSON.parse);
  for (const p of ['/replays', '/static/app.js', '/api/ping', '/api/events', '/ws']) {
    const rec = seen.find((s) => s.path === p);
    assert.ok(rec && rec.token, `no token header on ${p}`);
  }
  assert.ok(!seen.some((s) => s.path.includes('evil')), 'external request reached sidecar?');
});

test('real Electron: second instance exits 0 fast and does not spawn a second sidecar', { skip: !hasElectron, timeout: 90000 }, async () => {
  const home = tmp('si');
  const first = runElectron(home, 18822, [], 'normal', 9);
  await sleep(3500);
  const second = runElectron(home, 18822, [], 'normal', 9);
  const r2 = await second.done;
  assert.strictEqual(r2.code, 0);
  assert.ok(r2.ms < 6000, `second instance lingered ${r2.ms} ms`);
  const r1 = await first.done;
  assert.strictEqual(r1.code, 0);
  const healthCalls = fs.readFileSync(first.seen, 'utf8').trim().split('\n').filter((l) => l.includes('/api/app/health')).length;
  assert.strictEqual(healthCalls, 1, 'second instance started its own sidecar');
});

test('--check: JSON, exit 0 with fake sidecar; exit 2 when data dir unwritable', { skip: !hasElectron, timeout: 60000 }, () => {
  const env = Object.assign({}, process.env, { VALOHUB_SIDECAR_CMD: JSON.stringify([PY, FAKE]) });
  const home = tmp('chk');
  const ok = spawnSync(ELECTRON, ['.', '--check', '--home', home], { cwd: ROOT, env, encoding: 'utf8' });
  assert.strictEqual(ok.status, 0, ok.stdout + ok.stderr);
  const j = JSON.parse(ok.stdout.trim().split('\n').pop());
  assert.strictEqual(j.dataWritable, true);
  const filePath = path.join(home, 'afile'); fs.writeFileSync(filePath, 'x');
  const bad = spawnSync(ELECTRON, ['.', '--check', '--home', path.join(filePath, 'sub')], { cwd: ROOT, env, encoding: 'utf8' });
  assert.strictEqual(bad.status, 2, bad.stdout + bad.stderr);
});
