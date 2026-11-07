// Settings space (DESIGN-V2 §4.6): section nav + content, every control works or is disabled with a reason.
// Esc closes Settings (the shell navigates back). DOM via el(); no innerHTML with API data.
import { el } from '../shell.js';

const API_DECODER = '/api/decoder';
const API_DECODER_UPDATE = '/api/decoder/update';
const API_INFO = '/api/app/info';
const API_JOBS = '/api/jobs';
const API_PROVIDERS = '/api/ai/providers';
const API_HEALTH = '/api/app/health';

const SECTIONS = [
  { key: 'providers', label: 'AI Providers', title: 'AI Providers' },
  { key: 'appearance', label: 'Appearance', title: 'Appearance' },
  { key: 'parser', label: 'Parser', title: 'Parser' },
  { key: 'privacy', label: 'Data & Privacy', title: 'Data & Privacy' },
  { key: 'about', label: 'About', title: 'About' },
];

const S = {
  root: null,
  ctx: null,
  disposed: true,
  section: 'providers',
  nav: null,
  body: null,
  data: {},          // key -> loaded payload
  errors: {},        // key -> {head, meta}
  loading: false,
  timers: new Set(),
  intervals: new Set(),
  job: null,         // {id, timer}
};

function later(fn, ms) {
  const t = setTimeout(() => { S.timers.delete(t); if (!S.disposed) fn(); }, ms);
  S.timers.add(t);
  return t;
}
function every(fn, ms) {
  const i = setInterval(() => { if (!S.disposed) fn(); }, ms);
  S.intervals.add(i);
  return i;
}
function clearAll() {
  for (const t of S.timers) clearTimeout(t);
  S.timers.clear();
  for (const i of S.intervals) clearInterval(i);
  S.intervals.clear();
  S.job = null;
}
function words(v) { return typeof v === 'string' ? v : ''; }
function fmtNum(n) { return typeof n === 'number' ? n.toLocaleString('en-US') : ''; }
function shortCommit(v) { return words(v) ? words(v).slice(0, 12) : '—'; }

const SECTION_ERRORS = {
  network: 'The backend is not responding.',
  http_404: 'This endpoint is not available in this build.',
  http_500: 'The backend reported an internal error.',
};

function explain(err) {
  const code = words(err && err.code) || 'error';
  return {
    head: SECTION_ERRORS[code] || words(err && err.message) || 'This section could not be loaded.',
    meta: [code !== 'error' ? code : null, err && err.status ? `HTTP ${err.status}` : null,
           err && err.requestId ? `request ${err.requestId}` : null].filter(Boolean).join(' · '),
  };
}

// ------------------------------------------------------------------ shared bits

function row(label, value, { mono = false, note = '' } = {}) {
  return el('div', { class: 'settings__row' },
    el('span', { class: 'settings__label' }, label),
    el('span', { class: `settings__value${mono ? ' settings__value--mono' : ''}` }, value == null || value === '' ? '—' : String(value)),
    note ? el('span', { class: 'settings__note' }, note) : null);
}

function disabledRow(label, value, reason) {
  return el('div', { class: 'settings__row settings__row--off' },
    el('span', { class: 'settings__label' }, label),
    el('button', { type: 'button', class: 'btn', disabled: true, title: reason }, value),
    el('span', { class: 'settings__note', role: 'note' }, reason));
}

function card(title, children, { testid } = {}) {
  return el('section', { class: 'settings__card', 'data-testid': testid || null },
    el('h3', { class: 'settings__cardtitle' }, title),
    children);
}

function failureCard(err, retry) {
  const e = err || { head: 'This section could not be loaded.', meta: '' };
  return el('div', { class: 'settings__fail', role: 'alert' },
    el('p', { class: 'settings__fail-text' }, e.head),
    e.meta ? el('p', { class: 'sheet__codemeta' }, e.meta) : null,
    el('button', { type: 'button', class: 'btn btn--primary', onclick: retry }, 'Retry'));
}

