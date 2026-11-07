#!/usr/bin/env node
// Drive an app page over CDP and write report.json. See README.md.
import { CdpClient, waitForTargets } from './cdp.mjs';
import fs from 'node:fs';
import path from 'node:path';

const args = process.argv.slice(2);
const opt = (n, d) => { const i = args.indexOf('--' + n); return i >= 0 ? args[i + 1] : d; };
const port = Number(opt('debug-port', 19241));
const base = new URL(opt('base-url', 'http://127.0.0.1:18792'));
const ids = (opt('replay-ids', '') || '').split(',').filter(Boolean);
const expectCards = opt('expect-cards') !== undefined ? Number(opt('expect-cards')) : null;
const out = path.resolve(opt('out', 'qa-out'));
const minPainted = Number(opt('min-painted', 0.01));
const settleMs = Number(opt('settle-ms', 2500));
const cardSel = opt('card-selector', '.replay-card');
fs.mkdirSync(out, { recursive: true });

const report = { checks: [], requests: [], nonLoopback: [], blocked: [], screenshots: [], consoleErrors: [] };
const check = (name, ok, detail = '') => { report.checks.push({ name, ok: !!ok, detail: String(detail) }); console.log(`${ok ? 'PASS' : 'FAIL'} ${name} ${detail}`); };
const LOOP = new Set(['127.0.0.1', 'localhost', '[::1]', '::1']);
const hostOf = (u) => { try { return new URL(u).hostname; } catch { return ''; } };
const isLocalScheme = (u) => /^(data|blob|about|chrome|devtools|file):/i.test(u);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const reqs = new Map();
function hookNetwork(c) {
  c.on('Network.requestWillBeSent', (p) => {
    const r = { id: p.requestId, url: p.request.url, host: hostOf(p.request.url), status: null, blocked: false, error: null };
    reqs.set(p.requestId, r); report.requests.push(r);
  });
  c.on('Network.responseReceived', (p) => { const r = reqs.get(p.requestId); if (r) r.status = p.response.status; });
  c.on('Network.loadingFailed', (p) => {
    const r = reqs.get(p.requestId); if (!r) return;
    r.error = p.errorText;
    // Treat as blocked only when the browser/app policy refused it (not DNS/connection failure).
    if (p.blockedReason || /BLOCKED|ACCESS_DENIED|ABORTED/i.test(p.errorText || '')) r.blocked = true;
  });
  c.on('Runtime.consoleAPICalled', (p) => { if (p.type === 'error') report.consoleErrors.push(p.args.map((a) => a.value ?? a.description).join(' ')); });
  c.on('Runtime.exceptionThrown', (p) => report.consoleErrors.push('exception: ' + (p.exceptionDetails.exception?.description || p.exceptionDetails.text)));
  c.on('Log.entryAdded', (p) => { if (p.entry.level === 'error' && !/Failed to load resource/.test(p.entry.text)) report.consoleErrors.push(p.entry.text); });
}

async function nav(c, p) {
  const url = new URL(p, base);
  if (url.origin !== base.origin) throw new Error('refusing off-origin navigation ' + url);
  const loaded = c.once('Page.loadEventFired', () => true, 20000).catch(() => null);
  await c.send('Page.navigate', { url: url.href });
  await loaded; await sleep(settleMs);
}
async function shot(c, name) {
  const r = await c.send('Page.captureScreenshot', { format: 'png' });
  const f = path.join(out, name + '.png'); fs.writeFileSync(f, Buffer.from(r.data, 'base64')); report.screenshots.push(f); return f;
}

const PAINT_JS = `(() => {
  const res = [];
  for (const cv of document.querySelectorAll('canvas')) {
    const w = cv.width, h = cv.height;
    if (!w || !h) { res.push({ id: cv.id, w, h, fraction: 0, note: 'zero size' }); continue; }
    try {
      const d = cv.getContext('2d', { willReadFrequently: true }).getImageData(0, 0, w, h).data;
      const step = Math.max(1, Math.floor(Math.sqrt(w * h / 40000)));
      const counts = new Map(); let n = 0, opaque = 0;
      for (let y = 0; y < h; y += step) for (let x = 0; x < w; x += step) {
        const i = (y * w + x) * 4; n++;
        if (d[i + 3] < 8) continue; opaque++;
        const k = (d[i] >> 3) + ',' + (d[i + 1] >> 3) + ',' + (d[i + 2] >> 3);
        counts.set(k, (counts.get(k) || 0) + 1);
      }
      // background = most common opaque colour (or transparent); fraction = everything else
      let top = 0; for (const v of counts.values()) top = Math.max(top, v);
      const fraction = n ? (opaque - top) / n : 0;
      res.push({ id: cv.id, w, h, fraction, opaqueFraction: n ? opaque / n : 0 });
    } catch (e) {
      try { // WebGL or tainted: fall back to "context exists" only, flag it
        res.push({ id: cv.id, w, h, fraction: -1, note: String(e) });
      } catch {}
    }
  }
  return res;
})()`;

