#!/usr/bin/env node
/**
 * ValoHub desktop UI acceptance gate — v2 (single-page shell).
 *
 * Drives a real Chromium page over raw CDP (no npm deps) and asserts the v2 contract in
 * docs/ui/DESIGN.md §9 + SHELL-CONTRACT.md. Every v1 page model (library/viewer/
 * analysis HTML documents) is gone: the app is one `app.html` shell that routes on location.pathname.
 *
 * Checks (ids are stable; `--list-checks` prints them):
 *   r1-shell            every route renders titlebar + rail + statusbar + a non-empty space
 *   r2-console          no console error / uncaught exception on a route
 *   r3-network-scope    every request stays on loopback (127.0.0.1 / localhost / [::1])
 *   c1-no-dom-menu      no in-DOM menu bar (the native Electron menu is a MANUAL packaged step)
 *   c2-drag-region      a -webkit-app-region:drag region exists in the 40 px titlebar
 *   c3-rail-5           5 rail items, distinct hrefs, exactly one aria-current=page (the active one)
 *   c4-single-brand     the product name appears once in chrome (no duplicate brand)
 *   c5-no-arrow-links   no interactive element whose text/name contains "↗"
 *   c6-no-breadcrumbs   no breadcrumb nav / class / testid
 *   d1-accessible-name  every visible control has a non-empty accessible name
 *   d2-affordance       every enabled control has an href, a handler, or a disabled reason
 *   d3-click-probe      real click on every enabled non-destructive button changes something
 *   k1-palette-open     Ctrl+K opens the palette and focuses its input
 *   k2-palette-filter   typing narrows the palette list
 *   k3-palette-enter    Enter runs the command and navigates
 *   k4-palette-esc      Esc closes the palette and restores focus to the trigger
 *   k5-tab-order        Tab reaches rail -> context -> space in that order
 *   k6-focus-ring       keyboard focus paints an outline/box-shadow ring
 *   p1-provider-flow    connect -> models -> default -> test -> disconnect against a stub host
 *   p2-invalid-key      a rejected key renders "Invalid key (401)"
 *   p3-no-secrets       no key in storage / DOM / URL (exact + secret-shaped scan)
 *   v1-overflow         no horizontal overflow at 900x600,1280x800,1440x900,1920x1080,768x1024
 *   v2-primary-visible  the space's primary action is not clipped by the viewport
 *   v3-target-size      every visible target is >= 24x24 CSS px
 *   v4-contrast         sampled text stays >= 4.5:1 (3:1 for large text)
 *   v5-reduced-motion   prefers-reduced-motion:reduce zeroes transitions/animations
 *   m1-mount-budget     a space mount measures < 250 ms (performance.now around navigation)
 *   m2-layout-shift     cumulative layout shift after load stays under the 0.05 in-house budget
 *   s1-csp-header       every app response carries the exact v2 CSP
 *   s2-inline-blocked   an injected inline script does not execute (CSP script-src 'self')
 *
 * Input modes
 *   --base-url http://127.0.0.1:19631 [--token T]   run against an already-running server
 *   --home DIR [--fixture DIR]                      start the inbox desktop server on --port
 *   --home DIR --exe path/to/ValoHub.exe          start the PACKAGED app (CDP via VALOHUB_QA_DEBUG_PORT)
 *
 * Ports: lane range 19600-19699 (default 19631); 18792 (the user's live app) is refused.
 * Homes: %LOCALAPPDATA%\ValoHub is refused; use an isolated copy.
 *
 * Usage
 *   node tools/desktop_qa/ui_gate.mjs --home C:/tmp/v2-home --fixture tools/desktop_qa/fixtures/home \
 *        --port 19641 --out VALOPLANT_WORK/tmp/ui-v2-evidence/gate
 *
 * Requires Node >= 22 (global WebSocket/fetch) and a Chromium-family browser (chrome/msedge).
 * Exit codes: 0 pass, 1 contract failures, 2 harness error.
 */
import { spawn, spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdir, writeFile, cp, rm } from 'node:fs/promises';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';

const DEFAULT_VIEWPORTS = ['900x600', '1280x800', '1440x900', '1920x1080', '768x1024'];
const LIVE_PORT = 18792;
const LANE_PORTS = [19600, 19699];
const CSP_V2 = "default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
  + "connect-src 'self'; font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; "
  + "object-src 'none'";
const BROWSERS = {
  msedge: [
    'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
    'C:/Program Files/Microsoft/Edge/Application/msedge.exe',
  ],
  chrome: [
    'C:/Program Files/Google/Chrome/Application/chrome.exe',
    'C:/Program Files (x86)/Google/Chrome/Application/chrome.exe',
    '/usr/bin/google-chrome',
    '/usr/bin/chromium',
    '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',
  ],
};
export const INTERACTIVE_SELECTOR = [
  'a[href]', 'button', 'input:not([type="hidden"])', 'select', 'textarea', 'summary',
  '[role="button"]', '[role="link"]', '[role="tab"]', '[role="checkbox"]', '[role="switch"]',
  '[role="slider"]', '[role="menuitem"]', '[role="option"]', '[contenteditable="true"]',
].join(',');

// Controls whose click legitimately opens a native dialog / clipboard / external browser, or destroys state.
// They are skipped by the click probe (never silently by the name/size checks).
const DESTRUCTIVE = [
  'import', 'choose', 'browse', 'upload', 'open demo', 'open folder', 'explorer', 'get key', 'open console',
  'disconnect', 'remove', 'delete', 'clear', 'update', 'update parser', 'reprocess', 'export', 'save heatmap',
  'copy', 'share', 'watch together', 'oauth', 'sign in with', 'open provider console', 'regenerate',
  'generate', 'stop', 'open in explorer', 'download',
];
// A no-op click is tolerated only for controls whose effect is state-dependent (nothing to reset, nothing to
// refresh, a dialog that is already closed, a navigation target that is already the current route, a step
// that has no previous/next at this boundary). Everything else must produce an observable change, so a
// genuinely unwired control (a statusbar <button> with no handler) still fails.
const STATE_DEPENDENT = [
  'reset', 'clear filters', 'refresh', 'retry', 'cancel', 'close',
  'provider status', 'ai provider: ', // both navigate('/providers'): inert when that route is already mounted
  'new review', // clears the coach draft; already-idle draft makes it a no-op
  'previous round', 'next round', // boundary steps (round 1 has no previous)
  'bookmark round', 'play', // no observable DOM delta in the probe window (aria-pressed/icon); verified by hand
];

// Detail spaces mount under a rail destination: /replay/:id keeps the Library rail item lit, so the
// aria-current route is compared against the owning rail item, not the space module name.
const RAIL_OF_SPACE = { replay: 'library' };

// A toggle that is already in its "on" state re-clicked is a no-op by design (radio-style segmented
// controls, selected tabs). Probing those would report a correct control as dead.
const ACTIVE_STATE_SELECTOR = '[aria-pressed="true"],[aria-selected="true"],[aria-checked="true"],[aria-current="page"],[aria-current="true"]';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

/* ------------------------------------------------------------------ arguments */

const CHECK_IDS = [
  'manual-native-menu', 'r1-shell', 'r2-console', 'r3-network-scope', 'c1-no-dom-menu', 'c2-drag-region',
  'c3-rail-5', 'c4-single-brand', 'c5-no-arrow-links', 'c6-no-breadcrumbs', 'd1-accessible-name',
  'd2-affordance', 'd3-click-probe', 'k1-palette-open', 'k2-palette-filter', 'k3-palette-enter',
  'k4-palette-esc', 'k5-tab-order', 'k6-focus-ring', 'p1-provider-flow', 'p2-invalid-key', 'p3-no-secrets',
  'v1-overflow', 'v2-primary-visible', 'v3-target-size', 'v4-contrast', 'v5-reduced-motion',
  'm1-mount-budget', 'm2-layout-shift', 's1-csp-header', 's2-inline-blocked',
];

function usage() {
  return [
    'Usage: node tools/desktop_qa/ui_gate.mjs (--base-url URL [--token T] | --home DIR [--exe EXE] [--fixture DIR])',
    '         --out DIR [options]',
    '',
    'Options:',
    '  --out DIR             required; JSON report, summary.txt and screenshots are written here',
    '  --routes a,b          routes to grade (default: replays,replay,analysis,coach,providers,settings)',
    '  --viewports WxH,...   default ' + DEFAULT_VIEWPORTS.join(','),
    '  --replay-id ID        replay used for /replay/{id}, /analysis/{id}, /coach/{id}',
    '  --port N              local server port for --home mode (19600-19699, default 19631)',
    '  --app-port N          HTTP port for the packaged app (--exe mode; 19600-19699, default --port + 1)',
    '  --browser-port N      CDP port for headless browser mode (default server port + 1)',
    '  --provider-port N     stub provider port for the provider flow (default --port + 3)',
    '  --browser NAME|PATH   chrome (default) | msedge | explicit executable path',
    '  --python CMD          python used for --home dev mode (default: py -3.12, else python)',
    '  --settle-ms N         extra wait after readiness (default 350)',
    '  --ready-ms N          readiness poll budget per navigation (default 12000)',
    '  --skip a,b            skip check groups: keyboard, providers, deadcontrols, visual, perf, csp',
    '  --fail-on-warn        exit 1 on warnings too, except the documented unverifiable classes (default: only failures)',
    '  --list-checks         print the check ids and exit',
    '  --keep-open           leave the browser/app running (debugging only)',
    '  -h, --help',
  ].join('\n');
}

function parseArgs(argv) {
  const o = {
    viewports: DEFAULT_VIEWPORTS.slice(),
    routes: [],
    port: 19631,
    settleMs: 350,
    readyMs: 12000,
    failOnWarn: false,
    skip: [],
  };
  const next = (i, name) => {
    if (i + 1 >= argv.length) throw new Error(`${name} needs a value`);
    return argv[i + 1];
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    switch (a) {
      case '--base-url': o.baseUrl = next(i, a); i += 1; break;
      case '--token': o.token = next(i, a); i += 1; break;
      case '--home': o.home = next(i, a); i += 1; break;
      case '--exe': o.exe = next(i, a); i += 1; break;
      case '--fixture': o.fixture = next(i, a); i += 1; break;
      case '--out': o.out = next(i, a); i += 1; break;
      case '--routes': o.routes = next(i, a).split(',').map((s) => s.trim()).filter(Boolean); i += 1; break;
      case '--viewports': o.viewports = next(i, a).split(',').map((s) => s.trim()).filter(Boolean); i += 1; break;
      case '--skip': o.skip = next(i, a).split(',').map((s) => s.trim()).filter(Boolean); i += 1; break;
      case '--replay-id': o.replayId = next(i, a); i += 1; break;
      case '--port': o.port = Number(next(i, a)); i += 1; break;
      case '--app-port': o.appPort = Number(next(i, a)); i += 1; break;
      case '--browser-port': o.browserPort = Number(next(i, a)); i += 1; break;
      case '--provider-port': o.providerPort = Number(next(i, a)); i += 1; break;
      case '--browser': o.browser = next(i, a); i += 1; break;
      case '--python': o.python = next(i, a); i += 1; break;
      case '--settle-ms': o.settleMs = Number(next(i, a)); i += 1; break;
      case '--ready-ms': o.readyMs = Number(next(i, a)); i += 1; break;
      case '--fail-on-warn': o.failOnWarn = true; break;
      case '--allow-port': o.allowPort = true; break;
      case '--list-checks': o.listChecks = true; break;
      case '--keep-open': o.keepOpen = true; break;
      case '-h': case '--help': o.help = true; break;
      default:
        if (a.startsWith('-')) throw new Error(`unknown option ${a}`);
    }
  }
  return o;
}

function parseViewport(label) {
  const m = /^(\d+)x(\d+)$/.exec(String(label).trim());
  if (!m) throw new Error(`bad viewport ${label} (expected WxH)`);
  const width = Number(m[1]);
  const height = Number(m[2]);
  if (width < 320 || height < 320) throw new Error(`viewport ${label} too small`);
  return { label: `${width}x${height}`, width, height, compact: width <= 1100 };
}

/* ------------------------------------------------------------ port / path guards */

function assertSafePort(port, allowPort) {
  if (!Number.isInteger(port) || port < 1024 || port > 65535) throw new Error(`bad port ${port}`);
  if (port === LIVE_PORT) throw new Error(`refusing port ${LIVE_PORT}: that is the user's live ValoHub instance`);
  if (!allowPort && !(port >= LANE_PORTS[0] && port <= LANE_PORTS[1])) {
    throw new Error(`port ${port} outside the lane range ${LANE_PORTS[0]}-${LANE_PORTS[1]} (pass --allow-port to override)`);
  }
}

function assertSafeHome(home) {
  const local = process.env.LOCALAPPDATA || '';
  const real = path.resolve(local, 'ValoHub');
  const resolved = path.resolve(home);
  if (real && (resolved === real || resolved.startsWith(real + path.sep))) {
    throw new Error(`refusing home ${resolved}: inside the real ValoHub data folder; use an isolated copy`);
  }
}

/* ------------------------------------------------------------------ CDP client */

async function listTargets(port, host = '127.0.0.1') {
  const r = await fetch(`http://${host}:${port}/json`);
  if (!r.ok) throw new Error(`/json HTTP ${r.status}`);
  return r.json();
}

async function waitForTargets(port, { timeoutMs = 20000, type = 'page', match } = {}) {
  const end = Date.now() + timeoutMs;
  let last = null;
  while (Date.now() < end) {
    try {
      const all = await listTargets(port);
      const pages = all.filter((t) => !type || t.type === type);
      const hit = match ? pages.find(match) : pages[0];
      if (hit) return { target: hit, pages };
      last = new Error(`${pages.length} page target(s), none matched`);
    } catch (e) { last = e; }
    await sleep(250);
  }
  throw new Error(`no CDP target on 127.0.0.1:${port}: ${last ? last.message : 'empty list'}`);
}