function copyButton(text, label = 'Copy path') {
  const btn = el('button', { type: 'button', class: 'btn btn--ghost', 'data-testid': 'settings-copy-path' }, label);
  btn.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(text);
      btn.textContent = 'Copied';
    } catch {
      btn.textContent = 'Copy failed — select the path';
    }
    later(() => { if (btn.isConnected) btn.textContent = label; }, 1600);
  });
  return btn;
}

// ------------------------------------------------------------------ AI Providers section

async function loadProviders() {
  const data = await S.ctx.api.get(`${API_PROVIDERS}?probe=0`);
  return data || {};
}

function providersSection(data) {
  const list = Array.isArray(data.providers) ? data.providers : [];
  const clouds = list.filter((v) => v.kind !== 'local');
  const local = list.find((v) => v.kind === 'local');
  const connected = clouds.filter((v) => words(v.state) === 'connected').length;
  const consented = clouds.filter((v) => v.consented).length;
  const def = data.default && data.default.provider
    ? `${(list.find((v) => v.id === data.default.provider) || {}).label || data.default.provider}${data.default.model ? ` · ${data.default.model}` : ''}`
    : 'No default model';
  const localCount = local && Array.isArray(local.endpoints)
    ? local.endpoints.filter((e) => e && e.reachable === true).length : 0;
  return card('Providers', [
    row('Default model', def),
    row('Cloud providers connected', `${connected} of ${clouds.length}`),
    row('Cloud providers consented', `${consented} of ${clouds.length}`),
    row('Local servers answering', String(localCount)),
    el('div', { class: 'settings__actions' },
      el('a', { class: 'btn btn--primary', href: '/providers', 'data-testid': 'settings-open-providers' }, 'Open AI Providers'),
      el('button', { type: 'button', class: 'btn btn--ghost', onclick: () => showSection('providers', { force: true }) }, 'Refresh')),
    el('p', { class: 'settings__note' }, 'Keys are stored by the backend in Windows-protected storage; the page never receives one.'),
  ]);
}

// ------------------------------------------------------------------ Appearance section

function densityControl() {
  let current = 'comfortable';
  try {
    const raw = localStorage.getItem('vp.density');
    if (raw) current = JSON.parse(raw);
  } catch { /* keep the default */ }
  const wrap = el('div', { class: 'segmented', 'data-testid': 'settings-density', role: 'group', 'aria-label': 'Density' });
  const apply = (value) => {
    document.documentElement.dataset.density = value;
    try { localStorage.setItem('vp.density', JSON.stringify(value)); } catch { /* storage full or blocked */ }
    for (const b of wrap.children) b.setAttribute('aria-pressed', String(b.getAttribute('data-density') === value));
  };
  for (const [value, label] of [['comfortable', 'Comfortable'], ['compact', 'Compact']]) {
    const btn = el('button', { type: 'button', 'data-density': value, 'aria-pressed': String(current === value) }, label);
    btn.addEventListener('click', () => apply(value));
    wrap.append(btn);
  }
  return wrap;
}

function motionControl() {
  let pref = null;
  try {
    const raw = localStorage.getItem('vp.reducedMotion');
    pref = raw == null ? null : JSON.parse(raw);
  } catch { pref = null; }
  const osReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const input = el('input', { class: 'toggle', type: 'checkbox', id: 'settings-motion', 'data-testid': 'settings-motion' });
  input.checked = pref == null ? osReduced : !!pref;
  input.addEventListener('change', () => {
    document.documentElement.dataset.reducedMotion = input.checked ? 'true' : 'false';
    try { localStorage.setItem('vp.reducedMotion', JSON.stringify(input.checked)); } catch { /* blocked */ }
  });
  return el('label', { class: 'settings__toggle', for: 'settings-motion' },
    input,
    el('span', { class: 'settings__toggletext' },
      el('span', { class: 'settings__label' }, 'Reduce motion'),
      el('span', { class: 'settings__note' },
        `Disables transitions and animation. Windows reports reduced motion: ${osReduced ? 'on' : 'off'} (this switch overrides it).`)));
}

