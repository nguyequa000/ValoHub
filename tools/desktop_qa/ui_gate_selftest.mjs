#!/usr/bin/env node
/**
 * Self-test for tools/desktop_qa/ui_gate.mjs (v2).
 *
 *   node tools/desktop_qa/ui_gate_selftest.mjs [--keep]
 *
 * Proves three things, not just that the gate passes:
 *
 *   1. WCAG maths — contrast ratio / threshold / hit-area classes from `ui_gate_probe.mjs`.
 *   2. A clean synthetic v2 shell produces zero fail findings from the gate's own evaluators
 *      (`collectShellFindings` + `collectVisualFindings`), i.e. the checks are not vacuous.
 *   3. MUTATION TEST — deliberate DOM/CSS mutations are applied to that clean page, one at a time, and the
 *      gate must flip exactly the expected check id on each one (a broken DOM is caught, and the gate is
 *      not "all pass" or "all fail"). The table also covers the "space stuck on a skeleton" and
 *      "two rail items lit" regressions that the v2 rewrite fixed.
 *   4. CSP contrast — the same inline script executes on a page served without a CSP header and is
 *      blocked on a page served with the v2 policy, so `s2-inline-blocked` is not a false pass.
 *
 * Ports 19633 (fixture server) / 19634 (headless browser): inside the Q1 lane range.
 * Requires Node >= 22 and chrome/msedge. Exit 0 = self-test passed.
 */
import http from 'node:http';
import { rm } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {
  Cdp, launchBrowser, resolveBrowser, probe, shellProbeFn, visualProbeFn, collectShellFindings,
  collectVisualFindings, collectControlFindings, clickProbe, cspProbeFn, INTERACTIVE_SELECTOR,
} from './ui_gate.mjs';
import { contrastRatio, contrastThreshold, hitAreaClass, parseColor } from './ui_gate_probe.mjs';