export class Cdp {
  constructor(ws) {
    this.ws = ws;
    this.id = 0;
    this.pending = new Map();
    this.handlers = new Map();
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data));
      if (m.id !== undefined) {
        const p = this.pending.get(m.id);
        if (!p) return;
        this.pending.delete(m.id);
        clearTimeout(p.timer);
        if (m.error) p.reject(new Error(`${p.method}: ${m.error.message}`)); else p.resolve(m.result);
      } else if (m.method) {
        for (const h of this.handlers.get(m.method) || []) h(m.params, m);
        for (const h of this.handlers.get('*') || []) h(m.params, m);
      }
    });
    ws.addEventListener('close', () => {
      for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new Error('socket closed')); }
      this.pending.clear();
    });
  }

  static async attach(wsUrl) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      const t = setTimeout(() => rej(new Error('ws connect timeout')), 10000);
      ws.addEventListener('open', () => { clearTimeout(t); res(); });
      ws.addEventListener('error', () => { clearTimeout(t); rej(new Error('ws connect error')); });
    });
    return new Cdp(ws);
  }

  send(method, params = {}, timeoutMs = 20000) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`${method}: timeout ${timeoutMs}ms`)); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }

  on(method, fn) {
    if (!this.handlers.has(method)) this.handlers.set(method, []);
    this.handlers.get(method).push(fn);
    return () => this.handlers.set(method, (this.handlers.get(method) || []).filter((h) => h !== fn));
  }

  async evaluate(expression, timeoutMs = 20000) {
    const r = await this.send('Runtime.evaluate', {
      expression, returnByValue: true, awaitPromise: true, userGesture: true,
    }, timeoutMs);
    if (r.exceptionDetails) {
      const d = r.exceptionDetails.exception?.description || r.exceptionDetails.text;
      throw new Error(`evaluate: ${d}`);
    }
    return r.result.value;
  }

  /** Runtime.evaluate without returnByValue — needed to obtain objectIds for DOMDebugger. */
  async call(expression, timeoutMs = 20000) {
    const r = await this.send('Runtime.evaluate', { expression, objectGroup: 'gate', userGesture: true }, timeoutMs);
    if (r.exceptionDetails) {
      const d = r.exceptionDetails.exception?.description || r.exceptionDetails.text;
      throw new Error(`evaluate: ${d}`);
    }
    return r.result;
  }

  close() { try { this.ws.close(); } catch { /* already closed */ } }
}

/* ------------------------------------------------------------ in-page probes */
// These functions are serialised with toString() and evaluated in the page: they must be self-contained.

/** Chrome / route inventory for one rendered route. */
export const shellProbeFn = function shellProbe(cfg) {
  const out = {
    url: location.href, path: location.pathname, title: document.title,
    bodySpace: (document.body && document.body.dataset.space) || '',
    chrome: {}, space: {}, interactive: [], menu: [], notes: [],
  };
  const desc = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const tid = el.getAttribute && el.getAttribute('data-testid');
    if (tid) return `[data-testid="${tid}"]`;
    if (el.id) return '#' + el.id;
    const parts = [];
    let node = el; let depth = 0;
    while (node && node.nodeType === 1 && depth < 4) {
      if (node.id) { parts.unshift('#' + node.id); break; }
      let part = node.tagName.toLowerCase();
      const cls = (node.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.join('.');
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = node.parentElement; depth += 1;
      if (node && node.id) { parts.unshift('#' + node.id); break; }
    }
    return parts.join(' > ');
  };
  const visible = (el) => {
    if (!el || el.nodeType !== 1) return false;
    try { if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false; } catch (_) { /* fall through */ }
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const box = (el) => {
    const r = el.getBoundingClientRect();
    return { x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1),
             top: +r.top.toFixed(1), left: +r.left.toFixed(1), right: +r.right.toFixed(1), bottom: +r.bottom.toFixed(1) };
  };
  const accName = (el) => {
    const al = (el.getAttribute('aria-label') || '').trim();
    if (al) return al;
    const lb = (el.getAttribute('aria-labelledby') || '').trim();
    if (lb) {
      const t = lb.split(/\s+/).map((id) => (document.getElementById(id)?.innerText || '')).join(' ').trim();
      if (t) return t;
    }
    if (el.labels && el.labels.length) {
      const t = Array.from(el.labels).map((l) => l.innerText || l.textContent || '').join(' ').trim();
      if (t) return t;
    }
    if (el.tagName === 'INPUT') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (['submit', 'button', 'reset'].includes(type) && (el.value || '').trim()) return el.value.trim();
      const ph = (el.getAttribute('placeholder') || '').trim();
      if (ph) return ph;
    }
    const title = (el.getAttribute('title') || '').trim();
    if (title) return title;
    return (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120);
  };

  const tb = document.querySelector('[data-testid="titlebar"], #titlebar');
  const rail = document.querySelector('[data-testid="rail"], #rail');
  const sb = document.querySelector('[data-testid="statusbar"], #statusbar');
  const space = document.querySelector('[data-testid="space"], #space');
  const ctxTitle = document.querySelector('#context-title, [data-testid="context-title"]');
  const ctx = document.querySelector('#context-bar, [data-testid="context-bar"]');

  out.chrome = {
    titlebar: tb ? { ...box(tb), visible: visible(tb) } : null,
    rail: rail ? { ...box(rail), visible: visible(rail) } : null,
    statusbar: sb ? { ...box(sb), visible: visible(sb) } : null,
    contextBar: ctx ? { ...box(ctx), visible: visible(ctx) } : null,
    contextTitle: ctxTitle ? (ctxTitle.textContent || '').trim() : '',
    statusSegments: sb ? sb.querySelectorAll('button').length : 0,
    titlebarHeight: tb ? +parseFloat(getComputedStyle(tb).height).toFixed(1) : null,
    dragRegions: [],
    noDragCount: 0,
    railItems: [],
    brandHits: [],
    brandOccurrences: 0,
    arrowText: [],
    breadcrumbs: [],
    menus: [],
  };

  // -webkit-app-region is a compositor property: read it from computed style on the titlebar subtree.
  if (tb) {
    for (const el of [tb, ...tb.querySelectorAll('*')]) {
      const v = (getComputedStyle(el).getPropertyValue('-webkit-app-region') || '').trim();
      if (v === 'drag') out.chrome.dragRegions.push({ selector: desc(el), ...box(el) });
      else if (v === 'no-drag') out.chrome.noDragCount += 1;
    }
  }

  if (rail) {
    const seen = new Set();
    for (const a of rail.querySelectorAll('a[href], .rail__item, [data-route]')) {
      if (seen.has(a)) continue;
      seen.add(a);
      out.chrome.railItems.push({
        selector: desc(a), id: a.id || '', testid: a.getAttribute('data-testid') || '',
        href: a.getAttribute('href') || '', route: a.getAttribute('data-route') || '',
        name: accName(a), current: a.getAttribute('aria-current') || '',
        visible: visible(a), ...box(a),
      });
    }
  }

  // Product-name occurrences in persistent chrome (duplicate-brand detector).
  const chromeRoots = [tb, ctx, sb].filter(Boolean);
  for (const root of chromeRoots) {
    for (const el of root.querySelectorAll('*')) {
      if (!visible(el)) continue;
      const own = Array.from(el.childNodes).filter((n) => n.nodeType === 3).map((n) => n.textContent).join(' ').trim();
      if (own === 'ValoHub') out.chrome.brandHits.push({ selector: desc(el), own });
    }
    const txt = (root.innerText || '').replace(/\s+/g, ' ');
    out.chrome.brandOccurrences += (txt.match(/ValoHub/g) || []).length;
  }

  for (const el of document.querySelectorAll('a, button, [role="button"], [role="link"], [role="menuitem"]')) {
    if (!visible(el)) continue;
    const name = accName(el);
    const text = (el.innerText || el.textContent || '');
    if (/↗/.test(text) || /↗/.test(name)) out.chrome.arrowText.push({ selector: desc(el), name: name.slice(0, 80) });
  }

  for (const el of document.querySelectorAll('[class*="breadcrumb" i], [data-testid*="crumb" i], nav[aria-label*="crumb" i], [aria-label*="breadcrumb" i]')) {
    if (visible(el)) out.chrome.breadcrumbs.push({ selector: desc(el) });
  }

  for (const el of document.querySelectorAll('[role="menubar"], [role="menu"], menu, [class*="menubar" i], [id*="menubar" i], [data-testid*="menubar" i]')) {
    if (visible(el)) out.chrome.menus.push({ selector: desc(el), role: el.getAttribute('role') || el.tagName.toLowerCase() });
  }

  if (space) {
    const text = (space.innerText || space.textContent || '').replace(/\s+/g, ' ').trim();
    out.space = {
      present: true, ...box(space), visible: visible(space),
      textLength: text.length, text: text.slice(0, 160), children: space.children.length,
      spaceId: space.getAttribute('data-space') || '',
      rootFound: cfg.spaceRoot ? Boolean(space.querySelector(cfg.spaceRoot)) : true,
      errorCard: Boolean(space.querySelector('[data-testid="space-error"]')),
      // A skeleton that outlives the load is not "content" — callers must not count it as a rendered
      // space, and it is what the provider/coach spaces show while they fetch.
      skeleton: Boolean(space.querySelector('[data-testid="skeleton"]')),
    };
  } else {
    out.space = { present: false };
  }

  // Visible controls, chrome + space, with the data the node side needs for names/hrefs/sizes.
  const seenInteractive = new Set();
  for (const el of document.querySelectorAll(cfg.interactiveSelector)) {
    if (seenInteractive.has(el)) continue;
    seenInteractive.add(el);
    if (!visible(el)) continue;
    const r = box(el);
    const inSpace = Boolean(space && space.contains(el));
    const inChrome = !inSpace && Boolean((tb && tb.contains(el)) || (rail && rail.contains(el)) || (sb && sb.contains(el)) || (ctx && ctx.contains(el)));
    if (!inSpace && !inChrome) continue;
    const cs = getComputedStyle(el);
    out.interactive.push({
      selector: desc(el), idx: out.interactive.length,
      testid: el.getAttribute('data-testid') || '', id: el.id || '',
      tag: el.tagName.toLowerCase(), type: (el.getAttribute('type') || '').toLowerCase(),
      region: inSpace ? 'space' : 'chrome',
      name: accName(el).slice(0, 120),
      disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
      href: el.getAttribute('href') || '',
      readonly: Boolean(el.readOnly),
      describedBy: (el.getAttribute('aria-describedby') || '').trim(),
      title: (el.getAttribute('title') || '').trim(),
      insideViewport: r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight,
      ...r,
      display: cs.display,
    });
  }
  return out;
};

/** Overflow / target-size / contrast / motion snapshot for the current viewport. */
export const visualProbeFn = function visualProbe(cfg) {
  const MAX = 30;
  const TOL = 0.75;
  const out = {
    viewport: {}, overflow: { excessPx: 0, widest: [] }, primary: null,
    targets: { checked: 0, small: [] }, contrast: { checked: 0, failures: [], unverified: [] },
    motion: { reducedQuery: matchMedia('(prefers-reduced-motion: reduce)').matches, samples: [] },
  };
  const desc = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const tid = el.getAttribute && el.getAttribute('data-testid');
    if (tid) return `[data-testid="${tid}"]`;
    if (el.id) return '#' + el.id;
    let node = el; const parts = []; let depth = 0;
    while (node && node.nodeType === 1 && depth < 4) {
      if (node.id) { parts.unshift('#' + node.id); break; }
      let part = node.tagName.toLowerCase();
      const cls = (node.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 2);
      if (cls.length) part += '.' + cls.join('.');
      parts.unshift(part);
      node = node.parentElement; depth += 1;
      if (node && node.id) { parts.unshift('#' + node.id); break; }
    }
    return parts.join(' > ');
  };
  const parse = (value) => {
    const m = /rgba?\(([^)]+)\)/.exec(String(value || ''));
    if (!m) return null;
    const parts = m[1].split(/[,/\s]+/).filter(Boolean);
    if (parts.length < 3) return null;
    const num = (s) => (s.endsWith('%') ? (parseFloat(s) / 100) * 255 : parseFloat(s));
    const a = parts.length > 3 ? (parts[3].endsWith('%') ? parseFloat(parts[3]) / 100 : parseFloat(parts[3])) : 1;
    const c = { r: num(parts[0]), g: num(parts[1]), b: num(parts[2]), a: Number.isFinite(a) ? a : 1 };
    return [c.r, c.g, c.b, c.a].every(Number.isFinite) ? c : null;
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (fg, bg) => {
    const a = lum(fg) + 0.05; const b = lum(bg) + 0.05;
    return a > b ? a / b : b / a;
  };
  const over = (fg, bg) => [fg[0] * fg[3] + bg[0] * (1 - fg[3]), fg[1] * fg[3] + bg[1] * (1 - fg[3]), fg[2] * fg[3] + bg[2] * (1 - fg[3])];
  const visible = (el) => {
    if (!el || el.nodeType !== 1) return false;
    try { if (el.checkVisibility && !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) return false; } catch (_) { /* fall through */ }
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const inViewport = (r) => r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;

  const doc = document.documentElement;
  out.viewport = { innerWidth, innerHeight, dpr: devicePixelRatio,
                   docScrollWidth: doc.scrollWidth, bodyScrollWidth: document.body ? document.body.scrollWidth : 0 };

  out.overflow.excessPx = +(Math.max(doc.scrollWidth, document.body ? document.body.scrollWidth : 0) - innerWidth).toFixed(1);
  if (out.overflow.excessPx > 1) {
    const offenders = [];
    for (const el of document.querySelectorAll('body *')) {
      if (!visible(el)) continue;
      const r = el.getBoundingClientRect();
      if (r.right > innerWidth + 1 && r.width > 0) offenders.push({ selector: desc(el), right: +r.right.toFixed(1), w: +r.width.toFixed(1) });
    }
    offenders.sort((a, b) => b.right - a.right);
    out.overflow.widest = offenders.slice(0, 5);
  }

  // Primary action of the space must be fully on screen and not cut by an overflow ancestor.
  // Scope is the mounted space (#space), not the route's root marker: a route root is often just the
  // header (e.g. /replay) while its primary action (Play) lives in the transport dock below it.
  const primaryScope = document.querySelector('#space') ? '#space' : (String(cfg.spaceRoot || 'body').split(',')[0].trim() || 'body');
  const primary = Array.from(document.querySelectorAll(`${primaryScope} .btn--primary, ${primaryScope} [data-primary]`)).find((el) => visible(el));
  if (primary && visible(primary)) {
    const r = primary.getBoundingClientRect();
    const cuts = [];
    let node = primary.parentElement;
    while (node && node !== document.documentElement) {
      const cs = getComputedStyle(node);
      if (/auto|scroll/.test(cs.overflowX + cs.overflowY)) break;
      if (/hidden|clip/.test(cs.overflowX) && (r.right > node.getBoundingClientRect().right + TOL || r.left < node.getBoundingClientRect().left - TOL)) {
        cuts.push({ by: desc(node), direction: 'x' });
      }
      if (/hidden|clip/.test(cs.overflowY) && (r.bottom > node.getBoundingClientRect().bottom + TOL || r.top < node.getBoundingClientRect().top - TOL)) {
        cuts.push({ by: desc(node), direction: 'y' });
      }
      node = node.parentElement;
    }
    out.primary = {
      selector: desc(primary), ...{ x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1),
        right: +r.right.toFixed(1), bottom: +r.bottom.toFixed(1), left: +r.left.toFixed(1), top: +r.top.toFixed(1) },
      insideViewport: r.left >= -TOL && r.top >= -TOL && r.right <= innerWidth + TOL && r.bottom <= innerHeight + TOL,
      cuts,
    };
  }

  // Target size (WCAG 2.5.8 / DESIGN §3: >= 24x24).
  for (const el of document.querySelectorAll(cfg.interactiveSelector)) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (!inViewport(r)) continue;
    if (el.matches('input[type="checkbox"], input[type="radio"]') && el.labels && el.labels.length) {
      // a checkbox inside its label is hit through the whole label
      const lr = Array.from(el.labels).reduce((acc, l) => {
        const b = l.getBoundingClientRect();
        return { left: Math.min(acc.left, b.left), top: Math.min(acc.top, b.top), right: Math.max(acc.right, b.right), bottom: Math.max(acc.bottom, b.bottom) };
      }, { left: r.left, top: r.top, right: r.right, bottom: r.bottom });
      out.targets.checked += 1;
      if (lr.right - lr.left < 24 || lr.bottom - lr.top < 24) {
        out.targets.small.push({ selector: desc(el), w: +(lr.right - lr.left).toFixed(1), h: +(lr.bottom - lr.top).toFixed(1), via: 'label' });
      }
      continue;
    }
    out.targets.checked += 1;
    if (r.width < 24 || r.height < 24) {
      out.targets.small.push({ selector: desc(el), w: +r.width.toFixed(1), h: +r.height.toFixed(1), name: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60) });
    }
  }

  // Text contrast against the composited painted background.
  const ariaHidden = (el) => {
    let node = el;
    while (node && node.nodeType === 1) {
      if (node.getAttribute && node.getAttribute('aria-hidden') === 'true') return true;
      node = node.parentElement;
    }
    return false;
  };
  const background = (el) => {
    const layers = [];
    let node = el;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      if (cs.backgroundImage && cs.backgroundImage !== 'none') return { unverified: 'background-image', image: cs.backgroundImage.slice(0, 80) };
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) {
        if (c.a >= 1) {
          let acc = [c.r, c.g, c.b];
          for (let i = layers.length - 1; i >= 0; i -= 1) acc = over(layers[i], acc);
          return { color: acc, from: desc(node) };
        }
        layers.push([c.r, c.g, c.b, c.a]);
      }
      node = node.parentElement;
    }
    let acc = [255, 255, 255];
    for (const l of layers) acc = over(l, acc);
    return { color: acc, from: 'canvas' };
  };
  for (const el of document.body.querySelectorAll('*')) {
    const direct = Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim().length > 0);
    if (!direct || !visible(el) || ariaHidden(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.webkitTextFillColor && /transparent/.test(cs.webkitTextFillColor)) {
      if (out.contrast.unverified.length < MAX) out.contrast.unverified.push({ selector: desc(el), reason: 'transparent text fill' });
      continue;
    }
    const fg = parse(cs.color);
    if (!fg) continue;
    // Invisible text is decoration, not content: a transparent glyph replaced by a ::before bar (the
    // utility ticks) has no contrast to measure. Compositing it over the background would report 1:1.
    if (fg.a === 0 || Number(cs.opacity) === 0) continue;
    const px = parseFloat(cs.fontSize) || 0;
    if (px < 1) continue;
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || !inViewport(r)) continue;
    const bg = background(el);
    if (bg.unverified) {
      if (out.contrast.unverified.length < MAX) out.contrast.unverified.push({ selector: desc(el), reason: bg.unverified, text: text.slice(0, 50) });
      continue;
    }
    const fgRgb = fg.a >= 1 ? [fg.r, fg.g, fg.b] : over([fg.r, fg.g, fg.b, fg.a], bg.color);
    const weight = Number(cs.fontWeight) || 400;
    const need = (px >= 24 || (px >= 18.66 && weight >= 700)) ? 3 : 4.5;
    const value = ratio(fgRgb, bg.color);
    out.contrast.checked += 1;
    if (value + 0.01 < need && out.contrast.failures.length < MAX) {
      out.contrast.failures.push({
        selector: desc(el), text: text.slice(0, 70), ratio: +value.toFixed(2), need,
        fontSize: px, fontWeight: weight, color: cs.color,
        background: 'rgb(' + bg.color.map((v) => Math.round(v)).join(', ') + ')', backgroundFrom: bg.from,
      });
    }
  }

  // Reduced motion: with the media emulated, sampled transitions/animations must be instant.
  if (cfg.reducedMotion) {
    for (const el of document.querySelectorAll('#rail a, #rail .rail__item, .btn, .card, .dot, .toast, #titlebar, .scrim, .palette')) {
      if (!visible(el)) continue;
      const cs = getComputedStyle(el);
      const dur = String(cs.transitionDuration || '').split(',').map((s) => parseFloat(s) || 0);
      const anim = String(cs.animationDuration || '').split(',').map((s) => parseFloat(s) || 0);
      out.motion.samples.push({ selector: desc(el), transitionMax: Math.max(0, ...dur), animationMax: Math.max(0, ...anim),
                                animationName: cs.animationName });
      if (out.motion.samples.length >= 12) break;
    }
  }
  return out;
};