function appearanceSection() {
  const accents = [
    ['Library', '--c-library'], ['Replay', '--c-replay'], ['Analysis', '--c-analysis'],
    ['Coach', '--c-coach'], ['Providers', '--c-providers'], ['Settings', '--c-settings'],
  ];
  return [
    card('Density', [
      el('p', { class: 'settings__note' }, 'Row and control height. Applies to every space immediately.'),
      el('div', { class: 'settings__control' }, densityControl()),
    ]),
    card('Motion', [el('div', { class: 'settings__control' }, motionControl())]),
    card('Space accents', [
      el('p', { class: 'settings__note' }, 'Identity colour used for each space header, chips and chart series. Not selectable in this build.'),
      el('ul', { class: 'settings__swatches' }, accents.map(([name, variable]) => el('li', { class: 'settings__swatch' },
        el('span', { class: 'settings__swatchchip', style: `background:var(${variable})`, 'aria-hidden': 'true' }),
        el('span', null, name)))),
    ]),
  ];
}

// ------------------------------------------------------------------ Parser section

async function loadParser() { return (await S.ctx.api.get(API_DECODER)) || {}; }

function parserSection(data) {
  const updaterEnabled = data.updaterEnabled !== false;
  const branches = Array.isArray(data.registeredBranches) ? data.registeredBranches
    : (Array.isArray(data.supported) ? data.supported : []);
  const validated = Array.isArray(data.replayValidatedBranches) ? data.replayValidatedBranches : [];
  const changed = Array.isArray(data.buildChangedAssemblies) ? data.buildChangedAssemblies : [];
  const lastCheck = words(data.lastCheck);
  const lastResult = data.lastResult && typeof data.lastResult === 'object' ? data.lastResult : null;

  const dryRun = el('input', { class: 'toggle', type: 'checkbox', id: 'settings-parser-dryrun', 'data-testid': 'settings-parser-dry-run' });
  dryRun.checked = true;
  const run = el('button', {
    type: 'button', class: 'btn btn--primary', 'data-testid': 'settings-parser-update', disabled: !updaterEnabled,
  }, 'Check for update');
  const progressHost = el('div', { class: 'settings__progress', 'data-testid': 'settings-parser-progress', role: 'status', 'aria-live': 'polite' });
  run.addEventListener('click', () => startUpdate(dryRun.checked, run, progressHost));

  return [
    card('Decoder', [
      row('Status', words(data.status) || 'unknown'),
      row('Parser commit', shortCommit(data.parserCommit), { mono: true }),
      row('Build state', words(data.buildState) || '—'),
      row('Changed assemblies', changed.length ? changed.join(', ') : 'none'),
      row('Registered branches', branches.length ? branches.join(', ') : '—'),
      row('Validated by a real replay', validated.length ? validated.join(', ') : 'none yet'),
      row('Last check', lastCheck || 'never'),
      row('Last result', lastResult ? `${words(lastResult.status) || '—'}${lastResult.dryRun ? ' (dry run)' : ''}` : '—'),
    ]),
    card('Update', [
      el('p', { class: 'settings__note' },
        updaterEnabled
          ? 'Runs the bundled updater. A dry run checks without changing the decoder.'
          : 'The packaged app updates the parser through the installer, so this control is disabled here.'),
      el('div', { class: 'settings__control settings__control--row' },
        el('label', { class: 'settings__toggle', for: 'settings-parser-dryrun' }, dryRun,
          el('span', { class: 'settings__toggletext' },
            el('span', { class: 'settings__label' }, 'Dry run'),
            el('span', { class: 'settings__note' }, 'Check only; nothing is written.'))),
        run),
      progressHost,
    ]),
  ];
}

async function startUpdate(dryRun, button, host) {
  if (!S.ctx || button.disabled) return;
  button.disabled = true;
  button.textContent = 'Starting…';
  host.replaceChildren(el('span', { class: 'settings__note' }, 'Queued…'));
  try {
    const started = await S.ctx.api.post(API_DECODER_UPDATE, { dryRun: !!dryRun });
    S.job = { id: words(started && started.jobId) || null };
    pollJob(host);
  } catch (err) {
    if (err && err.status === 409) {
      S.job = { id: null };
      host.replaceChildren(el('span', { class: 'settings__note' }, 'An update is already running — watching it.'));
      pollJob(host);
    } else {
      const e = explain(err);
      host.replaceChildren(el('p', { class: 'sheet__error', role: 'alert' },
        el('strong', null, e.head), e.meta ? el('span', { class: 'sheet__codemeta' }, e.meta) : null));
    }
    button.disabled = false;
    button.textContent = 'Check for update';
  }
}