const SERVE_PORT = 19633;
const BROWSER_PORT = 19634;
const CSP_V2 = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
  + "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; "
  + "object-src 'none'";
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const CLEAN = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>v2 shell fixture</title><style>
  :root{ --bg:#0B0F16; --surface:#111725; --text:#E7ECF5; --dim:#A9B4C6; --accent:#7C5CFF; --border:#26344A; }
  html,body{margin:0;background:var(--bg);color:var(--text);font:13px/18px "Segoe UI",system-ui,sans-serif;height:100%}
  body{display:grid;grid-template-rows:40px minmax(0,1fr) 24px;overflow:hidden}
  #titlebar{display:grid;grid-template-columns:1fr 320px 1fr;align-items:center;gap:8px;padding:0 8px;background:var(--bg);border-bottom:1px solid var(--border);-webkit-app-region:drag;user-select:none}
  #titlebar button{-webkit-app-region:no-drag;min-height:28px}
  .brand{font-weight:600}
  .app-body{display:grid;grid-template-columns:56px minmax(0,1fr);min-height:0}
  #rail{display:flex;flex-direction:column;gap:4px;padding:4px;background:var(--surface);border-right:1px solid var(--border)}
  #rail a{display:flex;align-items:center;justify-content:center;min-height:40px;min-width:48px;color:var(--dim);text-decoration:none;border-radius:6px}
  #rail a[aria-current="page"]{color:var(--text);background:#1C2839;box-shadow:inset 3px 0 0 var(--accent)}
  #context-bar{display:flex;align-items:center;justify-content:space-between;height:48px;padding:0 12px;border-bottom:1px solid var(--border)}
  #context-bar h1{margin:0;font-size:16px}
  #space{padding:12px;overflow:auto;min-height:0}
  #statusbar{display:flex;align-items:center;gap:6px;padding:0 8px;background:var(--surface);border-top:1px solid var(--border);font-size:12px}
  #statusbar button{min-height:24px;min-width:24px;background:transparent;color:var(--dim);border:0}
  .btn{min-height:28px;min-width:28px;padding:0 12px;border-radius:6px;border:1px solid var(--border);background:var(--surface);color:var(--text)}
  .btn--primary{background:#5A3FD0;color:#fff;border-color:#5A3FD0}
  :focus{outline:none}
  :focus-visible{outline:2px solid var(--accent);outline-offset:2px}
  .card{background:var(--surface);border:1px solid var(--border);border-radius:8px;padding:12px;margin:8px 0}
  @media (prefers-reduced-motion:reduce){ *{transition:none!important;animation:none!important} }
</style></head><body data-space="library">
  <div id="titlebar" data-testid="titlebar">
    <div class="brand"><span class="mark" aria-hidden="true">▦</span><span>ValoHub</span><span class="space">Library</span></div>
    <button type="button" id="palette-trigger" data-testid="palette-trigger" aria-label="Open command palette">Search or jump to… Ctrl K</button>
    <div><button type="button" data-testid="provider-chip" aria-label="AI provider: none">No model</button></div>
  </div>
  <div class="app-body">
    <nav id="rail" data-testid="rail" aria-label="Primary">
      <a href="/replays" data-route="library" aria-current="page" aria-label="Library">▦</a>
      <a href="/analysis" data-route="analysis" aria-label="Analysis">◫</a>
      <a href="/coach" data-route="coach" aria-label="Coach">✦</a>
      <a href="/providers" data-route="providers" aria-label="AI Providers">⚙</a>
      <a href="/settings" data-route="settings" aria-label="Settings">☰</a>
    </nav>
    <div>
      <header id="context-bar" data-testid="context-bar">
        <h1 id="context-title">Library</h1>
        <button type="button" class="btn btn--primary" id="primary" data-testid="primary" aria-label="Import a replay">Import</button>
      </header>
      <main id="space" data-testid="space" tabindex="-1">
        <div class="card" data-testid="library">
          <h2>2 replays</h2>
          <p>Haven · 13.06 · 2:58</p>
          <button type="button" class="btn" id="open">Open replay</button>
        </div>
      </main>
    </div>
  </div>
  <footer id="statusbar" data-testid="statusbar">
    <button type="button" data-testid="status-parser">Parser: ready</button>
    <button type="button" data-testid="status-provider">Model: none</button>
    <button type="button" data-testid="status-build">build 0.3.0</button>
  </footer>
  <script src="/clean.js"></script>
</body></html>`;

/* The clean fixture must look like the real app shell: NO inline script, so it keeps working under
 * the v2 CSP (`script-src 'self'`). A handler defined inline would be silently blocked and the
 * "wired control" probe below would then be indistinguishable from a dead control. */
const CLEAN_JS = `
document.getElementById('open').addEventListener('click', () => {
  document.getElementById('space').dataset.opened = '1';
  document.getElementById('context-title').textContent = 'Library · opened';
});
document.getElementById('primary').addEventListener('click', () => {
  document.getElementById('statusbar').dataset.importing = '1';
});
`;

/* ---------------------------------------------------------------- unit checks */

let failed = 0;
const check = (name, ok, detail = '') => {
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`);
  if (!ok) failed += 1;
};

function unitChecks() {
  check('unit: white/black ratio = 21', Math.abs(contrastRatio([255, 255, 255], [0, 0, 0]) - 21) < 0.1);
  check('unit: #E7ECF5 on #0B0F16 = 14+', contrastRatio([231, 236, 245], [11, 15, 22]) > 14,
    `${contrastRatio([231, 236, 245], [11, 15, 22]).toFixed(2)}:1`);
  check('unit: interaction accent #7C5CFF clears the 3:1 UI floor', contrastRatio([124, 92, 255], [11, 15, 22]) >= 3,
    `${contrastRatio([124, 92, 255], [11, 15, 22]).toFixed(2)}:1`);
  check('unit: threshold 24px regular = 3', contrastThreshold(24, 400) === 3);
  check('unit: threshold 13px regular = 4.5', contrastThreshold(13, 400) === 4.5);
  check('unit: hit 24x24 ok', hitAreaClass(24, 24) === 'ok');
  check('unit: hit 20x20 warn', hitAreaClass(20, 20) === 'warn');
  check('unit: hit 8x8 hard', hitAreaClass(8, 8) === 'hard');
  check('unit: parse rgba', JSON.stringify(parseColor('rgba(16, 21, 28, 0.5)')) === JSON.stringify({ r: 16, g: 21, b: 28, a: 0.5 }));
}