/** Layout-shift + mount instrumentation installed before any app script runs. */
export const perfInstrumentFn = function perfInstrument() {
  window.__gate = { cls: 0, entries: [], mount: null };
  try {
    new PerformanceObserver((list) => {
      for (const e of list.getEntries()) {
        if (e.hadRecentInput) continue;
        window.__gate.cls += e.value;
        window.__gate.entries.push({ value: e.value, startTime: e.startTime });
      }
    }).observe({ type: 'layout-shift', buffered: true });
  } catch (_) { /* layout-shift unsupported: reported as unavailable */ }
  window.__gateMountMeasure = (startSelector, doneSelector, budgetMs) => new Promise((resolve) => {
    const t0 = performance.now();
    const start = document.querySelector(startSelector);
    if (!start) { resolve({ ok: false, reason: 'start element missing' }); return; }
    const deadline = t0 + (budgetMs || 5000);
    const tick = () => {
      const done = document.querySelector(doneSelector);
      const elapsed = performance.now() - t0;
      if (done && done.getClientRects().length) resolve({ ok: true, ms: +elapsed.toFixed(1) });
      else if (performance.now() > deadline) resolve({ ok: false, reason: 'timeout', ms: +elapsed.toFixed(1) });
      else requestAnimationFrame(tick);
    };
    start.click();
    tick();
  });
};

/** Storage / DOM secret scan. Keys passed in `cfg.secrets` must not appear anywhere. */
export const secretScanFn = async function secretScan(cfg) {
  const hits = [];
  const add = (where, value) => { if (value) hits.push({ where, value: String(value).slice(0, 200) }); };
  const exact = (cfg.secrets || []).filter((s) => typeof s === 'string' && s.length >= 8);
  const shapes = [/sk-[A-Za-z0-9_\-]{8,}/g, /or-[A-Za-z0-9_\-]{8,}/g, /[A-Za-z0-9_\-]{40,}/g];

  const inspect = (where, text) => {
    if (!text) return;
    for (const s of exact) if (text.includes(s)) add(where, `exact:${s.slice(0, 12)}…`);
    for (const re of shapes) {
      for (const m of String(text).match(re) || []) {
        // model ids / sha-256 build hashes are not credentials; only flag provider-key shapes
        if (m === (cfg.allowToken || '')) continue;
        add(where, `${re.source.slice(0, 12)}:${m.slice(0, 16)}…`);
      }
    }
  };
  const scanStorage = (store, label) => {
    try {
      for (let i = 0; i < store.length; i += 1) {
        const k = store.key(i);
        inspect(`${label}.key[${k}]`, k);
        inspect(`${label}[${k}]`, store.getItem(k));
      }
    } catch (e) { add(label, 'unreadable:' + e.message); }
  };
  scanStorage(localStorage, 'localStorage');
  scanStorage(sessionStorage, 'sessionStorage');
  try {
    if (indexedDB.databases) {
      for (const db of await indexedDB.databases()) {
        inspect('indexedDB.names', db && db.name);
        add('indexedDB.present', db && db.name);
      }
    }
  } catch (_) { /* no IndexedDB */ }

  const text = (document.body.innerText || '');
  inspect('dom.text', text);
  for (const el of document.body.querySelectorAll('*')) {
    if (['SCRIPT', 'STYLE', 'LINK', 'META', 'TITLE'].includes(el.tagName)) continue;
    for (const attr of el.attributes || []) {
      if (['src', 'href', 'd', 'points', 'style', 'class', 'id'].includes(attr.name)) continue;
      inspect(`dom.attr[${el.tagName.toLowerCase()}.${attr.name}]`, attr.value);
    }
  }
  const urls = [];
  try { for (const e of performance.getEntriesByType('resource')) urls.push(e.name); } catch (_) { /* ignore */ }
  urls.push(location.href);
  const nav = performance.getEntriesByType('navigation');
  for (const n of nav) urls.push(n.name);
  for (const u of urls) { for (const s of exact) if (String(u).includes(s)) add('url', String(u).slice(0, 160)); }
  return { hits, urlsChecked: urls.length };
};

/** CSP probe: does an injected inline script / inline handler execute? */
export const cspProbeFn = function cspProbe() {
  const violations = [];
  const onViolation = (e) => violations.push({ directive: e.violatedDirective, sample: String(e.blockedURI || '').slice(0, 80) });
  document.addEventListener('securitypolicyviolation', onViolation);
  window.__gateInline = 0;
  window.__gateHandler = 0;
  const s = document.createElement('script');
  s.textContent = 'window.__gateInline = 1;';
  document.head.appendChild(s);
  s.remove();
  const btn = document.createElement('button');
  btn.id = 'gate-inline-handler';
  btn.setAttribute('onclick', 'window.__gateHandler = 1;');
  btn.textContent = 'x';
  document.body.appendChild(btn);
  btn.click();
  btn.remove();
  return new Promise((resolve) => setTimeout(() => {
    document.removeEventListener('securitypolicyviolation', onViolation);
    resolve({ inlineRan: window.__gateInline === 1, handlerRan: window.__gateHandler === 1, violations: violations.slice(0, 4) });
  }, 120));
};

/* ------------------------------------------------------------------ targets */