function pollJob(host) {
  if (S.job && S.job.timer) { clearInterval(S.job.timer); S.intervals.delete(S.job.timer); }
  const startedAt = Date.now();
  const timer = every(async () => {
    if (!S.ctx) return;
    if (Date.now() - startedAt > 10 * 60 * 1000) {
      stopJob(host, 'The update did not report back within 10 minutes.');
      return;
    }
    let jobs = [];
    try {
      const data = await S.ctx.api.get(API_JOBS);
      jobs = Array.isArray(data && data.jobs) ? data.jobs : [];
    } catch { return; }
    const job = S.job && S.job.id
      ? jobs.find((j) => j && j.id === S.job.id)
      : jobs.find((j) => j && j.kind === 'decoder-update' && ['queued', 'running'].includes(j.status));
    if (!job) return;
    paintJob(host, job);
    if (job.status === 'done' || job.status === 'error') {
      stopJob(host, null);
      showSection('parser', { force: true });
    }
  }, 1000);
  if (S.job) S.job.timer = timer;
}

function paintJob(host, job) {
  const pct = Math.max(0, Math.min(100, Number(job.pct) || 0));
  host.replaceChildren(
    el('div', { class: 'progress', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(pct) },
      el('span', { style: `--value:${pct}%` })),
    el('p', { class: 'settings__note' },
      `${words(job.stage) || 'working'} · ${pct}%${words(job.message) ? ` · ${words(job.message)}` : ''}${words(job.status) === 'error' && words(job.error) ? ` · error: ${words(job.error)}` : ''}`));
}

function stopJob(host, message) {
  if (S.job && S.job.timer) { clearInterval(S.job.timer); S.intervals.delete(S.job.timer); }
  S.job = null;
  if (message) host.replaceChildren(el('p', { class: 'settings__note' }, message));
  const btn = S.body && S.body.querySelector('[data-testid="settings-parser-update"]');
  if (btn) { btn.disabled = false; btn.textContent = 'Check for update'; }
}

// ------------------------------------------------------------------ Data & Privacy section

async function loadInfo() { return (await S.ctx.api.get(API_INFO)) || {}; }