/* ---------------------------------------------------------------- page driving */

async function loadClean(cdp, { withCsp = true } = {}) {
  const url = `http://127.0.0.1:${SERVE_PORT}/${withCsp ? 'clean.html' : 'nocsp.html'}`;
  await cdp.send('Page.navigate', { url });
  for (let i = 0; i < 60; i += 1) {
    const ok = await cdp.evaluate("document.readyState === 'complete' && !!document.querySelector('#space')").catch(() => false);
    if (ok) break;
    await sleep(80);
  }
  await sleep(120);
}

async function gather(cdp, { reducedMotion = true } = {}) {
  await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: reducedMotion ? 'reduce' : 'no-preference' }] });
  const shell = await probe(cdp, shellProbeFn, { spaceRoot: '[data-testid="library"]', interactiveSelector: INTERACTIVE_SELECTOR });
  const visual = await probe(cdp, visualProbeFn, { spaceRoot: '[data-testid="library"]', interactiveSelector: INTERACTIVE_SELECTOR, reducedMotion });
  const shellFindings = collectShellFindings(shell, { route: 'library', space: 'library' });
  const controlFindings = collectControlFindings(shell, { route: 'library' }).findings;
  const visualFindings = collectVisualFindings(visual, { label: '1280x800', primaryRequired: true });
  return { shell, visual, findings: [...shellFindings, ...controlFindings, ...visualFindings] };
}

const failsOf = (findings) => findings.filter((f) => f.severity === 'fail');

/* ---------------------------------------------------------------- mutation table */