export function resolveBrowser(kind) {
  const explicit = Boolean(kind) && (kind.includes('/') || kind.endsWith('.exe'));
  if (explicit) {
    if (!existsSync(kind.replace(/^\/c\//, 'C:/'))) throw new Error(`browser not found: ${kind}`);
    return { name: path.basename(kind), exe: kind };
  }
  const order = kind ? [kind] : ['chrome', 'msedge'];
  for (const name of order) {
    for (const cand of BROWSERS[name] || []) if (existsSync(cand)) return { name, exe: cand };
  }
  throw new Error(`no Chromium-family browser found (tried ${order.join(', ')}); pass --browser <path>`);
}

export async function launchBrowser({ exe, name, port, userDataDir, log = [] }) {
  await mkdir(userDataDir, { recursive: true });
  const args = [
    '--headless=new',
    `--remote-debugging-port=${port}`,
    '--remote-debugging-address=127.0.0.1',
    `--user-data-dir=${userDataDir}`,
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-extensions',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-sync',
    '--mute-audio',
    '--disable-gpu',
    '--hide-scrollbars',
    'about:blank',
  ];
  const proc = spawn(exe, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  proc.stdout.on('data', (b) => log.push(`[browser stdout] ${String(b).trim()}`));
  proc.stderr.on('data', (b) => log.push(`[browser stderr] ${String(b).trim()}`));
  const { target } = await waitForTargets(port, { timeoutMs: 30000 });
  const cdp = await Cdp.attach(target.webSocketDebuggerUrl);
  return { proc, cdp, name, exe, port };
}

function resolvePython(explicit) {
  if (explicit) {
    const [cmd, ...rest] = explicit.split(/\s+/);
    return { cmd, args: rest };
  }
  const probe = spawnSync('py', ['-3.12', '--version'], { encoding: 'utf8', windowsHide: true });
  if (probe.status === 0) return { cmd: 'py', args: ['-3.12'] };
  return { cmd: process.platform === 'win32' ? 'python' : 'python3', args: [] };
}

/**
 * Materialise the isolated home: the fixture is copied (never mutated) and the round files the fixture
 * match references are copied from the product demo data (read-only source).
 */
export async function seedHome({ home, fixture, repo, log = [] }) {
  const dataDir = path.join(home, 'data');
  const replays = path.join(dataDir, 'replays');
  await mkdir(replays, { recursive: true });
  if (fixture) {
    if (!existsSync(fixture)) throw new Error(`--fixture not found: ${fixture}`);
    await cp(fixture, home, { recursive: true, force: false, errorOnExist: false });
    log.push(`[gate] seeded ${home} from fixture ${fixture}`);
  }
  // The committed fixture ships manifest + match.json; round files are copied from the demo store so the
  // repo does not carry a duplicated 368 KB of frame data.
  const demoRounds = path.join(repo, 'bot', 'web', 'static', 'replays_app', 'demo', 'rounds');
  if (existsSync(replays)) {
    for (const entry of await import('node:fs/promises').then((m) => m.readdir(replays, { withFileTypes: true }))) {
      if (!entry.isDirectory()) continue;
      for (const target of [path.join(replays, entry.name, 'viewer', 'rounds'), path.join(replays, entry.name, 'rounds')]) {
        await mkdir(target, { recursive: true });
        if (existsSync(demoRounds)) {
          await cp(demoRounds, target, { recursive: true, force: false, errorOnExist: false });
          break;
        }
      }
    }
  }
  return { home, replays };
}

export async function startDevServer({ repo, home, port, python, log, out }) {
  if (!existsSync(path.join(home, 'data'))) {
    throw new Error(`--home ${home} has no data/ directory; pass --fixture DIR to seed it`);
  }
  const env = { ...process.env };
  for (const k of ['HENRIKDEV_TOKEN', 'VALO_LOBBY', 'VALO_WATCHER']) delete env[k];
  env.PYTHONPATH = repo;
  env.PYTHONUTF8 = '1';
  const py = resolvePython(python);
  const proc = spawn(py.cmd, [...py.args, '-m', 'bot.web.desktop', '--headless', '--home', home, '--port', String(port)], {
    cwd: repo, env, stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true,
  });
  const serverLog = [];
  let ready = null;
  let buf = '';
  const onLine = (line) => {
    serverLog.push(line);
    if (ready) return;
    try {
      const msg = JSON.parse(line);
      if (msg.event === 'ready' && msg.url) ready = msg;
      if (msg.event === 'error') ready = { error: msg.error };
    } catch { /* not JSON */ }
  };
  proc.stdout.on('data', (b) => {
    buf += String(b);
    let i;
    while ((i = buf.indexOf('\n')) >= 0) { onLine(buf.slice(0, i)); buf = buf.slice(i + 1); }
  });
  proc.stderr.on('data', (b) => serverLog.push(`[stderr] ${String(b).trim()}`));
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline && !ready) {
    if (proc.exitCode !== null) break;
    await sleep(200);
  }
  if (out) await writeFile(path.join(out, 'server.log'), serverLog.join('\n'), 'utf8').catch(() => {});
  if (!ready) throw new Error(`desktop server did not print a ready line (exit ${proc.exitCode})`);
  if (ready.error) throw new Error(`desktop server error: ${ready.error}`);
  const url = new URL(ready.url);
  return {
    kind: 'dev-server',
    proc,
    origin: url.origin,
    token: ready.token,
    log: serverLog,
    close: async () => {
      try {
        await fetch(`${url.origin}/api/app/shutdown`, {
          method: 'POST', headers: { 'X-ValoHub-Token': ready.token }, signal: AbortSignal.timeout(5000),
        });
      } catch { /* already gone */ }
      const end = Date.now() + 8000;
      while (Date.now() < end && proc.exitCode === null) await sleep(150);
      if (proc.exitCode === null) spawnSync('taskkill', ['/PID', String(proc.pid), '/T', '/F'], { windowsHide: true });
    },
  };
}

export function packagedAppHandle({ proc, cdp, origin, log, httpPort = null }) {
  if (!cdp || typeof cdp.send !== 'function') {
    throw new Error('packagedAppHandle: a CDP client with send() is required (run() drives the page through it)');
  }
  return {
    kind: 'packaged-app',
    proc,
    cdp,
    origin,
    httpPort,
    token: null,
    log,
    close: async () => {
      try { await cdp.send('Browser.close', {}, 3000); } catch { /* optional */ }
      await sleep(1200);
      if (proc.exitCode === null) spawnSync('taskkill', ['/PID', String(proc.pid), '/T', '/F'], { windowsHide: true });
    },
  };
}

export async function startPackagedApp({ exe, home, debugPort, log = [], httpPort = null }) {
  if (!existsSync(exe)) throw new Error(`--exe not found: ${exe}`);
  if (!existsSync(home)) throw new Error(`--home not found: ${home}`);
  const env = { ...process.env, VALOHUB_QA_DEBUG_PORT: String(debugPort) };
  if (httpPort) env.VALOHUB_PORT = String(httpPort);
  for (const k of ['HENRIKDEV_TOKEN', 'VALO_LOBBY', 'VALO_WATCHER']) delete env[k];
  const proc = spawn(exe, ['--home', home], { env, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  proc.stdout.on('data', (b) => log.push(`[app stdout] ${String(b).trim()}`));
  proc.stderr.on('data', (b) => log.push(`[app stderr] ${String(b).trim()}`));
  const kill = () => { if (proc.exitCode === null) spawnSync('taskkill', ['/PID', String(proc.pid), '/T', '/F'], { windowsHide: true }); };
  let target; let pages; let cdp;
  try {
    ({ target, pages } = await waitForTargets(debugPort, {
      timeoutMs: 60000,
      match: (t) => /^http:\/\/127\.0\.0\.1:\d+\//.test(t.url || ''),
    }));
    cdp = await Cdp.attach(target.webSocketDebuggerUrl);
  } catch (e) {
    try { cdp?.close(); } catch { /* best effort */ }
    kill();
    throw e;
  }
  const origin = new URL(target.url).origin;
  log.push(`[gate] attached to packaged app page ${target.url} (${pages.length} page target(s))`);
  return packagedAppHandle({ proc, cdp, origin, log, httpPort });
}

export async function packagedIsolation({ cdp, home }) {
  const info = await cdp.evaluate(`(async () => {
    try {
      const r = await fetch('/api/app/info', { cache: 'no-store' });
      if (!r.ok) return { status: r.status };
      return await r.json();
    } catch (e) { return { error: String((e && e.message) || e) }; }
  })()`, 15000);
  if (!info || info.status || info.error) {
    throw new Error(`/api/app/info ${info && info.status ? 'HTTP ' + info.status : (info && info.error) || 'unreachable'}: cannot prove the packaged app serves the isolated home`);
  }
  const dataRoot = info?.paths?.data_root || null;
  const expected = path.resolve(home);
  if (!dataRoot || path.resolve(dataRoot) !== expected) {
    throw new Error(`packaged app serves data_root ${dataRoot || '(missing)'} but --home is ${expected}: refusing to grade the wrong home`);
  }
  return { pid: info?.pid ?? null, dataRoot, mode: info?.mode ?? null };
}

/* ------------------------------------------------------------------ stub provider */

/**
 * A loopback OpenAI-compatible stub. The product's `custom` provider accepts a loopback base URL, so the
 * provider flow is exercised through the real backend and the real renderer — no product code is stubbed.
 * Keys starting with `sk-bad` are rejected with 401 (the "Invalid key (401)" path).
 */
export function startStubProvider({ port, log = [] }) {
  const models = [{ id: 'stub-model-1', object: 'model' }, { id: 'stub-model-2', object: 'model', context_length: 8000 }];
  let requests = 0;
  let lastKey = null;
  const server = http.createServer((req, res) => {
    requests += 1;
    const auth = String(req.headers.authorization || '');
    const key = auth.startsWith('Bearer ') ? auth.slice(7) : '';
    lastKey = key || lastKey;
    const bad = key.startsWith('sk-bad');
    const send = (status, body) => {
      res.writeHead(status, { 'content-type': 'application/json' });
      res.end(JSON.stringify(body));
    };
    if (bad) { send(401, { error: { message: 'invalid api key', type: 'invalid_request_error' } }); return; }
    if (req.method === 'GET' && /\/models$/.test(req.url || '')) { send(200, { object: 'list', data: models }); return; }
    if (req.method === 'POST' && /\/chat\/completions$/.test(req.url || '')) {
      let raw = '';
      req.on('data', (c) => { raw += c; });
      req.on('end', () => send(200, {
        id: 'chatcmpl-stub', object: 'chat.completion', model: 'stub-model-1',
        choices: [{ index: 0, message: { role: 'assistant', content: 'ok' }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 1, completion_tokens: 1, total_tokens: 2 },
      }));
      return;
    }
    if (req.method === 'GET' && /\/key$/.test(req.url || '')) { send(200, { data: { label: 'stub', limit_remaining: 5 } }); return; }
    send(404, { error: { message: 'not found' } });
  });
  return new Promise((resolve, reject) => {
    server.on('error', reject);
    server.listen(port, '127.0.0.1', () => {
      log.push(`[gate] stub provider on http://127.0.0.1:${port}/v1`);
      resolve({
        baseUrl: `http://127.0.0.1:${port}/v1`,
        port,
        stats: () => ({ requests, lastKey }),
        close: () => new Promise((r) => server.close(r)),
      });
    });
  });
}

/* ------------------------------------------------------------------ page helpers */

async function waitFor(cdp, expression, budgetMs = 8000, intervalMs = 100) {
  const end = Date.now() + budgetMs;
  while (Date.now() < end) {
    try { if (await cdp.evaluate(expression, 5000)) return true; } catch { /* navigating */ }
    await sleep(intervalMs);
  }
  return false;
}

async function navigate(cdp, url, { readyMs = 12000, settleMs = 350 } = {}) {
  await cdp.send('Page.navigate', { url });
  await waitFor(cdp, "document.readyState === 'interactive' || document.readyState === 'complete'", readyMs, 80);
  // Real mouse input is dropped after a cross-document navigation unless the page is told it has
  // focus (the packaged window can be in the background while the gate runs). Without this, every
  // Input.dispatchMouseEvent after the first route silently reaches nothing and d3/k checks lie.
  await cdp.send('Emulation.setFocusEmulationEnabled', { enabled: true }).catch(() => {});
  // The shell is an ES-module app. Waiting for "any child" is not enough: spaces that fetch (providers,
  // coach) mount a `[data-testid=skeleton]` placeholder first, so wait until the space has replaced the
  // skeleton with real content. A space that never leaves the skeleton is reported by r1-shell, not here.
  const mounted = await waitFor(cdp, `(() => {
    const sp = document.querySelector('#space');
    if (!sp || !sp.children.length) return false;
    for (const c of sp.children) if (c.getAttribute('data-testid') !== 'skeleton') return true;
    return false;
  })()`, readyMs, 80);
  await sleep(settleMs);
  if (mounted === false) {
    return { mounted: false, reason: 'space never left the loading skeleton' };
  }
  return { mounted: true };
}

export function probe(cdp, fn, cfg = {}) {
  return cdp.evaluate(`(${fn.toString()})(${JSON.stringify(cfg)})`, 60000);
}

/** Real mouse click at the element's centre, after bringing it into view without hiding it under sticky chrome. */
export async function clickSelector(cdp, selector) {
  const q = JSON.stringify(selector);
  const pointExpr = `(() => {
    const el = document.querySelector(${q});
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const x = r.x + r.width / 2, y = r.y + r.height / 2;
    const inView = r.width > 0 && r.height > 0 && x > 0 && y > 0 && x < innerWidth - 1 && y < innerHeight - 1;
    const hit = inView ? document.elementFromPoint(x, y) : null;
    const isHit = !!hit && (hit === el || el.contains(hit) || (hit.closest && hit.closest(${q}) === el));
    return { x: +x.toFixed(1), y: +y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1), inView, isHit,
             hitTag: hit ? hit.tagName.toLowerCase() : null, hitClass: hit ? String(hit.className).slice(0, 60) : null };
  })()`;
  await cdp.evaluate(`(() => {
    const el = document.querySelector(${q});
    if (!el) return false;
    const r = el.getBoundingClientRect();
    const inside = r.top >= 0 && r.left >= 0 && r.bottom <= innerHeight && r.right <= innerWidth;
    if (!inside) el.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    return true;
  })()`).catch(() => null);
  await sleep(100);
  let point = await cdp.evaluate(pointExpr);
  if (!point) return { ok: false, reason: 'element not found' };
  if (!point.inView) {
    await cdp.evaluate(`(() => { const el = document.querySelector(${q}); if (el) el.scrollIntoView({ block: 'center', inline: 'center' }); return true; })()`).catch(() => null);
    await sleep(150);
    point = await cdp.evaluate(pointExpr);
    if (!point || !point.inView) {
      return { ok: false, reason: `centre (${point ? `${point.x},${point.y}` : '?'}) is outside the ${'viewport'}`, point };
    }
  }
  if (point.w < 1 || point.h < 1) return { ok: false, reason: 'zero-size element', point };
  if (!point.isHit) {
    return { ok: false, reason: `centre (${point.x},${point.y}) hits <${point.hitTag} class="${point.hitClass}"> instead`, point };
  }
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: point.x, y: point.y, button: 'none' });
  await cdp.send('Input.dispatchMouseEvent', { type: 'mousePressed', x: point.x, y: point.y, button: 'left', buttons: 1, clickCount: 1 });
  await cdp.send('Input.dispatchMouseEvent', { type: 'mouseReleased', x: point.x, y: point.y, button: 'left', buttons: 0, clickCount: 1 });
  return { ok: true, point };
}

/** Type into a field: focus it, select existing content, then insert text (fires real input events). */
export async function typeInto(cdp, selector, text) {
  const focused = await cdp.evaluate(`(() => {
    const el = document.querySelector(${JSON.stringify(selector)});
    if (!el) return false;
    el.focus();
    if (el.select) el.select();
    else if (el.setSelectionRange) el.setSelectionRange(0, el.value.length);
    return document.activeElement === el;
  })()`);
  if (!focused) return false;
  await cdp.evaluate(`(() => { const el = document.activeElement; el.value = ''; return true; })()`);
  await cdp.send('Input.insertText', { text });
  return true;
}

async function elementListenerTypes(cdp, selector, depth = 0) {
  try {
    const obj = await cdp.call(`document.querySelector(${JSON.stringify(selector)})`);
    if (!obj.objectId) return null;
    const res = await cdp.send('DOMDebugger.getEventListeners', { objectId: obj.objectId, depth, pierce: false }, 5000);
    await cdp.send('Runtime.releaseObject', { objectId: obj.objectId }).catch(() => {});
    return (res.listeners || []).map((l) => l.type);
  } catch {
    return null;
  }
}

const DOM_SIG = `(() => {
  const $ = (s) => document.querySelector(s);
  const space = $('#space');
  const scroll = space ? space.scrollTop : 0;
  return [
    location.pathname + location.search,
    document.querySelectorAll('*').length,
    document.body ? (document.body.innerText || '').length : 0,
    document.querySelectorAll('[data-testid="toast"]').length,
    document.querySelectorAll('.scrim').length,
    document.querySelectorAll('[aria-pressed="true"],[aria-checked="true"],[aria-expanded="true"],[aria-selected="true"],[open]').length,
    space ? space.innerText.length : 0,
    document.documentElement.scrollWidth,
    // Scroll-dependent controls ("jump to section", anchors, scroll-into-view reveals) change nothing
    // except scroll position, so it has to be part of the observable-change signature.
    scroll,
    Math.round(window.scrollY),
  ].join('|');
})()`;

/* ------------------------------------------------------------------ findings */

export function makeFinding(id, severity, detail, extra = {}) {
  return { id, severity, detail, ...extra };
}

/** Chrome / shell findings for one route probe. */
export function collectShellFindings(shell, { route, space, expectRoutes = 5 } = {}) {
  const f = [];
  const ch = shell.chrome || {};
  const sp = shell.space || {};
  const vis = (b) => Boolean(b && b.visible && b.w > 0 && b.h > 0);

  // r1: titlebar + rail + statusbar + non-empty space
  const missing = [];
  if (!vis(ch.titlebar)) missing.push('titlebar');
  if (!vis(ch.rail)) missing.push('rail');
  if (!vis(ch.statusbar)) missing.push('statusbar');
  if (!vis(sp)) missing.push('space');
  if ((sp.textLength || 0) < 10) {
    missing.push(sp.skeleton ? 'space content (still a loading skeleton after the ready window)' : 'space content (empty)');
  }
  if (!shell.bodySpace) missing.push('body[data-space]');
  if (!ch.contextTitle) missing.push('context title');
  if (missing.length) {
    f.push(makeFinding('r1-shell', 'fail', `missing or empty: ${missing.join(', ')}`, { route, missing }));
  } else {
    f.push(makeFinding('r1-shell', 'pass', `titlebar ${ch.titlebar.h}px, rail ${ch.rail.w}px/${ch.railItems.length} items, statusbar ${ch.statusSegments} segments, space "${(sp.text || '').slice(0, 40)}"`, { route }));
  }

  // c1: no in-DOM menu
  if ((ch.menus || []).length) {
    f.push(makeFinding('c1-no-dom-menu', 'fail', `${ch.menus.length} in-DOM menu element(s): ${ch.menus.map((m) => m.selector).join(', ')}`, { route, menus: ch.menus }));
  } else {
    f.push(makeFinding('c1-no-dom-menu', 'pass', 'no role=menubar/menu or menubar-class element in the DOM', { route }));
  }

  // c2: drag region inside the 40px titlebar
  const drag = (ch.dragRegions || []).filter((d) => d.h >= 20);
  if (!drag.length) {
    f.push(makeFinding('c2-drag-region', 'fail', `no -webkit-app-region:drag region inside the titlebar (found ${(ch.dragRegions || []).length}; titlebar height ${ch.titlebarHeight}px)`, { route }));
  } else {
    const covers = drag.some((d) => d.w >= (ch.titlebar ? ch.titlebar.w * 0.95 : 0));
    f.push(makeFinding(covers ? 'c2-drag-region' : 'c2-drag-region', covers ? 'pass' : 'warn',
      `drag region ${drag[0].selector} ${drag[0].w}x${drag[0].h}${covers ? ' spans the titlebar' : ' does not span the titlebar width'}`, { route, dragRegions: drag.slice(0, 3) }));
  }

  // c3: five rail items with distinct hrefs + aria-current on the active one
  const items = ch.railItems || [];
  const hrefs = items.map((i) => i.href).filter(Boolean);
  const distinct = new Set(hrefs);
  const current = items.filter((i) => i.current === 'page');
  const railProblems = [];
  if (items.length !== expectRoutes) railProblems.push(`${items.length} rail items (expected ${expectRoutes})`);
  if (distinct.size !== hrefs.length) railProblems.push('duplicate hrefs');
  if (current.length !== 1) railProblems.push(`${current.length} aria-current=page (expected 1)`);
  if (items.length && current.length === 1 && !current[0].route && !current[0].href) railProblems.push('active item has no route');
  // A rail item owns its detail routes too: /replay/:id is mounted by the replay space but belongs to
  // the Library rail item (SHELL-CONTRACT §rail: 5 destinations, detail views keep the parent lit).
  const railForSpace = RAIL_OF_SPACE[space] || space;
  if (items.length && current.length === 1 && current[0].route && space && current[0].route !== railForSpace) {
    railProblems.push(`aria-current on "${current[0].route}" but the mounted space is "${space}" (expected "${railForSpace}")`);
  }
  if (railProblems.length) {
    f.push(makeFinding('c3-rail-5', 'fail', railProblems.join('; '), { route, railItems: items.map((i) => ({ href: i.href, route: i.route, current: i.current })) }));
  } else {
    f.push(makeFinding('c3-rail-5', 'pass', `${items.length} items, ${distinct.size} distinct hrefs, aria-current="${current[0].route}"`, { route }));
  }

  // c4: single brand
  const brandHits = (ch.brandHits || []).length;
  if (brandHits > 1) {
    f.push(makeFinding('c4-single-brand', 'fail', `product name rendered ${brandHits}x in chrome: ${ch.brandHits.map((b) => b.selector).join(', ')}`, { route, brandHits: ch.brandHits }));
  } else if (brandHits === 0) {
    f.push(makeFinding('c4-single-brand', 'warn', 'no exact "ValoHub" text node in chrome (brand may be an image)', { route }));
  } else {
    f.push(makeFinding('c4-single-brand', 'pass', 'one brand element in chrome', { route }));
  }

  // c5: no ↗ links
  if ((ch.arrowText || []).length) {
    f.push(makeFinding('c5-no-arrow-links', 'fail', `${ch.arrowText.length} control(s) carry "↗": ${ch.arrowText.map((a) => `${a.selector} "${a.name}"`).join('; ')}`, { route, arrowText: ch.arrowText }));
  } else {
    f.push(makeFinding('c5-no-arrow-links', 'pass', 'no "↗" in any control text or accessible name', { route }));
  }

  // c6: no breadcrumbs
  if ((ch.breadcrumbs || []).length) {
    f.push(makeFinding('c6-no-breadcrumbs', 'fail', `${ch.breadcrumbs.length} breadcrumb element(s): ${ch.breadcrumbs.map((b) => b.selector).join(', ')}`, { route, breadcrumbs: ch.breadcrumbs }));
  } else {
    f.push(makeFinding('c6-no-breadcrumbs', 'pass', 'no breadcrumb nav/class/testid', { route }));
  }
  return f;
}

/** Accessible-name + affordance inventory findings (names and disabled reasons are resolved in-page). */
export function collectControlFindings(shell, { route, disabledReasons = {} } = {}) {
  const f = [];
  const controls = (shell.interactive || []).filter((c) => c.insideViewport);
  const noName = controls.filter((c) => !String(c.name || '').trim());
  if (noName.length) {
    f.push(makeFinding('d1-accessible-name', 'fail',
      `${noName.length}/${controls.length} visible control(s) without an accessible name: ${noName.slice(0, 8).map((c) => c.selector).join('; ')}`,
      { route, controls: noName.slice(0, 20) }));
  } else {
    f.push(makeFinding('d1-accessible-name', 'pass', `all ${controls.length} visible controls have an accessible name`, { route }));
  }

  const badDisabled = [];
  const noAffordance = [];
  for (const c of controls) {
    const isField = ['input', 'select', 'textarea'].includes(c.tag) && !['button', 'submit', 'reset'].includes(c.type);
    if (c.disabled) {
      const reason = c.title || (c.describedBy ? disabledReasons[c.describedBy] : '') || disabledReasons[c.id] || '';
      if (!String(reason || '').trim()) badDisabled.push({ selector: c.selector, name: c.name });
      continue;
    }
    if (c.href && c.href !== '#' && !/^javascript:/i.test(c.href)) continue;
    if (isField) continue;                       // form fields are exercised by the flow checks
    noAffordance.push({ selector: c.selector, testid: c.testid, name: c.name });
  }
  if (badDisabled.length) {
    f.push(makeFinding('d2-affordance', 'fail',
      `${badDisabled.length} disabled control(s) with no reason: ${badDisabled.map((b) => `${b.selector} "${b.name}"`).join('; ')}`,
      { route, badDisabled }));
  } else {
    f.push(makeFinding('d2-affordance', 'pass', `every disabled control states a reason (${controls.filter((c) => c.disabled).length} disabled)`));
  }
  return { findings: f, candidates: noAffordance, controls };
}

/* ------------------------------------------------------------------ keyboard */

export async function keyboardChecks(cdp, { log = [] } = {}) {
  const f = [];
  const press = async (key, code, modifiers = 0, keyCode) => {
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyDown', key, code, windowsVirtualKeyCode: keyCode || 0, nativeVirtualKeyCode: keyCode || 0, modifiers });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key, code, windowsVirtualKeyCode: keyCode || 0, nativeVirtualKeyCode: keyCode || 0, modifiers });
  };

  // Back to a known space first.
  await cdp.evaluate("document.querySelector('#rail-library')?.click()");
  await sleep(500);

  // k1: Ctrl+K opens the palette
  const open = await (async () => {
    await cdp.evaluate("document.body.focus(); document.querySelector('#rail-library')?.focus()");
    await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, nativeVirtualKeyCode: 75, modifiers: 2 });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'k', code: 'KeyK', windowsVirtualKeyCode: 75, nativeVirtualKeyCode: 75, modifiers: 2 });
    return waitFor(cdp, "!!document.querySelector('[data-testid=\"palette\"]')", 2500, 80);
  })();
  const paletteState = open ? await cdp.evaluate(`(() => {
    const p = document.querySelector('[data-testid="palette"]');
    const input = p && p.querySelector('input');
    return { present: !!p, inputFocused: !!input && document.activeElement === input, items: p ? p.querySelectorAll('[role="option"]').length : 0,
             firstText: p ? (p.innerText || '').split('\\n').slice(0, 6) : [] };
  })()`) : { present: false };
  if (!open) f.push(makeFinding('k1-palette-open', 'fail', 'Ctrl+K did not open [data-testid=palette]'));
  else if (!paletteState.inputFocused) f.push(makeFinding('k1-palette-open', 'fail', 'palette opened but its search input is not focused'));
  else f.push(makeFinding('k1-palette-open', 'pass', `palette opened with ${paletteState.items} command(s) and focused input`));

  // k2: typing filters
  if (open) {
    await cdp.send('Input.insertText', { text: 'set' });
    await sleep(250);
    const filtered = await cdp.evaluate(`(() => {
      const p = document.querySelector('[data-testid="palette"]');
      return { items: p.querySelectorAll('[role="option"]').length, text: (p.innerText || '').replace(/\\s+/g, ' ').slice(0, 160) };
    })()`);
    if (filtered.items === 0 || filtered.items >= paletteState.items) {
      f.push(makeFinding('k2-palette-filter', 'fail', `typing "set" produced ${filtered.items} option(s) from ${paletteState.items}`, { filtered }));
    } else {
      f.push(makeFinding('k2-palette-filter', 'pass', `typing "set" narrowed ${paletteState.items} -> ${filtered.items} option(s)`));
    }
    // k3: Enter navigates
    const before = await cdp.evaluate('location.pathname');
    await press('Enter', 'Enter', 0, 13);
    const navigated = await waitFor(cdp, `location.pathname !== ${JSON.stringify(before)}`, 4000, 80);
    const after = await cdp.evaluate(`({ path: location.pathname, palette: !!document.querySelector('[data-testid="palette"]') })`);
    if (!navigated) f.push(makeFinding('k3-palette-enter', 'fail', `Enter left the path at ${after.path}`));
    else if (after.palette) f.push(makeFinding('k3-palette-enter', 'fail', `Enter navigated to ${after.path} but the palette stayed open`));
    else f.push(makeFinding('k3-palette-enter', 'pass', `Enter navigated ${before} -> ${after.path} and closed the palette`));
  } else {
    f.push(makeFinding('k2-palette-filter', 'fail', 'skipped: palette never opened'));
    f.push(makeFinding('k3-palette-enter', 'fail', 'skipped: palette never opened'));
  }

  // k4: Esc closes and restores focus
  const reopened = await (async () => {
    await cdp.evaluate("document.querySelector('#palette-trigger')?.click()");
    return waitFor(cdp, "!!document.querySelector('[data-testid=\"palette\"]')", 2500, 80);
  })();
  if (!reopened) {
    f.push(makeFinding('k4-palette-esc', 'fail', 'palette trigger did not reopen the palette'));
  } else {
    await press('Escape', 'Escape', 0, 27);
    const closed = await waitFor(cdp, "!document.querySelector('[data-testid=\"palette\"]')", 2500, 80);
    // Under CPU contention focus restoration may arrive late. Retry observation once,
    // never focus/click for the app: missing restoration must still report a warning.
    const focusRestored = "document.activeElement && document.activeElement.getAttribute('data-testid') === 'palette-trigger'";
    const focused = await waitFor(cdp, focusRestored, 600, 40)
      || await waitFor(cdp, focusRestored, 2400, 80);
    const focus = await cdp.evaluate(`(() => { const a = document.activeElement; return a ? (a.getAttribute('data-testid') || a.id || a.tagName) : null; })()`);
    if (!closed) f.push(makeFinding('k4-palette-esc', 'fail', 'Esc did not close the palette'));
    else f.push(makeFinding('k4-palette-esc', focused ? 'pass' : 'warn',
      `Esc closed the palette; focus is on ${focus}${focused ? '' : ' (expected #palette-trigger)'}`));
  }
  return f;
}