function privacySection(data) {
  const paths = data.paths && typeof data.paths === 'object' ? data.paths : {};
  const dataRoot = words(paths.data_root);
  const cache = words(paths.cache);
  const logs = words(paths.logs);
  const features = Array.isArray(data.features) ? data.features.map(String) : [];
  const canOpen = features.includes('open-folder');
  const canClear = features.includes('clear-cache');

  const openFolder = canOpen
    ? el('button', {
      type: 'button', class: 'btn', 'data-testid': 'settings-open-folder',
      onclick: async () => {
        try { await S.ctx.api.post('/api/app/open-folder', { path: 'data_root' }); } catch (err) {
          S.ctx.ui.toast(explain(err).head, 'error');
        }
      },
    }, 'Open folder')
    : el('button', {
      type: 'button', class: 'btn', disabled: true, 'data-testid': 'settings-open-folder',
      title: 'This build serves no folder-opening endpoint',
    }, 'Open folder');

  return [
    card('Data folder', [
      row('Location', dataRoot || 'unknown', { mono: true }),
      el('div', { class: 'settings__actions' },
        dataRoot ? copyButton(dataRoot) : null,
        openFolder),
      el('p', { class: 'settings__note' },
        canOpen
          ? 'Opens the folder in Explorer.'
          : 'This build has no folder-opening endpoint, so the folder is copied to the clipboard instead — paste it into Explorer.'),
    ]),
    card('Cache and logs', [
      row('Cache folder', cache || 'unknown', { mono: true }),
      el('div', { class: 'settings__actions' },
        cache ? copyButton(cache, 'Copy cache path') : null,
        canClear
          ? el('button', {
            type: 'button', class: 'btn btn--danger', 'data-testid': 'settings-clear-cache',
            onclick: async () => {
              const ok = await S.ctx.ui.confirm({
                title: 'Clear the cache?', body: 'Deletes cached model lists and staged files in the cache folder. Replays are not touched.',
                confirmLabel: 'Clear cache', danger: true,
              });
              if (!ok) return;
              try { await S.ctx.api.post('/api/app/clear-cache', { scope: 'cache' }); S.ctx.ui.toast('Cache cleared', 'success'); } catch (err) {
                S.ctx.ui.toast(explain(err).head, 'error');
              }
            },
          }, 'Clear cache')
          : el('button', {
            type: 'button', class: 'btn', disabled: true, 'data-testid': 'settings-clear-cache',
            title: 'No cache-clear endpoint in this build',
          }, 'Clear cache')),
      el('p', { class: 'settings__note' },
        canClear ? 'Clearing the cache never deletes replays.' : 'Disabled: this build serves no cache-clear endpoint, so nothing can be cleared here.'),
      row('Log folder', logs || 'unknown', { mono: true }),
      el('div', { class: 'settings__actions' },
        logs ? copyButton(logs, 'Copy log path') : null,
        el('button', {
          type: 'button', class: 'btn', disabled: true, 'data-testid': 'settings-egress-log',
          title: 'No egress-log endpoint in this build',
        }, 'Open egress log')),
      el('p', { class: 'settings__note' },
        'Disabled: cloud requests are made only by the backend and there is no egress-log endpoint in this build. The log folder above holds the redacted sidecar logs.'),
    ]),
    card('Cloud egress', [
      row('Offline by default', data.offline === false ? 'no' : 'yes'),
      row('Updater', data.updaterEnabled === false ? 'installer-managed' : 'in-app'),
      el('p', { class: 'settings__note' },
        'Every cloud provider needs its own consent record with the host it may reach. Player names are replaced before anything is sent and the replay file never leaves this PC.'),
    ]),
  ];
}

// ------------------------------------------------------------------ About section

async function loadAbout() {
  const [info, decoder, health] = await Promise.allSettled([
    S.ctx.api.get(API_INFO), S.ctx.api.get(API_DECODER), S.ctx.api.get(API_HEALTH),
  ]);
  const pick = (r) => (r.status === 'fulfilled' ? r.value : null);
  return { info: pick(info) || {}, decoder: pick(decoder) || {}, health: pick(health) || {} };
}

function aboutSection(data) {
  const info = data.info || {};
  const decoder = data.decoder || {};
  const health = data.health || {};
  return [
    card('Build', [
      el('div', { class: 'about__identity' },
        el('span', { class: 'about__mark', 'aria-hidden': 'true' }),
        el('span', { class: 'about__name' }, 'ValoHub')),
      row('App', words(info.app) || 'ValoHub'),
      row('Version', words(info.version) || words(health.version) || '—'),
      row('Mode', words(info.mode) || '—'),
      row('Build commit', words(info.buildCommit) || words(info.build_commit) || 'not reported', { mono: true, note: 'Packaged build source commit from BUILD-INFO.json, reported by /api/app/info (VALOHUB_BUILD_COMMIT).' }),
      row('Backend', health.ok === false ? 'unreachable' : 'ok'),
      row('Process id', info.pid == null ? '—' : String(info.pid), { mono: true }),
    ]),
    card('Parser', [
      row('Decoder status', words(decoder.status) || '—'),
      row('Parser commit', shortCommit(decoder.parserCommit), { mono: true }),
    ]),
    card('Licenses', [
      disabledRow('Third-party licenses', 'Open licenses', 'No license viewer endpoint in this build; licenses ship with the installer and the bundle.'),
      el('p', { class: 'settings__note' },
        'ValoHub bundles a .NET replay decoder and JavaScript/Electron dependencies. Their notices are part of the packaged artifact.'),
    ]),
  ];
}

// ------------------------------------------------------------------ section plumbing

const LOADERS = {
  providers: loadProviders,
  appearance: async () => null,
  parser: loadParser,
  privacy: loadInfo,
  about: loadAbout,
};