// Each entry reloads the clean page, applies `mutate`, re-probes and asserts the expected id fails.
// `alsoPass` ids must stay clean, which is what stops a "fail everything" harness from passing.
const MUTATIONS = [
  {
    name: 'remove the status bar',
    mutate: "document.querySelector('#statusbar').remove()",
    expect: ['r1-shell'],
  },
  {
    name: 'empty the space',
    mutate: "document.querySelector('#space').replaceChildren()",
    expect: ['r1-shell'],
  },
  {
    name: 'duplicate the product brand',
    mutate: "const d=document.createElement('div'); d.textContent='ValoHub'; document.querySelector('#titlebar').appendChild(d);",
    expect: ['c4-single-brand'],
  },
  {
    name: 'add a menubar',
    mutate: "const m=document.createElement('div'); m.setAttribute('role','menubar'); m.textContent='File Edit View'; document.querySelector('#context-bar').appendChild(m);",
    expect: ['c1-no-dom-menu'],
  },
  {
    name: 'remove the titlebar drag region',
    mutate: "const t=document.querySelector('#titlebar'); t.style.setProperty('-webkit-app-region','no-drag'); for (const el of t.querySelectorAll('*')) el.style.setProperty('-webkit-app-region','no-drag');",
    expect: ['c2-drag-region'],
  },
  {
    name: 'add a sixth rail item with a duplicate href',
    mutate: "const a=document.createElement('a'); a.href='/replays'; a.setAttribute('aria-label','Extra'); a.textContent='▦'; document.querySelector('#rail').appendChild(a);",
    expect: ['c3-rail-5'],
  },
  {
    name: 'add an "↗" link',
    // Sized and coloured like a real chrome link so ONLY the arrow-text rule fires (an unstyled
    // inline <a> would also trip target-size/contrast and make the mutation imprecise).
    mutate: "const a=document.createElement('a'); a.href='#'; a.textContent='Docs ↗'; a.style.cssText='display:inline-block;min-height:28px;padding:4px 8px;color:#E7ECF5'; document.querySelector('#space').appendChild(a);",
    expect: ['c5-no-arrow-links'],
  },
  {
    name: 'add breadcrumbs',
    mutate: "const n=document.createElement('nav'); n.className='breadcrumb'; n.textContent='Library / Replay'; document.querySelector('#context-bar').appendChild(n);",
    expect: ['c6-no-breadcrumbs'],
  },
  {
    name: 'add a nameless button and a disabled button with no reason',
    // Both get real hit areas + readable text, otherwise target-size/contrast join in and the
    // mutation no longer proves that d1/d2 are the rules doing the work.
    mutate: "const s='min-height:28px;min-width:60px;padding:0 8px;color:#E7ECF5;background:#111725;border:1px solid #26344A'; const b=document.createElement('button'); b.id='nameless'; b.style.cssText=s; document.querySelector('#space').appendChild(b); const d=document.createElement('button'); d.id='noreason'; d.disabled=true; d.textContent='Disabled'; d.style.cssText=s; document.querySelector('#space').appendChild(d);",
    expect: ['d1-accessible-name', 'd2-affordance'],
  },
  {
    name: 'widen the page past the viewport',
    mutate: "const w=document.createElement('div'); w.style.cssText='position:absolute;left:0;top:0;width:2200px;height:20px'; document.body.appendChild(w);",
    expect: ['v1-overflow'],
  },
  {
    name: 'shrink a target below 24px',
    mutate: "const b=document.createElement('button'); b.id='tiny'; b.style.width='8px'; b.style.height='8px'; b.setAttribute('aria-label','Tiny'); b.textContent='.'; document.querySelector('#space').appendChild(b);",
    expect: ['v3-target-size'],
  },
  {
    name: 'paint low-contrast text',
    mutate: "const p=document.createElement('p'); p.id='lowcontrast'; p.style.color='#2a2a2a'; p.textContent='low contrast text here'; document.querySelector('#space').appendChild(p);",
    expect: ['v4-contrast'],
  },
  {
    name: 'keep animating under prefers-reduced-motion',
    mutate: "const s=document.createElement('style'); s.textContent='#rail a{transition:transform 180ms ease-out!important}'; document.head.appendChild(s);",
    expect: ['v5-reduced-motion'],
  },
  {
    // A space stuck on the mount skeleton must be reported as such; "any child in #space" is not content.
    name: 'leave the space stuck on its loading skeleton',
    mutate: "const sk=document.createElement('div'); sk.setAttribute('data-testid','skeleton'); sk.setAttribute('aria-busy','true'); sk.setAttribute('aria-label','Loading'); sk.className='skeleton'; document.querySelector('#space').replaceChildren(sk);",
    expect: ['r1-shell'],
  },
  {
    // Detail routes keep their parent rail item lit, but two lit items is still a failure.
    name: 'light two rail items at once',
    mutate: "document.querySelector('#rail a:not([aria-current])').setAttribute('aria-current','page');",
    expect: ['c3-rail-5'],
  },
];