/** Tab order + focus ring, measured with real Tab key presses (so :focus-visible applies). */
export async function focusOrderChecks(cdp) {
  const f = [];
  // Start at the TOP of the tab order, not inside the space: focusing #space would leave the probe
  // measuring "what comes after the content", which can never reach the rail.
  await cdp.evaluate(`(() => {
    const first = document.querySelector('#titlebar a, #titlebar button, #titlebar [tabindex]:not([tabindex="-1"])');
    if (first) { first.focus(); return 'titlebar'; }
    if (document.body) document.body.focus();
    return 'body';
  })()`);
  const order = [];
  for (let i = 0; i < 14; i += 1) {
    await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9, nativeVirtualKeyCode: 9 });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9, nativeVirtualKeyCode: 9 });
    await sleep(60);
    const info = await cdp.evaluate(`(() => {
      const a = document.activeElement;
      if (!a) return null;
      const cs = getComputedStyle(a);
      const inRail = !!a.closest('#rail');
      const inContext = !!a.closest('#context-bar');
      const inSpace = !!a.closest('#space');
      const inTitle = !!a.closest('#titlebar');
      const inStatus = !!a.closest('#statusbar');
      return {
        tag: a.tagName.toLowerCase(), id: a.id || '', testid: a.getAttribute('data-testid') || '',
        name: (a.getAttribute('aria-label') || a.textContent || '').trim().slice(0, 50),
        region: inRail ? 'rail' : inContext ? 'context' : inSpace ? 'space' : inTitle ? 'titlebar' : inStatus ? 'statusbar' : 'body',
        outlineWidth: parseFloat(cs.outlineWidth) || 0, outlineStyle: cs.outlineStyle, boxShadow: cs.boxShadow,
      };
    })()`);
    if (!info) break;
    if (order.length && order[order.length - 1].testid === info.testid && order[order.length - 1].id === info.id
        && order[order.length - 1].tag === info.tag && order[order.length - 1].name === info.name) continue;
    order.push(info);
  }
  const regions = order.map((o) => o.region);
  const railIdx = regions.indexOf('rail');
  const ctxIdx = regions.indexOf('context');
  const spaceIdx = regions.indexOf('space');
  if (railIdx < 0 || spaceIdx < 0) {
    f.push(makeFinding('k5-tab-order', 'fail', `Tab sequence reached ${JSON.stringify(regions)} (rail=${railIdx}, context=${ctxIdx}, space=${spaceIdx})`, { order }));
  } else if (!(railIdx < spaceIdx)) {
    f.push(makeFinding('k5-tab-order', 'fail', `space content is reached before the rail: ${JSON.stringify(regions)}`, { order }));
  } else if (ctxIdx >= 0 && !(railIdx < ctxIdx && ctxIdx < spaceIdx)) {
    f.push(makeFinding('k5-tab-order', 'warn', `context bar is out of rail -> context -> content order: ${JSON.stringify(regions)}`, { order }));
  } else {
    f.push(makeFinding('k5-tab-order', 'pass', `Tab reaches rail -> ${ctxIdx >= 0 ? 'context -> ' : ''}content: ${JSON.stringify(regions.slice(0, 8))}`, { order: order.slice(0, 8) }));
  }

  const ring = order.filter((o) => o.outlineStyle !== 'none' && o.outlineWidth >= 1 || (o.boxShadow && o.boxShadow !== 'none'));
  const ringMissing = order.filter((o) => !((o.outlineStyle !== 'none' && o.outlineWidth >= 1) || (o.boxShadow && o.boxShadow !== 'none')));
  if (!order.length) {
    f.push(makeFinding('k6-focus-ring', 'fail', 'Tab moved focus nowhere: no focusable controls'));
  } else if (ringMissing.length) {
    f.push(makeFinding('k6-focus-ring', 'fail', `${ringMissing.length}/${order.length} keyboard-focused control(s) paint no ring: ${ringMissing.slice(0, 6).map((o) => `${o.testid || o.id || o.tag} (outline ${o.outlineStyle} ${o.outlineWidth}px, shadow ${o.boxShadow})`).join('; ')}`, { order }));
  } else {
    f.push(makeFinding('k6-focus-ring', 'pass', `all ${ring.length} keyboard-focused control(s) paint an outline/box-shadow ring`, { order }));
  }
  return f;
}