const RENDERERS = {
  providers: providersSection,
  appearance: appearanceSection,
  parser: parserSection,
  privacy: privacySection,
  about: aboutSection,
};

function setNav(sectionKey) {
  if (!S.nav) return;
  for (const b of S.nav.children) {
    const on = b.getAttribute('data-section') === sectionKey;
    if (on) b.setAttribute('aria-current', 'true'); else b.removeAttribute('aria-current');
    b.tabIndex = on ? 0 : -1;
  }
}

async function showSection(key, { force = false } = {}) {
  if (!S.ctx || !S.body) return;
  const section = SECTIONS.find((s) => s.key === key) || SECTIONS[0];
  S.section = section.key;
  setNav(section.key);
  S.body.replaceChildren(el('div', { class: 'skeleton', 'aria-busy': 'true', 'aria-label': 'Loading' },
    el('div', { class: 'skeleton__row' }), el('div', { class: 'skeleton__row' }), el('div', { class: 'skeleton__row' })));
  if (!force && S.data[section.key] !== undefined && !S.errors[section.key]) {
    paint(section.key);
    return;
  }
  delete S.errors[section.key];
  try {
    S.data[section.key] = await LOADERS[section.key]();
  } catch (err) {
    S.errors[section.key] = explain(err);
  }
  if (S.disposed || !S.body) return;
  paint(section.key);
}

function paint(key) {
  if (!S.body) return;
  const section = SECTIONS.find((s) => s.key === key) || SECTIONS[0];
  const err = S.errors[key];
  const head = el('header', { class: 'settings__head' },
    el('h2', { class: 'settings__title' }, section.title));
  let content;
  if (err) content = [failureCard(err, () => showSection(key, { force: true }))];
  else {
    try {
      const rendered = RENDERERS[key](S.data[key]);
      content = Array.isArray(rendered) ? rendered : (rendered == null ? [] : [rendered]);
    } catch (e) {
      content = [failureCard({ head: 'This section could not be rendered.', meta: '' }, () => showSection(key, { force: true }))];
    }
  }
  S.body.replaceChildren(head, ...content);
  S.body.scrollTop = 0;
}

function buildNav() {
  const nav = el('nav', { class: 'settings-nav', 'data-testid': 'settings-nav', 'aria-label': 'Settings sections' });
  for (const s of SECTIONS) {
    const btn = el('button', { type: 'button', class: 'settings-nav__item', 'data-section': s.key, 'data-testid': `settings-section-${s.key}` }, s.label);
    btn.addEventListener('click', () => showSection(s.key));
    nav.append(btn);
  }
  nav.addEventListener('keydown', (e) => {
    const items = [...nav.children];
    const i = items.indexOf(document.activeElement);
    if (i < 0) return;
    let next = null;
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') next = (i + 1) % items.length;
    else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') next = (i - 1 + items.length) % items.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = items.length - 1;
    if (next == null) return;
    e.preventDefault();
    items[next].focus();
    showSection(items[next].getAttribute('data-section'));
  });
  return nav;
}

// ------------------------------------------------------------------ space module

export default {
  id: 'settings',
  title: 'Settings',
  accent: 'settings',
  async mount(root, ctx) {
    S.root = root;
    S.ctx = ctx;
    S.disposed = false;
    S.data = {};
    S.errors = {};
    if (S.job && S.job.timer) { clearInterval(S.job.timer); S.intervals.delete(S.job.timer); }
    S.job = null;

    S.nav = buildNav();
    S.body = el('div', { class: 'settings-body', 'data-testid': 'settings-body' });
    root.replaceChildren(el('div', { class: 'settings-layout', 'data-testid': 'settings' }, S.nav, S.body));

    const wanted = words(ctx.query && ctx.query.get ? ctx.query.get('section') : '');
    const initial = SECTIONS.some((s) => s.key === wanted) ? wanted : 'providers';
    await showSection(initial, { force: true });
  },
  unmount() {
    S.disposed = true;
    clearAll();
    S.root = null;
    S.ctx = null;
    S.nav = null;
    S.body = null;
    S.data = {};
    S.errors = {};
  },
};