async function paintedCheck(c, label) {
  const cvs = await c.evaluate(PAINT_JS);
  check(`${label}: canvas exists`, cvs.length > 0, `canvases=${cvs.length}`);
  const best = cvs.reduce((m, x) => Math.max(m, x.fraction), -2);
  report[`canvas:${label}`] = cvs;
  check(`${label}: canvas painted`, best >= minPainted, `best non-background fraction=${best.toFixed(4)} (min ${minPainted}) ${JSON.stringify(cvs.map((x) => [x.id, x.w, x.h, +x.fraction.toFixed(4)]))}`);
}
async function imageCheck(c, label) {
  const r = await c.evaluate(`(() => { const im=[...document.images]; const ok=im.filter(i=>i.naturalWidth>0).length; const broken=im.filter(i=>i.complete&&i.naturalWidth===0).map(i=>i.currentSrc||i.src); return {total:im.length, ok, broken}; })()`);
  check(`${label}: images load`, r.broken.length === 0, `loaded=${r.ok}/${r.total} broken=${JSON.stringify(r.broken.slice(0, 5))}`);
  report[`images:${label}`] = r;
}
async function sandboxCheck(c) {
  const r = await c.evaluate(`({req: typeof window.require, proc: typeof window.process, open: (() => { try { return window.open('about:blank') } catch (e) { return 'threw' } })()})`);
  check('sandbox: window.require undefined', r.req === 'undefined', r.req);
  check('sandbox: window.process undefined', r.proc === 'undefined', r.proc);
  check('sandbox: window.open returns null', r.open === null, JSON.stringify(r.open));
}

let c;
try {
  const targets = await waitForTargets(port, { timeoutMs: 20000 });
  const t = targets.find((x) => hostOf(x.url) && new URL(x.url).origin === base.origin) || targets[0];
  c = await CdpClient.connect(t.webSocketDebuggerUrl);
  for (const d of ['Page', 'Runtime', 'Network', 'Log']) await c.send(d + '.enable');
  hookNetwork(c);

  await nav(c, '/replays');
  const cards = await c.evaluate(`document.querySelectorAll(${JSON.stringify(cardSel)}).length`);
  check('/replays: cards', expectCards === null ? true : cards === expectCards, `cards=${cards} expected=${expectCards ?? 'any'}`);
  await shot(c, 'replays');
  await sandboxCheck(c);
  for (const id of ids) {
    await nav(c, `/replays/${id}/view`);
    await paintedCheck(c, `view ${id}`); await imageCheck(c, `view ${id}`); await shot(c, `view-${id}`);
    await nav(c, `/replays/${id}/analysis`);
    const n = await c.evaluate('document.querySelectorAll("canvas").length');
    check(`analysis ${id}: loads`, n > 0, `canvases=${n}`); await shot(c, `analysis-${id}`);
  }
  await sleep(500);
  check('no console errors', report.consoleErrors.length === 0, JSON.stringify(report.consoleErrors.slice(0, 5)));
} catch (e) {
  check('harness run', false, e.stack || e.message);
} finally { if (c) c.close(); }

for (const r of report.requests) {
  if (isLocalScheme(r.url)) continue;
  if (!LOOP.has(r.host)) { report.nonLoopback.push(r.url); if (r.blocked) report.blocked.push(r.url); }
}
const leaked = report.nonLoopback.filter((u) => !report.blocked.includes(u));
check('no unblocked non-loopback requests', leaked.length === 0, JSON.stringify(leaked.slice(0, 5)));
for (const r of report.requests) delete r.id;
fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
const failed = report.checks.filter((x) => !x.ok).length;
console.log(`checks=${report.checks.length} failed=${failed} nonLoopback=${report.nonLoopback.length} blocked=${report.blocked.length}`);
process.exit(failed ? 1 : 0);