/* ------------------------------------------------------------------ providers */

export async function providerChecks(cdp, { stub, appOrigin } = {}) {
  const f = [];
  if (!stub) {
    return [makeFinding('p1-provider-flow', 'warn', 'no stub provider (--provider-port unavailable)'),
      makeFinding('p2-invalid-key', 'warn', 'no stub provider'),
      makeFinding('p3-no-secrets', 'warn', 'no stub provider')];
  }
  const goodKey = 'sk-stub-0000000000000000000000000000';
  const badKey = 'sk-bad-0000000000000000000000000000';
  const secrets = [goodKey, badKey];

  await navigate(cdp, `${appOrigin}/providers`, { settleMs: 350 });
  const loaded = await waitFor(cdp, "!!document.querySelector('[data-testid=\"provider-row-custom\"]')", 8000);
  if (!loaded) {
    f.push(makeFinding('p1-provider-flow', 'fail', 'the custom provider row never rendered on /providers'));
    f.push(makeFinding('p2-invalid-key', 'fail', 'skipped: no provider row'));
    f.push(makeFinding('p3-no-secrets', 'fail', 'skipped: no provider row'));
    return f;
  }

  const openConnect = async () => {
    const clicked = await clickSelector(cdp, '[data-testid="provider-connect-custom"]');
    if (!clicked.ok) return clicked.reason;
    await waitFor(cdp, "!!document.querySelector('[data-testid=\"connect-sheet\"], [data-testid=\"consent-sheet\"]')", 4000);
    if (await cdp.evaluate("!!document.querySelector('[data-testid=\"consent-sheet\"]')")) {
      await clickSelector(cdp, '[data-testid="consent-enable"]');
      await waitFor(cdp, "!!document.querySelector('[data-testid=\"connect-sheet\"]')", 4000);
    }
    return (await cdp.evaluate("!!document.querySelector('[data-testid=\"connect-sheet\"]')")) ? null : 'connect sheet did not open';
  };

  // --- p2: a rejected key renders "Invalid key (401)"
  const openFail = await openConnect();
  if (openFail) {
    f.push(makeFinding('p2-invalid-key', 'fail', `could not open the connect sheet: ${openFail}`));
  } else {
    await typeInto(cdp, '[data-testid="connect-base-url"]', stub.baseUrl);
    await typeInto(cdp, '[data-testid="connect-key"]', badKey);
    await clickSelector(cdp, '[data-testid="connect-verify"]');
    const shown = await waitFor(cdp, "(() => { const e = document.querySelector('[data-testid=\"connect-error\"]'); return !!e && !e.hidden && /Invalid key \\(401\\)/.test(e.innerText || ''); })()", 9000, 120);
    const text = await cdp.evaluate("(() => { const e = document.querySelector('[data-testid=\"connect-error\"]'); return e && !e.hidden ? e.innerText.replace(/\\s+/g, ' ').slice(0, 160) : ''; })()");
    if (shown) f.push(makeFinding('p2-invalid-key', 'pass', `rendered "${text}"`));
    else f.push(makeFinding('p2-invalid-key', 'fail', `connect-error did not show "Invalid key (401)"; saw ${JSON.stringify(text)}`));
  }

  // --- p1: connect with a good key -> model list -> default -> test -> disconnect
  const steps = [];
  const fail = (detail) => f.push(makeFinding('p1-provider-flow', 'fail', detail, { steps }));
  const openOk = openFail ? openFail : null;
  let sheetOpen = await cdp.evaluate("!!document.querySelector('[data-testid=\"connect-sheet\"]')");
  if (!sheetOpen) {
    const again = await openConnect();
    if (again) { fail(`could not reopen the connect sheet: ${again}`); }
    sheetOpen = await cdp.evaluate("!!document.querySelector('[data-testid=\"connect-sheet\"]')");
  }
  if (sheetOpen) {
    await typeInto(cdp, '[data-testid="connect-base-url"]', stub.baseUrl);
    await typeInto(cdp, '[data-testid="connect-key"]', goodKey);
    await clickSelector(cdp, '[data-testid="connect-verify"]');
    const connected = await waitFor(cdp, "(() => { const s = document.querySelector('[data-testid=\"connect-success\"]'); return !!s && !s.hidden && /Connected/.test(s.innerText || ''); })()", 12000, 120);
    const successText = await cdp.evaluate("(() => { const s = document.querySelector('[data-testid=\"connect-success\"]'); return s && !s.hidden ? s.innerText.replace(/\\s+/g, ' ') : ''; })()");
    steps.push({ step: 'connect', ok: connected, detail: successText });
    if (!connected) {
      const err = await cdp.evaluate("(() => { const e = document.querySelector('[data-testid=\"connect-error\"]'); return e && !e.hidden ? e.innerText.replace(/\\s+/g, ' ').slice(0, 200) : ''; })()");
      fail(`verify with the stub key did not report Connected (${successText || err || 'no message'})`);
    } else {
      // the sheet closes itself after ~1.2s
      await waitFor(cdp, "!document.querySelector('[data-testid=\"connect-sheet\"]')", 4000);
      const rowState = await cdp.evaluate("(() => { const s = document.querySelector('[data-testid=\"provider-state-custom\"]'); return s ? s.innerText.replace(/\\s+/g, ' ').trim() : ''; })()");
      const rowMeta = await cdp.evaluate("(() => { const r = document.querySelector('[data-testid=\"provider-row-custom\"]'); return r ? (r.innerText || '').replace(/\\s+/g, ' ').slice(0, 220) : ''; })()");
      const modelsListed = /Connected · 2 models/.test(rowState) || /2 models/.test(rowMeta);
      steps.push({ step: 'models', ok: modelsListed, detail: `${rowState} | ${rowMeta.slice(0, 120)}` });
      if (!modelsListed) fail(`row state "${rowState}" does not show the 2 stub models (meta: ${rowMeta.slice(0, 120)})`);

      // model list UI (the local row's Manage opens the picker with every provider's models)
      const manage = await clickSelector(cdp, '[data-testid="provider-connect-local"]');
      const picker = manage.ok && await waitFor(cdp, "!!document.querySelector('[data-testid=\"model-picker\"]')", 6000);
      if (picker) {
        const pickerText = await cdp.evaluate("(() => { const p = document.querySelector('[data-testid=\"model-picker\"]'); return (p.innerText || '').replace(/\\s+/g, ' ').slice(0, 300); })()");
        const hasModels = await cdp.evaluate("[...document.querySelectorAll('[data-testid=\"model-picker\"] [data-model]')].map((b) => b.getAttribute('data-model'))");
        steps.push({ step: 'model-list', ok: hasModels.includes('stub-model-1') && hasModels.includes('stub-model-2'), detail: JSON.stringify(hasModels).slice(0, 160) });
        if (!(hasModels.includes('stub-model-1') && hasModels.includes('stub-model-2'))) fail(`model picker does not list the stub models: ${JSON.stringify(hasModels).slice(0, 160)} (${pickerText.slice(0, 120)})`);
        const picked = await clickSelector(cdp, '[data-testid="model-picker"] [data-model="stub-model-2"]');
        const toast = picked.ok && await waitFor(cdp, "[...document.querySelectorAll('[data-testid=\"toast\"]')].some((t) => /Default model/.test(t.innerText || ''))", 5000, 100);
        const chip = await cdp.evaluate("(() => { const c = document.querySelector('#provider-chip-text'); return c ? c.textContent.trim() : ''; })()");
        steps.push({ step: 'default', ok: Boolean(toast || /stub-model-2/.test(chip)), detail: `toast=${toast} chip=${chip}` });
        if (!toast && !/stub-model-2/.test(chip)) fail('choosing a model did not set the default (no toast, chip unchanged)');
        await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
        await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
        await waitFor(cdp, "!document.querySelector('[data-testid=\"model-picker\"]')", 3000);
      } else {
        steps.push({ step: 'model-list', ok: false, detail: manage.ok ? 'picker did not open' : manage.reason });
        fail(`model picker did not open from the local row (${manage.reason || 'no [data-testid=model-picker]'})`);
      }

      // Test action
      const t = await clickSelector(cdp, '[data-testid="provider-test-custom"]');
      const testToast = t.ok && await waitFor(cdp, "[...document.querySelectorAll('[data-testid=\"toast\"]')].some((x) => /answered|ms/.test(x.innerText || ''))", 9000, 120);
      steps.push({ step: 'test', ok: Boolean(testToast), detail: t.ok ? 'toast observed' : t.reason });
      if (!testToast) fail(`Test did not report a successful 1-token chat (${t.reason || 'no toast'})`);

      // Disconnect (confirm dialog)
      const d = await clickSelector(cdp, '[data-testid="provider-disconnect-custom"]');
      const confirm = d.ok && await waitFor(cdp, "!!document.querySelector('[data-testid=\"confirm-ok\"]')", 4000);
      if (!confirm) {
        steps.push({ step: 'disconnect', ok: false, detail: d.reason || 'confirm dialog missing' });
        fail(`Disconnect did not raise the confirm dialog (${d.reason || 'no confirm-ok'})`);
      } else {
        await clickSelector(cdp, '[data-testid="confirm-ok"]');
        await sleep(900);
        const state = await cdp.evaluate("(() => { const s = document.querySelector('[data-testid=\"provider-state-custom\"]'); return s ? s.innerText.replace(/\\s+/g, ' ').trim() : ''; })()");
        const cleared = /Needs key|Not connected/.test(state);
        steps.push({ step: 'disconnect', ok: cleared, detail: state });
        if (!cleared) fail(`after Disconnect the row still says "${state}"`);
      }
    }
  } else {
    fail('the connect sheet could not be opened');
  }
  if (!f.some((x) => x.id === 'p1-provider-flow' && x.severity === 'fail')) {
    f.unshift(makeFinding('p1-provider-flow', 'pass', `connect -> ${steps.map((s) => s.step).join(' -> ')} all ok`, { steps }));
  }

  // --- p3: no secrets anywhere in the renderer
  const scan = await cdp.evaluate(`(${secretScanFn.toString()})(${JSON.stringify({ secrets, allowToken: '' })})`, 30000);
  const urlsWithKey = (scan.urlsChecked !== undefined) ? [] : [];
  const hits = scan.hits || [];
  if (hits.length) {
    f.push(makeFinding('p3-no-secrets', 'fail', `${hits.length} secret hit(s): ${hits.slice(0, 6).map((h) => `${h.where} ${h.value}`).join('; ')}`, { hits: hits.slice(0, 20) }));
  } else {
    f.push(makeFinding('p3-no-secrets', 'pass', `no key in localStorage/sessionStorage/IndexedDB/DOM/URL (${scan.urlsChecked} URLs checked)`));
  }
  return f;
}

/* ------------------------------------------------------------------ csp / perf */

export async function cspChecks({ origin, token, routes, log = [] }) {
  const f = [];
  const problems = [];
  for (const route of routes) {
    const url = `${origin}${route.path}`;
    let res;
    try {
      res = await fetch(url, {
        headers: token ? { 'X-ValoHub-Token': token } : {},
        redirect: 'manual', signal: AbortSignal.timeout(8000),
      });
    } catch (e) {
      problems.push(`${route.path}: ${e.message}`);
      continue;
    }
    const csp = res.headers.get('content-security-policy') || '';
    const normal = csp.replace(/\s+/g, ' ').trim();
    const ok = normal === CSP_V2;
    if (!ok) problems.push(`${route.path}: ${normal || '(no CSP header)'}`);
    const scriptSrc = (normal.split(';').map((s) => s.trim()).find((s) => s.startsWith('script-src')) || '');
    if (!/^script-src 'self'(;|$)/.test(scriptSrc) || /unsafe-inline|unsafe-eval|\*/.test(scriptSrc)) {
      problems.push(`${route.path}: script-src is "${scriptSrc || '(missing)'}"`);
    }
  }
  if (problems.length) f.push(makeFinding('s1-csp-header', 'fail', problems.slice(0, 6).join(' | '), { problems }));
  else f.push(makeFinding('s1-csp-header', 'pass', `exact v2 policy on all ${routes.length} app routes`));
  return f;
}

export async function inlineScriptCheck(cdp) {
  const r = await cdp.evaluate(`(${cspProbeFn.toString()})()`, 10000);
  if (r.inlineRan || r.handlerRan) {
    return [makeFinding('s2-inline-blocked', 'fail', `inline script executed (script=${r.inlineRan}, handler=${r.handlerRan}); CSP script-src is not blocking inline code`, { result: r })];
  }
  if (!r.violations.length) {
    return [makeFinding('s2-inline-blocked', 'warn', 'inline script did not run but no securitypolicyviolation fired (page may be served without CSP)', { result: r })];
  }
  return [makeFinding('s2-inline-blocked', 'pass', `inline <script> and inline onclick blocked (${r.violations.map((v) => v.directive).join(', ')})`, { result: r })];
}

export async function perfChecks(cdp, { budgetMs = 250 } = {}) {
  const f = [];
  // m1: mount budget — click a rail link and measure until the destination space is in the DOM
  const measured = await cdp.evaluate(`(async () => {
    const res = await window.__gateMountMeasure('#rail-providers', '[data-testid="providers"]', 5000);
    return res;
  })()`, 15000).catch((e) => ({ ok: false, reason: e.message }));
  if (!measured || !measured.ok) {
    f.push(makeFinding('m1-mount-budget', 'fail', `mount could not be measured: ${(measured && measured.reason) || 'unknown'}`));
  } else if (measured.ms > budgetMs) {
    f.push(makeFinding('m1-mount-budget', 'fail', `space mount took ${measured.ms} ms (budget ${budgetMs} ms)`));
  } else {
    f.push(makeFinding('m1-mount-budget', 'pass', `space mount ${measured.ms} ms < ${budgetMs} ms`));
  }
  // m2: layout shift after load
  const cls = await cdp.evaluate("(() => (window.__gate ? { cls: +window.__gate.cls.toFixed(4), entries: window.__gate.entries.length } : null))()");
  if (!cls) {
    f.push(makeFinding('m2-layout-shift', 'warn', 'layout-shift was not observable (instrumentation missing)'));
  } else if (cls.cls > 0.05) {
    // In-house budget 0.05: half the web-vitals "good" ceiling (0.1). The 0.01 used during bring-up
    // flagged cold-start shifts that no user can perceive.
    f.push(makeFinding('m2-layout-shift', 'fail', `cumulative layout shift ${cls.cls} over ${cls.entries} entr(ies)`));
  } else {
    f.push(makeFinding('m2-layout-shift', 'pass', `CLS ${cls.cls} over ${cls.entries} entr(ies)`));
  }
  return f;
}

/* ------------------------------------------------------------------ dead controls */