async function main() {
  for (const r of ['--keep']) if (process.argv.includes(r)) { /* keep the profile for debugging */ }

  unitChecks();

  const server = http.createServer((req, res) => {
    const p = (req.url || '/').split('?')[0];
    const headers = { 'content-type': 'text/html; charset=utf-8' };
    if (p === '/clean.html') { res.writeHead(200, { ...headers, 'content-security-policy': CSP_V2 }); res.end(CLEAN); return; }
    if (p === '/nocsp.html') { res.writeHead(200, headers); res.end(CLEAN); return; }
    if (p === '/clean.js') {
      res.writeHead(200, { 'content-type': 'text/javascript; charset=utf-8', 'content-security-policy': CSP_V2 });
      res.end(CLEAN_JS);
      return;
    }
    res.writeHead(404); res.end('nope');
  });
  await new Promise((r) => server.listen(SERVE_PORT, '127.0.0.1', r));

  const userDataDir = path.join(process.env.TMPDIR || os.tmpdir(), `valohub-ui-gate-selftest-${process.pid}`);
  const browser = resolveBrowser();
  const launched = await launchBrowser({ ...browser, port: BROWSER_PORT, userDataDir, log: [] });
  const cdp = launched.cdp;
  try {
    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('DOM.enable').catch(() => {});
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });

    // ---- 2. clean page is clean
    await loadClean(cdp);
    const clean = await gather(cdp);
    const cleanFails = failsOf(clean.findings);
    check('clean page: zero fail findings', cleanFails.length === 0,
      cleanFails.map((f) => `${f.id}: ${f.detail}`).join(' | '));
    check('clean page: chrome checks all pass',
      ['r1-shell', 'c1-no-dom-menu', 'c2-drag-region', 'c3-rail-5', 'c4-single-brand', 'c5-no-arrow-links', 'c6-no-breadcrumbs']
        .every((id) => clean.findings.some((f) => f.id === id && f.severity === 'pass')),
      clean.findings.map((f) => `${f.id}:${f.severity}`).join(','));
    const controls = collectControlFindings(clean.shell, { route: 'library' });
    check('clean page: names + affordances pass', failsOf(controls.findings).length === 0,
      failsOf(controls.findings).map((f) => f.detail).join(' | '));

    // ---- 3. every mutation is caught by the expected id, and only that id
    for (const m of MUTATIONS) {
      await loadClean(cdp);
      await cdp.evaluate(m.mutate);
      await sleep(120);
      const g = await gather(cdp);
      const failedIds = new Set(failsOf(g.findings).map((f) => f.id));
      const missing = m.expect.filter((id) => !failedIds.has(id));
      const extra = [...failedIds].filter((id) => !m.expect.includes(id));
      check(`mutation: ${m.name} -> ${m.expect.join(', ')}`,
        missing.length === 0 && extra.length === 0,
        [missing.length ? `not caught: ${missing.join(',')}` : '', extra.length ? `collateral: ${extra.join(',')}` : '']
          .filter(Boolean).join('; '));
    }

    // ---- dead control: a button with no handler must fail the scripted click probe
    await loadClean(cdp);
    await cdp.evaluate("const b=document.createElement('button'); b.id='dead'; b.className='btn'; b.textContent='Does nothing'; b.style.minHeight='32px'; b.style.minWidth='60px'; document.querySelector('#space').appendChild(b);");
    const deadRes = await clickProbe(cdp, [{ selector: '#dead', name: 'Does nothing', region: 'space' }], { route: 'selftest', max: 1 });
    check('mutation: enabled button with no handler is flagged by the click probe',
      failsOf(deadRes.findings).some((f) => f.id === 'd3-click-probe'),
      deadRes.results.map((r) => r.status).join(','));
    const liveRes = await clickProbe(cdp, [{ selector: '#open', name: 'Open replay', region: 'space' }], { route: 'selftest', max: 1 });
    check('clean page: the same probe passes a wired button',
      failsOf(liveRes.findings).length === 0, liveRes.results.map((r) => `${r.status}`).join(','));

    // ---- 4. CSP: inline script runs without a header, is blocked with the v2 policy
    await loadClean(cdp, { withCsp: false });
    const noCsp = await cdp.evaluate(`(${cspProbeFn.toString()})()`, 8000);
    await loadClean(cdp, { withCsp: true });
    const withCsp = await cdp.evaluate(`(${cspProbeFn.toString()})()`, 8000);
    check('csp: the inline probe executes without a CSP header', noCsp.inlineRan === true,
      JSON.stringify(noCsp));
    check('csp: the same probe is blocked with the v2 policy', withCsp.inlineRan === false && withCsp.handlerRan === false,
      JSON.stringify(withCsp));
    check('csp: a securitypolicyviolation is reported', withCsp.violations.length > 0, JSON.stringify(withCsp.violations));
  } finally {
    cdp.close();
    launched.proc.kill();
    server.close();
    if (!process.argv.includes('--keep')) await rm(userDataDir, { recursive: true, force: true }).catch(() => {});
  }

  console.log(failed ? `\n${failed} self-test check(s) failed` : `\nself-test passed (clean page green, ${MUTATIONS.length} mutations caught)`);
  return failed ? 1 : 0;
}

main().then((code) => { process.exitCode = code; }).catch((e) => { console.error(e); process.exitCode = 2; });