/**
 * Scripted click probe: every enabled, non-destructive button in the space must produce an observable
 * change (DOM/text/aria/location/overlay/focus). Controls whose effect is state-dependent are reported
 * as warn; everything else that no-ops is a failure.
 */
export async function clickProbe(cdp, candidates, { max = 10, route, path: routePath = null, restore = null, log = [] } = {}) {
  const f = [];
  const results = [];
  // Space controls first, then chrome: a chrome control that opens an overlay must not mask the space.
  const ordered = [...candidates.filter((c) => c.region === 'space'), ...candidates.filter((c) => c.region !== 'space')];
  const list = ordered.slice(0, max);
  const pressEscape = async () => {
    await cdp.send('Input.dispatchKeyEvent', { type: 'rawKeyDown', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
    await cdp.send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 });
    await sleep(120);
  };
  for (const c of list) {
    const label = `${c.name || c.testid || c.selector}`.toLowerCase();
    if (DESTRUCTIVE.some((d) => label.includes(d))) { results.push({ selector: c.selector, name: c.name, status: 'skipped-destructive' }); continue; }
    // Re-clicking a control that is already on (Grid when Grid is the active segment) is a no-op by
    // design; probing it would misreport a working control as dead.
    const active = await cdp.evaluate(`(() => {
      const el = document.querySelector(${JSON.stringify(c.selector)});
      if (!el) return null;
      return el.matches(${JSON.stringify(ACTIVE_STATE_SELECTOR)});
    })()`).catch(() => null);
    if (active === true) { results.push({ selector: c.selector, name: c.name, status: 'skipped-active' }); continue; }
    // A disabled control cannot react to a click by design; d2 already proves it states a reason.
    const disabledNow = await (async () => {
      for (let i = 0; i < 12; i += 1) {
        const d = await cdp.evaluate(`(() => {
          const el = document.querySelector(${JSON.stringify(c.selector)});
          if (!el) return null;
          return Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true';
        })()`).catch(() => null);
        if (d !== true) return d;
        await sleep(150); // a data-backed control enables itself once the replay metadata lands
      }
      return true;
    })();
    if (disabledNow === true) { results.push({ selector: c.selector, name: c.name, status: 'skipped-disabled' }); continue; }
    const before = await cdp.evaluate(DOM_SIG);
    const click = await clickSelector(cdp, c.selector);
    if (!click.ok) {
      // The element may have been removed by the previous probe re-rendering the space: that is a stale
      // handle, not a dead control. Only a control that still exists but cannot be clicked is a failure.
      const stillThere = click.reason === 'element not found'
        ? await cdp.evaluate(`!!document.querySelector(${JSON.stringify(c.selector)})`).catch(() => true)
        : true;
      if (click.reason === 'element not found' && !stillThere) {
        results.push({ selector: c.selector, name: c.name, status: 'stale', reason: 'removed by an earlier probe in this pass' });
        continue;
      }
      results.push({ selector: c.selector, name: c.name, status: 'unclickable', reason: click.reason });
      f.push(makeFinding('d3-click-probe', 'fail', `${c.selector} "${c.name}" is not clickable: ${click.reason}`, { route, control: c }));
      continue;
    }
    let after = before;
    for (let i = 0; i < 6; i += 1) {
      await sleep(120);
      after = await cdp.evaluate(DOM_SIG);
      if (after !== before) break;
    }
    if (after !== before) {
      results.push({ selector: c.selector, name: c.name, status: 'changed' });
    } else {
      const tolerated = STATE_DEPENDENT.some((d) => label.includes(d));
      results.push({ selector: c.selector, name: c.name, status: tolerated ? 'no-op-state-dependent' : 'dead' });
      if (!tolerated) {
        f.push(makeFinding('d3-click-probe', 'fail', `${c.selector} "${c.name}" did nothing when clicked (no DOM, aria, overlay, focus or URL change)`, { route, control: c }));
      }
    }
    // Leave the page in the state the next probe expects: close any overlay the click opened, undo navigation.
    const state = await cdp.evaluate(`({ scrim: !!document.querySelector('.scrim'), path: location.pathname + location.search, modal: !!document.querySelector('[role="dialog"]') })`);
    if (state.scrim || state.modal) { await pressEscape(); }
    if (routePath && state.path !== routePath && restore) {
      await restore();
      await sleep(250);
    }
  }
  const dead = results.filter((r) => r.status === 'dead');
  if (!dead.length) {
    const changed = results.filter((r) => r.status === 'changed').length;
    const skipped = results.filter((r) => r.status.startsWith('skipped') || r.status === 'stale').length;
    f.push(makeFinding('d3-click-probe', 'pass', `${changed}/${results.length} probed control(s) reacted; ${skipped} skipped (destructive/active/stale)`, { route, results }));
  }
  return { findings: f, results };
}

/* ------------------------------------------------------------------ visual checks */

export function collectVisualFindings(visual, viewport) {
  const f = [];
  const label = viewport.label;
  // v1: horizontal overflow
  if (visual.overflow.excessPx > 1) {
    f.push(makeFinding('v1-overflow', 'fail',
      `scrollWidth exceeds innerWidth by ${visual.overflow.excessPx} px; widest: ${visual.overflow.widest.map((o) => `${o.selector} right=${o.right}`).join(', ') || '(none identified)'}`,
      { viewport: label, widest: visual.overflow.widest }));
  } else {
    f.push(makeFinding('v1-overflow', 'pass', `scrollWidth ${visual.viewport.docScrollWidth} <= innerWidth ${visual.viewport.innerWidth}`, { viewport: label }));
  }

  // v2: primary action not clipped
  if (viewport.primaryRequired === false) {
    f.push(makeFinding('v2-primary-visible', 'pass', 'route declares no primary action', { viewport: label }));
  } else if (!visual.primary) {
    f.push(makeFinding('v2-primary-visible', 'warn', 'no .btn--primary in this space to check', { viewport: label }));
  } else if (!visual.primary.insideViewport) {
    f.push(makeFinding('v2-primary-visible', 'fail',
      `primary action ${visual.primary.selector} is outside the viewport (x ${visual.primary.x}, y ${visual.primary.y}, ${visual.primary.w}x${visual.primary.h})`,
      { viewport: label, primary: visual.primary }));
  } else if (visual.primary.cuts.length) {
    f.push(makeFinding('v2-primary-visible', 'fail',
      `primary action ${visual.primary.selector} is clipped by ${visual.primary.cuts.map((c) => `${c.by} (${c.direction})`).join(', ')}`,
      { viewport: label, primary: visual.primary }));
  } else {
    f.push(makeFinding('v2-primary-visible', 'pass', `${visual.primary.selector} ${visual.primary.w}x${visual.primary.h} fully inside ${label}`, { viewport: label }));
  }

  // v3: target size
  if (visual.targets.small.length) {
    f.push(makeFinding('v3-target-size', 'fail',
      `${visual.targets.small.length}/${visual.targets.checked} visible target(s) below 24x24: ${visual.targets.small.slice(0, 8).map((s) => `${s.selector} ${s.w}x${s.h}`).join('; ')}`,
      { viewport: label, small: visual.targets.small }));
  } else {
    f.push(makeFinding('v3-target-size', 'pass', `all ${visual.targets.checked} visible targets >= 24x24`, { viewport: label }));
  }

  // v4: contrast
  if (visual.contrast.failures.length) {
    f.push(makeFinding('v4-contrast', 'fail',
      `${visual.contrast.failures.length}/${visual.contrast.checked} sampled text node(s) under their threshold: ${visual.contrast.failures.slice(0, 5).map((c) => `${c.selector} ${c.ratio}:1 < ${c.need}:1`).join('; ')}`,
      { viewport: label, failures: visual.contrast.failures }));
  } else {
    f.push(makeFinding('v4-contrast', 'pass', `${visual.contrast.checked} text node(s) sampled, all >= 4.5:1 (3:1 large)`, { viewport: label }));
  }
  if (visual.contrast.unverified.length) {
    f.push(makeFinding('v4-contrast', 'warn',
      `${visual.contrast.unverified.length} text node(s) over an image/gradient cannot be verified mechanically`,
      { viewport: label, unverified: visual.contrast.unverified.slice(0, 8) }));
  }

  // v5: reduced motion
  if (!visual.motion || !visual.motion.reducedQuery) {
    f.push(makeFinding('v5-reduced-motion', 'fail', 'matchMedia(prefers-reduced-motion: reduce) did not become true under emulation', { viewport: label }));
  } else {
    const slow = visual.motion.samples.filter((s) => s.transitionMax > 0 || s.animationMax > 0);
    if (slow.length) {
      f.push(makeFinding('v5-reduced-motion', 'fail',
        `${slow.length} element(s) still animate with reduced motion: ${slow.slice(0, 6).map((s) => `${s.selector} ${s.transitionMax}s/${s.animationMax}s`).join('; ')}`,
        { viewport: label, samples: slow }));
    } else {
      f.push(makeFinding('v5-reduced-motion', 'pass', `${visual.motion.samples.length} sampled element(s) are instant under reduce`, { viewport: label }));
    }
  }
  return f;
}

/* ------------------------------------------------------------------ routes */

/** The six v2 surfaces, with the per-space root element each lane declared. */
export function buildRoutes(replayId) {
  const id = replayId || 'v2gate-unknown';
  return [
    { id: 'replays', path: '/replays', space: 'library', root: '[data-testid="library"], [data-testid="library-empty"]', primary: true },
    { id: 'replay', path: `/replay/${encodeURIComponent(id)}`, space: 'replay', root: '[data-testid="replay-header"], [data-testid="replay-error"], [data-testid="empty"]', primary: true },
    { id: 'analysis', path: `/analysis/${encodeURIComponent(id)}`, space: 'analysis', root: '[data-testid="analysis"]', primary: false },
    { id: 'coach', path: `/coach/${encodeURIComponent(id)}`, space: 'coach', root: '[data-testid="coach"]', primary: true },
    { id: 'providers', path: '/providers', space: 'providers', root: '[data-testid="providers"]', primary: true },
    { id: 'settings', path: '/settings', space: 'settings', root: '[data-testid="settings"]', primary: false },
  ];
}

/* ------------------------------------------------------------------ main */

async function main() {
  let args;
  try { args = parseArgs(process.argv.slice(2)); } catch (e) { console.error(`ui_gate: ${e.message}`); console.error(usage()); return 2; }
  if (args.help) { console.log(usage()); return 0; }
  if (args.listChecks) { console.log(CHECK_IDS.join('\n')); return 0; }
  if (!args.out) { console.error('ui_gate: --out DIR is required'); console.error(usage()); return 2; }
  if (!args.baseUrl && !args.home) { console.error('ui_gate: pass --base-url URL or --home DIR'); console.error(usage()); return 2; }

  const repo = path.resolve(import.meta.dirname, '..', '..');
  const out = path.resolve(args.out);
  await mkdir(out, { recursive: true });
  const viewports = args.viewports.map(parseViewport);
  const skip = new Set(args.skip);
  const browserProfileDir = path.join(process.env.TMPDIR || os.tmpdir(), `valohub-ui-gate-v2-${process.pid}`);

  const log = [];
  const cleanups = [];
  const report = {
    schema: 'valoplant_ui_gate/v2',
    generatedAt: new Date().toISOString(),
    node: process.version,
    platform: `${process.platform} ${os.release()}`,
    mode: null,
    target: null,
    routes: [],
    viewports: viewports.map((v) => v.label),
    findings: [],
    manual: [
      {
        id: 'manual-native-menu',
        title: 'No native menu bar in the packaged app',
        why: 'The Electron menu is not in the renderer DOM; it can only be observed on a signed packaged build.',
        how: 'Launch the packaged app (--exe --home DIR) and confirm no File/Edit/View strip is on screen; press Alt once to reveal the file menu, and confirm it hides again after 1 s. Record the screenshot with the run.',
      },
      {
        id: 'manual-caption',
        title: 'Native caption buttons + window drag',
        why: 'titleBarOverlay caption buttons are painted by the OS compositor, invisible to CDP.',
        how: 'On the packaged app, drag the 40 px titlebar to move the window, double-click to maximise, and confirm minimise/maximise/close hit their native hit-test at 125% and 150% scaling.',
      },
    ],
    summary: null,
    log: [],
  };
  let cdp = null;

  const findings = [];
  const addAll = (items) => { for (const f of items) findings.push(f); };
  const printFinding = (f) => {
    const scope = [f.route, f.viewport].filter(Boolean).join(' @ ');
    const tag = f.severity === 'fail' ? 'FAIL' : f.severity === 'warn' ? 'WARN' : 'PASS';
    console.log(`${tag} ${f.id}${scope ? ` [${scope}]` : ''}${f.severity === 'pass' ? '' : ` :: ${f.detail}`}`);
  };

  const run = async () => {
    /* ---- resolve the target ------------------------------------------------- */
    let sessionToken = null;
    if (args.baseUrl) {
      const url = new URL(args.baseUrl);
      if (!/^(127\.0\.0\.1|localhost|\[::1\])$/.test(url.hostname)) throw new Error(`--base-url must be loopback, got ${url.hostname}`);
      if (url.port && Number(url.port) === LIVE_PORT) throw new Error(`refusing ${LIVE_PORT}: the user's live app`);
      const token = args.token || url.searchParams.get('t') || null;
      const browserPort = args.browserPort || (url.port ? Number(url.port) + 1 : 19632);
      assertSafePort(browserPort, true);
      const browser = resolveBrowser(args.browser);
      const launched = await launchBrowser({ ...browser, port: browserPort, userDataDir: browserProfileDir, log });
      cdp = launched.cdp;
      cleanups.push(() => launched.proc.kill());
      report.mode = 'base-url';
      sessionToken = token;
      report.target = { kind: 'base-url', origin: url.origin, token: token ? '(set)' : null, browser: browser.name, browserPort };
    } else if (args.exe) {
      assertSafeHome(args.home);
      assertSafePort(args.port, args.allowPort);
      const appPort = args.appPort || (args.port + 1 <= LANE_PORTS[1] ? args.port + 1 : args.port - 1);
      assertSafePort(appPort, args.allowPort);
      const app = await startPackagedApp({ exe: args.exe, home: path.resolve(args.home), debugPort: args.port, httpPort: appPort, log });
      cdp = app.cdp;
      cleanups.push(app.close);
      const isolation = await packagedIsolation({ cdp, home: args.home });
      report.mode = 'packaged-app';
      report.target = { kind: 'packaged-app', origin: app.origin, exe: args.exe, home: path.resolve(args.home), debugPort: args.port, httpPort: appPort, dataRoot: isolation.dataRoot, pid: isolation.pid };
    } else {
      assertSafeHome(args.home);
      assertSafePort(args.port, args.allowPort);
      const home = path.resolve(args.home);
      if (args.fixture) await seedHome({ home, fixture: path.resolve(args.fixture), repo, log });
      const server = await startDevServer({ repo, home, port: args.port, python: args.python, log, out });
      cleanups.push(server.close);
      const browserPort = args.browserPort || args.port + 1;
      assertSafePort(browserPort, true);
      const browser = resolveBrowser(args.browser);
      const launched = await launchBrowser({ ...browser, port: browserPort, userDataDir: browserProfileDir, log });
      cdp = launched.cdp;
      cleanups.push(() => launched.proc.kill());
      report.mode = 'dev-server';
      report.target = { kind: 'dev-server', origin: server.origin, home, browser: browser.name, browserPort, serverPid: server.proc.pid };
      sessionToken = server.token;
    }
    const origin = report.target.origin;

    const version = await cdp.send('Browser.getVersion', {}, 5000).catch(() => null);
    report.target.browserVersion = version ? version.product : null;

    await cdp.send('Page.enable');
    await cdp.send('Runtime.enable');
    await cdp.send('Network.enable').catch(() => {});
    await cdp.send('DOM.enable').catch(() => {});
    await cdp.send('Page.addScriptToEvaluateOnNewDocument', { source: `(${perfInstrumentFn.toString()})()` }).catch(() => {});

    // Session token: cookie for the page, header for node-side fetches (CSP).
    if (sessionToken) {
      report.target.token = '(set)';
      await cdp.send('Network.setCookie', {
        name: 'vh_session', value: sessionToken, url: `${origin}/`, path: '/', httpOnly: true, sameSite: 'Strict',
      }).catch(() => {});
    }

    // Console + network collection (per route window).
    const consoleErrors = [];
    const externalRequests = new Set();
    const requestUrls = [];
    cdp.on('Runtime.exceptionThrown', (p) => consoleErrors.push({ type: 'exception', text: p.exceptionDetails?.exception?.description || p.exceptionDetails?.text }));
    cdp.on('Runtime.consoleAPICalled', (p) => {
      if (p.type === 'error') consoleErrors.push({ type: 'console.error', text: (p.args || []).map((a) => a.value || a.description || '').join(' ').slice(0, 300) });
    });
    cdp.on('Network.requestWillBeSent', (p) => {
      const url = p.request?.url || '';
      requestUrls.push(url);
      if (!/^(data|blob|about|devtools|chrome-extension):/i.test(url)) {
        try {
          const host = new URL(url).hostname;
          if (!['127.0.0.1', 'localhost', '::1', '[::1]', ''].includes(host)) externalRequests.add(`${host} ${url.slice(0, 120)}`);
        } catch { /* opaque URL */ }
      }
    });

    const replayId = args.replayId || await (async () => {
      try {
        const r = await fetch(`${origin}/api/replays`, { headers: sessionToken ? { 'X-ValoHub-Token': sessionToken } : {}, signal: AbortSignal.timeout(8000) });
        if (!r.ok) return null;
        const body = await r.json();
        const list = Array.isArray(body) ? body : (body.replays || []);
        const ready = list.find((x) => (x.status || 'ready') === 'ready') || list[0];
        return ready && ready.id ? String(ready.id) : null;
      } catch { return null; }
    })();
    let allRoutes = buildRoutes(replayId);
    if (args.routes.length) {
      const want = new Set(args.routes);
      allRoutes = allRoutes.filter((r) => want.has(r.id));
      if (!allRoutes.length) throw new Error(`--routes matched nothing (known: ${buildRoutes().map((r) => r.id).join(', ')})`);
    }
    report.target.replayId = replayId;
    report.target.replayIdSource = args.replayId ? 'cli' : (replayId ? 'api' : 'none');
    report.target.origin = origin;

    /* ---- chrome + route coverage + per-viewport visual checks ---------------- */
    for (const route of allRoutes) {
      const routeEntry = { id: route.id, path: route.path, results: [], shell: null };
      for (const vp of viewports) {
        consoleErrors.length = 0;
        externalRequests.clear();
        await cdp.send('Emulation.setDeviceMetricsOverride', {
          width: vp.width, height: vp.height, deviceScaleFactor: 1, mobile: vp.width <= 480, screenWidth: vp.width, screenHeight: vp.height,
        });
        await cdp.send('Emulation.setEmulatedMedia', {
          features: [{ name: 'prefers-reduced-motion', value: 'reduce' }],
        }).catch(() => {});
        await sleep(80);
        const url = `${origin}${route.path}`;
        await navigate(cdp, url, { readyMs: args.readyMs, settleMs: args.settleMs });

        const shell = await probe(cdp, shellProbeFn, {
          spaceRoot: route.root, interactiveSelector: INTERACTIVE_SELECTOR,
        }).catch((e) => ({ error: e.message, chrome: {}, space: {}, interactive: [] }));

        const visual = await probe(cdp, visualProbeFn, {
          spaceRoot: route.root, interactiveSelector: INTERACTIVE_SELECTOR, reducedMotion: true,
        }).catch((e) => ({ error: e.message, viewport: {}, overflow: { excessPx: 0, widest: [] }, targets: { checked: 0, small: [] }, contrast: { checked: 0, failures: [], unverified: [] }, motion: { reducedQuery: true, samples: [] } }));

        // Disabled-control reasons: collect the text of aria-describedby targets in-page.
        const disabledReasons = await cdp.evaluate(`(() => {
          const out = {};
          for (const el of document.querySelectorAll('[id]')) {
            const t = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
            if (t) out[el.id] = t.slice(0, 200);
          }
          return out;
        })()`).catch(() => ({}));

        const routeFindings = [];
        if (shell.error) {
          routeFindings.push(makeFinding('r1-shell', 'fail', `page probe failed: ${shell.error}`, { route: route.id }));
        } else {
          routeFindings.push(...collectShellFindings(shell, { route: route.id, space: route.space }));
        }
        routeFindings.push(makeFinding('r2-console', consoleErrors.length ? 'fail' : 'pass',
          consoleErrors.length ? `${consoleErrors.length} console error(s): ${consoleErrors.slice(0, 3).map((c) => c.text).join(' | ')}` : 'no console errors or uncaught exceptions',
          { route: route.id, errors: consoleErrors.slice(0, 6) }));
        routeFindings.push(makeFinding('r3-network-scope', externalRequests.size ? 'fail' : 'pass',
          externalRequests.size ? `${externalRequests.size} non-loopback request(s): ${[...externalRequests].slice(0, 5).join(' | ')}` : `all ${requestUrls.length} request(s) stayed on loopback`,
          { route: route.id, external: [...externalRequests].slice(0, 10) }));
        if (!skip.has('visual')) {
          routeFindings.push(...collectVisualFindings({ ...visual, viewport: visual.viewport || { innerWidth: vp.width, innerHeight: vp.height, docScrollWidth: vp.width } }, { ...vp, primaryRequired: route.primary }));
        }

        if (!skip.has('deadcontrols') && !shell.error && vp === viewports[0]) {
          const { findings: cf, candidates } = collectControlFindings(shell, { route: route.id, disabledReasons });
          routeFindings.push(...cf);
          const probeRes = await clickProbe(cdp, candidates, {
            max: 10, route: route.id, path: route.path,
            restore: async () => { await navigate(cdp, `${origin}${route.path}`, { readyMs: args.readyMs, settleMs: args.settleMs }); },
          });
          routeFindings.push(...probeRes.findings);
          routeEntry.clickProbe = probeRes.results;
        }

        const shotName = `${route.id}-${vp.label}.png`;
        try {
          const shot = await cdp.send('Page.captureScreenshot', { format: 'png', fromSurface: true, captureBeyondViewport: false }, 30000);
          await writeFile(path.join(out, shotName), Buffer.from(shot.data, 'base64'));
          routeEntry.results.push({ viewport: vp.label, screenshot: shotName });
        } catch (e) {
          log.push(`[gate] screenshot ${shotName} failed: ${e.message}`);
        }
        if (vp === viewports[0]) routeEntry.shell = shell;
        addAll(routeFindings);
        for (const f of routeFindings) printFinding(f);
      }
      report.routes.push(routeEntry);
    }

    /* ---- keyboard, focus, providers, csp, perf (once) ----------------------- */
    await cdp.send('Emulation.setDeviceMetricsOverride', {
      width: 1280, height: 800, deviceScaleFactor: 1, mobile: false, screenWidth: 1280, screenHeight: 800,
    });
    await cdp.send('Emulation.setEmulatedMedia', { features: [{ name: 'prefers-reduced-motion', value: 'no-preference' }] }).catch(() => {});
    await navigate(cdp, `${origin}/replays`, { readyMs: args.readyMs, settleMs: args.settleMs });

    if (!skip.has('keyboard')) {
      const k = await keyboardChecks(cdp, { log }).catch((e) => [makeFinding('k1-palette-open', 'fail', `keyboard harness error: ${e.message}`)]);
      addAll(k); k.forEach(printFinding);
      await navigate(cdp, `${origin}/replays`, { readyMs: args.readyMs, settleMs: args.settleMs });
      const fo = await focusOrderChecks(cdp).catch((e) => [makeFinding('k5-tab-order', 'fail', `focus harness error: ${e.message}`)]);
      addAll(fo); fo.forEach(printFinding);
    }

    if (!skip.has('providers')) {
      const providerPort = args.providerPort || args.port + 3;
      let stub = null;
      try {
        assertSafePort(providerPort, true);
        stub = await startStubProvider({ port: providerPort, log });
      } catch (e) {
        log.push(`[gate] stub provider failed: ${e.message}`);
      }
      if (stub) cleanups.push(stub.close);
      const p = await providerChecks(cdp, { stub, appOrigin: origin })
        .catch((e) => [makeFinding('p1-provider-flow', 'fail', `provider harness error: ${e.message}`)]);
      addAll(p); p.forEach(printFinding);
      if (stub && p.find((x) => x.id === 'p3-no-secrets')) {
        const stats = stub.stats();
        findings.push(makeFinding('p3-no-secrets', 'pass', `stub provider saw ${stats.requests} request(s); last key was ${stats.lastKey ? 'sent by the backend (not the renderer)' : 'absent'}`, { informational: true }));
      }
    }

    if (!skip.has('csp')) {
      await navigate(cdp, `${origin}/replays`, { readyMs: args.readyMs, settleMs: args.settleMs });
      const c = await inlineScriptCheck(cdp);
      addAll(c); c.forEach(printFinding);
      const h = await cspChecks({ origin, token: sessionToken, routes: allRoutes });
      addAll(h); h.forEach(printFinding);
    }

    if (!skip.has('perf')) {
      await navigate(cdp, `${origin}/replays`, { readyMs: args.readyMs, settleMs: 600 });
      const p = await perfChecks(cdp, {});
      addAll(p); p.forEach(printFinding);
    }

    /* ---- report ------------------------------------------------------------- */
    const fails = findings.filter((f) => f.severity === 'fail');
    const warns = findings.filter((f) => f.severity === 'warn');
    // Warnings that the harness cannot resolve by design (text painted over a gradient has no
    // computable background) are documented exceptions to --fail-on-warn; anything else still blocks.
    const ACCEPTED_WARNINGS = [{ id: 'v4-contrast', match: /over an image\/gradient cannot be verified mechanically/ }];
    const acceptedWarns = warns.filter((w) => ACCEPTED_WARNINGS.some((a) => a.id === w.id && a.match.test(w.detail || '')));
    const blockingWarns = warns.filter((w) => !acceptedWarns.includes(w));
    const byCheck = {};
    for (const f of findings) {
      byCheck[f.id] = byCheck[f.id] || { pass: 0, fail: 0, warn: 0 };
      byCheck[f.id][f.severity] += 1;
    }
    report.findings = findings.filter((f) => f.severity !== 'pass');
    report.summary = {
      pass: fails.length === 0 && (!args.failOnWarn || blockingWarns.length === 0),
      passCount: findings.filter((f) => f.severity === 'pass').length,
      failCount: fails.length,
      warnCount: warns.length,
      acceptedWarnCount: acceptedWarns.length,
      blockingWarnCount: blockingWarns.length,
      routesChecked: allRoutes.length,
      viewportsChecked: viewports.length,
      checksRun: Object.keys(byCheck).length,
      byCheck,
      failedChecks: [...new Set(fails.map((f) => f.id))],
      warnChecks: [...new Set(warns.map((f) => f.id))],
      manualSteps: report.manual.map((m) => m.id),
    };
    report.log = log;
    await writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
    const lines = [
      `ValoHub UI gate v2 — ${report.generatedAt}`,
      `target   : ${report.mode} ${origin}${report.target.replayId ? ` (replay ${report.target.replayId})` : ''}`,
      `routes   : ${allRoutes.map((r) => r.path).join(', ')}`,
      `viewports: ${viewports.map((v) => v.label).join(', ')}`,
      `result   : ${report.summary.pass ? 'PASS' : 'FAIL'} — ${report.summary.passCount} pass / ${fails.length} fail / ${warns.length} warn (${acceptedWarns.length} accepted, ${blockingWarns.length} blocking) over ${report.summary.checksRun} check ids`,
      '',
    ];
    for (const f of fails) lines.push(`FAIL [${f.id}]${f.route ? ` ${f.route}` : ''}${f.viewport ? ` @${f.viewport}` : ''}: ${f.detail}`);
    for (const f of warns) lines.push(`WARN [${f.id}]${f.route ? ` ${f.route}` : ''}${f.viewport ? ` @${f.viewport}` : ''}: ${f.detail}`);
    lines.push('', 'MANUAL (packaged app):');
    for (const m of report.manual) lines.push(`  - ${m.id}: ${m.title} — ${m.how}`);
    lines.push('', `report: ${path.join(out, 'report.json')}`);
    await writeFile(path.join(out, 'summary.txt'), lines.join('\n') + '\n', 'utf8');
    console.log(`\n${lines.slice(0, 6).join('\n')}`);
    return report.summary.pass ? 0 : 1;
  };

  try {
    return await run();
  } catch (e) {
    console.error(`ui_gate: ${e && e.stack ? e.stack : e}`);
    try {
      await writeFile(path.join(out, 'error.txt'), String(e && e.stack ? e.stack : e), 'utf8');
      report.summary = { pass: false, error: String((e && e.message) || e) };
      await writeFile(path.join(out, 'report.json'), JSON.stringify(report, null, 2), 'utf8');
    } catch { /* nothing else to do */ }
    return 2;
  } finally {
    if (!args.keepOpen) {
      for (const fn of cleanups.reverse()) {
        try { await fn(); } catch { /* best effort */ }
      }
      if (cdp) cdp.close();
      await rm(browserProfileDir, { recursive: true, force: true, maxRetries: 3 }).catch(() => {});
    }
  }
}

const INVOKED_DIRECTLY = Boolean(process.argv[1]) && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href;
if (INVOKED_DIRECTLY) {
  // A late CDP/socket rejection after the report is written used to flip a PASS into exit 1.
  process.on('unhandledRejection', (reason) => {
    console.error(`ui_gate: ignored late rejection: ${reason && reason.message ? reason.message : reason}`);
  });
  main().then((code) => { process.exitCode = code; });
}
