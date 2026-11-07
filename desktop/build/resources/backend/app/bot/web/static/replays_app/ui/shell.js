// ValoHub UI v2 shell: chrome, router, palette, toasts, modal, api client.
// Contract: docs/ui/SHELL-CONTRACT.md. No innerHTML with data; DOM built with el().

export function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [k, v] of Object.entries(attrs)) {
      if (v == null || v === false) continue;
      if (k === 'class') node.className = v;
      else if (k === 'text') node.textContent = v;
      else if (k === 'dataset') Object.assign(node.dataset, v);
      else if (k.startsWith('on') && typeof v === 'function') node.addEventListener(k.slice(2).toLowerCase(), v);
      else node.setAttribute(k, v === true ? '' : String(v));
    }
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    node.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return node;
}

export class ApiError extends Error {
  constructor(status, code, message, requestId) {
    super(message || code || `HTTP ${status}`);
    this.name = 'ApiError';
    this.status = status;
    this.code = code || 'error';
    this.requestId = requestId || null;
  }
}

const ROUTES = [
  { re: /^\/replays\/?$/, space: 'library', params: () => ({}) },
  { re: /^\/replay\/([^/]+)\/?$/, space: 'replay', params: (m) => ({ id: decodeURIComponent(m[1]) }) },
  { re: /^\/analysis(?:\/([^/]+))?\/?$/, space: 'analysis', params: (m) => ({ id: m[1] ? decodeURIComponent(m[1]) : undefined }) },
  { re: /^\/coach(?:\/([^/]+))?\/?$/, space: 'coach', params: (m) => ({ id: m[1] ? decodeURIComponent(m[1]) : undefined }) },
  { re: /^\/providers\/?$/, space: 'providers', params: () => ({}) },
  { re: /^\/settings\/?$/, space: 'settings', params: () => ({}) },
];
const RAIL_OF = { library: 'library', replay: 'library', analysis: 'analysis', coach: 'coach', providers: 'providers', settings: 'settings' };

export function matchRoute(pathname) {
  for (const r of ROUTES) {
    const m = r.re.exec(pathname);
    if (m) return { space: r.space, params: r.params(m) };
  }
  return null;
}

// ---------- localStorage (UI prefs only) ----------
const SECRET_SHAPE = /(sk-[A-Za-z0-9]{8,}|AIza[0-9A-Za-z_-]{20,}|Bearer\s+\S+|api[_-]?key)/i;
function lsGet(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw == null ? fallback : JSON.parse(raw);
  } catch { return fallback; }
}
function lsSet(key, value) {
  try {
    const raw = JSON.stringify(value);
    if (SECRET_SHAPE.test(raw)) return false; // never persist anything secret-shaped
    localStorage.setItem(key, raw);
    return true;
  } catch { return false; }
}

// ---------- shortcuts ----------
function keyName(e) {
  const parts = [];
  if (e.ctrlKey || e.metaKey) parts.push('Ctrl');
  if (e.altKey) parts.push('Alt');
  if (e.shiftKey) parts.push('Shift');
  let k = e.key;
  if (k === ' ') k = 'Space';
  else if (k.length === 1) k = k.toUpperCase();
  parts.push(k);
  return parts.join('+');
}
function isTyping(t) {
  return !!t && (t.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(t.tagName));
}

export function createShell({ spaces: spaceList }) {
  const spaces = new Map(spaceList.map((s) => [s.id, s]));
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;
  const mainEl = $('space');
  const overlayRoot = $('overlay-root');
  const announcer = $('announcer');
  const reduceMq = window.matchMedia('(prefers-reduced-motion: reduce)');

  const globalShortcuts = new Map();
  let spaceShortcuts = new Map();
  let current = null; // { space, ctx, seq }
  let seq = 0;
  let status = { parser: null, provider: null, job: null };
  let overlayStack = [];
  let replayCache = null;

  // ---------- state ----------
  function stateFor(ns) {
    return {
      get: (key, fallback) => lsGet(`vp.${ns}.${key}`, fallback),
      set: (key, value) => lsSet(`vp.${ns}.${key}`, value),
    };
  }

  // ---------- api ----------
  async function request(method, path, body, signal) {
    if (typeof path !== 'string' || !path.startsWith('/')) throw new ApiError(0, 'bad_path', 'API paths must be same-origin absolute paths');
    const init = { method, credentials: 'same-origin', headers: { Accept: 'application/json' }, signal };
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    let res;
    try { res = await fetch(path, init); } catch (err) {
      if (err && err.name === 'AbortError') throw err;
      throw new ApiError(0, 'network', 'Backend unreachable');
    }
    const text = await res.text();
    let data = null;
    if (text) { try { data = JSON.parse(text); } catch { data = null; } }
    if (!res.ok) {
      const d = data && typeof data === 'object' ? data : {};
      const err = d.error && typeof d.error === 'object' ? d.error : d;
      throw new ApiError(res.status, err.code || (typeof d.error === 'string' ? d.error : null) || `http_${res.status}`,
        err.message || res.statusText || `HTTP ${res.status}`, err.request_id || err.requestId || res.headers.get('x-request-id'));
    }
    return data;
  }
  async function stream(path, body, { onEvent, signal } = {}) {
    let res;
    try {
      res = await fetch(path, {
        method: body === undefined ? 'GET' : 'POST', credentials: 'same-origin', signal,
        headers: { Accept: 'text/event-stream', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (err) {
      if (err && err.name === 'AbortError') throw err;
      throw new ApiError(0, 'network', 'Backend unreachable');
    }
    if (!res.ok || !res.body) throw new ApiError(res.status, `http_${res.status}`, res.statusText || 'Stream failed', res.headers.get('x-request-id'));
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    const flush = (block) => {
      let event = 'message'; const data = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith('event:')) event = line.slice(6).trim();
        else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''));
      }
      if (!data.length) return;
      const raw = data.join('\n');
      let parsed = raw;
      try { parsed = JSON.parse(raw); } catch { /* keep text */ }
      if (onEvent) onEvent({ event, data: parsed });
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.search(/\r?\n\r?\n/)) >= 0) {
        flush(buf.slice(0, i));
        buf = buf.slice(i).replace(/^\r?\n\r?\n/, '');
      }
    }
    if (buf.trim()) flush(buf);
  }
  const api = {
    get: (p) => request('GET', p),
    post: (p, b) => request('POST', p, b === undefined ? {} : b),
    put: (p, b) => request('PUT', p, b === undefined ? {} : b),
    del: (p) => request('DELETE', p),
    stream,
    upload,
  };

  // Multipart upload with progress (library import). XHR is the only way to get upload progress.
  function upload(path, file, { onProgress, signal, fields, method = 'PUT' } = {}) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open(method, path, true);
      xhr.withCredentials = true;
      const form = new FormData();
      for (const [k, v] of Object.entries(fields || {})) { if (v != null) form.append(k, String(v)); }
      form.append('file', file, file.name);
      if (onProgress) xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(e.loaded / e.total); };
      const onAbort = () => xhr.abort();
      if (signal) {
        if (signal.aborted) { reject(new DOMException('Aborted', 'AbortError')); return; }
        signal.addEventListener('abort', onAbort, { once: true });
      }
      const done = () => { if (signal) signal.removeEventListener('abort', onAbort); };
      xhr.onerror = () => { done(); reject(new ApiError(0, 'network', 'Backend unreachable')); };
      xhr.onabort = () => { done(); reject(new DOMException('Aborted', 'AbortError')); };
      xhr.onload = () => {
        done();
        let data = null;
        try { data = xhr.responseText ? JSON.parse(xhr.responseText) : null; } catch { data = null; }
        if (xhr.status >= 200 && xhr.status < 300) { resolve(data); return; }
        const d = data && typeof data === 'object' ? data : {};
        const err = d.error && typeof d.error === 'object' ? d.error : d;
        reject(new ApiError(xhr.status, err.code || `http_${xhr.status}`, err.message || xhr.statusText || `HTTP ${xhr.status}`,
          err.request_id || xhr.getResponseHeader('x-request-id')));
      };
      xhr.send(form);
    });
  }

  // Canonical moment link: path only, never the loopback host/port.
  const links = {
    moment(id, round, seconds) {
      const q = new URLSearchParams();
      const r = Number(round);
      if (Number.isFinite(r) && r >= 1) q.set('r', String(Math.trunc(r)));
      const s = Number(seconds);
      if (Number.isFinite(s) && s >= 0) q.set('t', String(Math.round(s * 10) / 10));
      const qs = q.toString();
      return `/replay/${encodeURIComponent(String(id))}${qs ? `?${qs}` : ''}`;
    },
  };

  // ---------- announcer, motion, density ----------
  function announce(msg) {
    announcer.textContent = '';
    window.setTimeout(() => { announcer.textContent = msg; }, 30);
  }
  function applyMotion() {
    const pref = lsGet('vp.reducedMotion', null);
    const on = pref == null ? reduceMq.matches : !!pref;
    root.dataset.reducedMotion = on ? 'true' : 'false';
  }
  function setDensity(d) {
    root.dataset.density = d;
    lsSet('vp.density', d);
  }
  function setRail(open) {
    document.body.dataset.rail = open ? 'open' : 'closed';
    lsSet('vp.rail.expanded', !!open);
  }
  const railOpen = () => document.body.dataset.rail === 'open';

  // ---------- toasts ----------
  let toastStack = null;
  function toast(message, kind = 'info') {
    if (!toastStack) { toastStack = el('div', { class: 'toast-stack', role: 'status', 'aria-live': 'polite' }); document.body.append(toastStack); }
    const k = ['info', 'success', 'error'].includes(kind) ? kind : 'info';
    const t = el('div', { class: `toast toast--${k}`, 'data-testid': 'toast' }, String(message));
    toastStack.append(t);
    window.setTimeout(() => t.remove(), k === 'error' ? 7000 : 4000);
    return t;
  }

  // ---------- overlays (modal, palette) ----------
  function focusables(container) {
    return [...container.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')];
  }
  function openOverlay(content, { label, top = false, onClose, testid, opener = null } = {}) {
    const openerEl = opener || document.activeElement;
    const scrim = el('div', { class: `scrim${top ? ' scrim--top' : ''}`, 'data-testid': testid || 'modal-scrim' });
    content.setAttribute('role', 'dialog');
    content.setAttribute('aria-modal', 'true');
    content.setAttribute('aria-label', label || 'Dialog');
    scrim.append(content);
    scrim.addEventListener('mousedown', (e) => { if (e.target === scrim) entry.close(); });
    // Esc/Tab are handled on the document in the capture phase: a CDP/synthetic key press can land on
    // body when focus was moved programmatically, and the scrim-only listener then never sees it.
    const onDocKey = (e) => {
      if (overlayStack[overlayStack.length - 1] !== entry) return;
      if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); entry.close(); }
      else if (e.key === 'Tab') {
        const f = focusables(content);
        if (!f.length) { e.preventDefault(); return; }
        const first = f[0], last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', onDocKey, true);
    const entry = {
      scrim,
      close() {
        if (!scrim.isConnected) return;
        document.removeEventListener('keydown', onDocKey, true);
        scrim.remove();
        overlayStack = overlayStack.filter((o) => o !== entry);
        if (openerEl && openerEl.isConnected) openerEl.focus();
        if (onClose) onClose();
      },
    };
    overlayStack.push(entry);
    overlayRoot.append(scrim);
    const f = focusables(content);
    (content.querySelector('[data-autofocus]') || f[0] || content).focus?.();
    return entry;
  }
  function openModal(contentEl, { label } = {}) {
    closeModal();
    const box = el('div', { class: 'modal' }, contentEl);
    modalEntry = openOverlay(box, { label });
    return modalEntry;
  }
  let modalEntry = null;
  function closeModal() { if (modalEntry) { modalEntry.close(); modalEntry = null; } }
  function confirmDialog({ title = 'Confirm', body = '', confirmLabel = 'Confirm', danger = false } = {}) {
    return new Promise((resolve) => {
      let settled = false;
      const done = (v) => { if (!settled) { settled = true; resolve(v); } };
      const ok = el('button', { type: 'button', class: `btn ${danger ? 'btn--danger' : 'btn--primary'}`, 'data-testid': 'confirm-ok', 'data-autofocus': '' }, confirmLabel);
      const cancel = el('button', { type: 'button', class: 'btn', 'data-testid': 'confirm-cancel' }, 'Cancel');
      const box = el('div', { class: 'modal' },
        el('h2', { class: 'modal__title' }, title), el('p', { class: 'empty__body' }, body),
        el('div', { class: 'modal__actions' }, cancel, ok));
      const entry = openOverlay(box, { label: title, onClose: () => done(false) });
      ok.addEventListener('click', () => { done(true); entry.close(); });
      cancel.addEventListener('click', () => entry.close());
    });
  }

  // ---------- ui helpers for spaces ----------
  function skeleton(host, rows = 4) {
    host.replaceChildren(el('div', { class: 'skeleton', 'data-testid': 'skeleton', 'aria-busy': 'true', 'aria-label': 'Loading' },
      Array.from({ length: rows }, () => el('div', { class: 'skeleton__row' }))));
  }
  function empty(host, { icon = '◌', title = '', body = '', actionLabel, onAction } = {}) {
    const card = el('div', { class: 'empty', 'data-testid': 'empty' },
      el('div', { class: 'empty__art', 'aria-hidden': 'true' }, icon),
      el('h2', { class: 'empty__title' }, title),
      body ? el('p', { class: 'empty__body' }, body) : null,
      actionLabel ? el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'empty-action', onclick: () => onAction && onAction() }, actionLabel) : null);
    host.replaceChildren(card);
    return card;
  }
  function setStatus(patch = {}) {
    status = { ...status, ...patch };
    const seg = (id) => $(id);
    const parser = status.parser;
    if (parser != null) {
      const p = typeof parser === 'string' ? { text: parser } : parser;
      seg('status-parser').querySelector('.seg-text').textContent = `Parser: ${p.text ?? 'unknown'}`;
      seg('status-parser').querySelector('.dot').className = `dot${p.level ? ` dot--${p.level}` : ''}`;
    }
    if (status.provider != null) {
      const p = typeof status.provider === 'string' ? { text: status.provider } : status.provider;
      seg('status-provider').querySelector('.seg-text').textContent = `Model: ${p.text ?? 'none'}`;
      $('provider-chip-text').textContent = p.chip || p.text || 'No model';
      $('provider-chip').setAttribute('aria-label', `AI provider: ${p.chip || p.text || 'none'}`);
      $('provider-chip').querySelector('.dot').className = `dot${p.level ? ` dot--${p.level}` : ''}`;
    }
    if (status.job != null) {
      const j = typeof status.job === 'string' ? { text: status.job } : status.job;
      const label = j.text ?? 'Job';
      const pct = Number.isFinite(j.progress) ? ` ${Math.round(j.progress * 100)}%` : '';
      seg('status-job').querySelector('.seg-text').textContent = `${label}${pct}`;
    }
    if (status.build != null) {
      const b = typeof status.build === 'string' ? { text: status.build } : status.build;
      const el = seg('status-build');
      if (el) el.querySelector('.seg-text').textContent = b.text ?? '';
    }
  }
  const ui = { toast, confirm: confirmDialog, openModal, closeModal, setStatus, skeleton, empty };

  // ---------- routing ----------
  function navigate(path, { replace = false } = {}) {
    const u = new URL(path, location.origin);
    if (u.origin !== location.origin) return;
    const next = u.pathname + u.search;
    if (next === location.pathname + location.search && current) return;
    history[replace ? 'replaceState' : 'pushState']({}, '', next);
    route();
  }
  const makeCtx = (params, query, spaceId) => ({
    params, query,
    navigate: (p) => navigate(p),
    replace: (p) => navigate(p, { replace: true }),
    api, ui, links,
    state: stateFor(spaceId),
    shortcuts: { register: (map) => { for (const [k, fn] of Object.entries(map || {})) spaceShortcuts.set(k, fn); } },
  });

  function setContext(space, params) {
    $('context-title').textContent = space.title;
    $('context-sub').textContent = params && params.id ? `Replay ${params.id}` : '';
    $('titlebar-space').textContent = space.title;
    document.title = `${space.title} · ValoHub`;
  }
  function markRail(spaceId) {
    const active = RAIL_OF[spaceId];
    document.querySelectorAll('#rail .rail__item').forEach((a) => {
      const on = a.dataset.route === active;
      if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
      a.tabIndex = on ? 0 : -1;
    });
    if (!active) document.querySelector('#rail .rail__item')?.setAttribute('tabindex', '0');
  }

  async function unmountCurrent() {
    if (!current) return;
    const prev = current; current = null;
    try { prev.space.unmount && prev.space.unmount(); } catch (err) { console.error('unmount failed', err); }
    spaceShortcuts = new Map();
  }

  async function route() {
    const mySeq = ++seq;
    await unmountCurrent();
    let match = matchRoute(location.pathname);
    if (!match) {
      if (location.pathname === '/' || location.pathname === '' || /app\.html$/.test(location.pathname)) {
        const last = lsGet('vp.lastSpace', 'library');
        history.replaceState({}, '', last && last !== 'replay' && last !== 'analysis' && last !== 'coach' ? `/${last === 'library' ? 'replays' : last}` : '/replays');
        match = matchRoute(location.pathname);
      } else {
        showNotFound(location.pathname);
        return;
      }
    }
    const space = spaces.get(match.space);
    if (!space) { showNotFound(location.pathname); return; }
    document.body.dataset.space = space.id;
    root.dataset.space = space.id;
    setContext(space, match.params);
    markRail(space.id);
    lsSet('vp.lastSpace', space.id);
    mainEl.replaceChildren();
    skeleton(mainEl, 5);
    const ctx = makeCtx(match.params, new URLSearchParams(location.search), space.id);
    const entry = { space, ctx, seq: mySeq };
    current = entry;
    await mountInto(entry, mySeq);
  }

  async function mountInto(entry, mySeq) {
    const { space, ctx } = entry;
    try {
      // Focus is only claimed when the old content owned it (or nothing did). An async mount
      // must never steal focus from a rail link, a dialog, or a control an overlay restored
      // it to -- the palette's Escape close is exactly that case.
      const active = document.activeElement;
      const focusWasHere = !active || active === document.body || active === mainEl
        || mainEl.contains(active);
      mainEl.replaceChildren();
      await space.mount(mainEl, ctx);
      if (mySeq !== seq) return;
      announce(`${space.title} loaded`);
      if (focusWasHere && !overlayStack.length) mainEl.focus({ preventScroll: true });
    } catch (err) {
      if (mySeq !== seq) return;
      console.error(`space ${space.id} failed to mount`, err);
      try { space.unmount && space.unmount(); } catch { /* ignore */ }
      const msg = err instanceof ApiError ? err.message : 'This view could not be loaded.';
      const retry = el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'space-retry', onclick: async () => {
        skeleton(mainEl, 5);
        spaceShortcuts = new Map();
        await mountInto(entry, mySeq);
      } }, 'Retry');
      mainEl.replaceChildren(el('div', { class: 'empty', 'data-testid': 'space-error', role: 'alert' },
        el('div', { class: 'empty__art', 'aria-hidden': 'true' }, '!'),
        el('h2', { class: 'empty__title' }, `${space.title} failed to load`),
        el('p', { class: 'empty__body' }, msg), retry));
      announce(`${space.title} failed to load`);
    }
  }

  function showNotFound(path) {
    document.body.dataset.space = '';
    $('context-title').textContent = 'Not found';
    $('context-sub').textContent = '';
    $('titlebar-space').textContent = '';
    markRail(null);
    empty(mainEl, { icon: '?', title: 'Page not found', body: `No view exists at ${path}.`, actionLabel: 'Go to Library', onAction: () => navigate('/replays') });
    announce('Page not found');
  }

  // ---------- command palette ----------
  async function loadReplays() {
    if (replayCache) return replayCache;
    try {
      const data = await api.get('/api/replays');
      const list = Array.isArray(data) ? data : (data && (data.replays || data.items)) || [];
      replayCache = list.filter((r) => r && (r.id || r.replay_id)).map((r) => ({
        id: String(r.id ?? r.replay_id),
        name: String(r.name ?? r.title ?? r.file_name ?? r.id ?? r.replay_id),
      }));
    } catch { replayCache = []; }
    return replayCache;
  }
  function baseCommands() {
    return [
      { id: 'nav-library', label: 'Go to Library', hint: 'Navigate', run: () => navigate('/replays') },
      { id: 'nav-analysis', label: 'Go to Analysis', hint: 'Navigate', run: () => navigate('/analysis') },
      { id: 'nav-coach', label: 'Go to Coach', hint: 'Navigate', run: () => navigate('/coach') },
      { id: 'nav-providers', label: 'Open AI Providers', hint: 'Navigate', run: () => navigate('/providers') },
      { id: 'nav-settings', label: 'Open Settings', hint: 'Ctrl+,', run: () => navigate('/settings') },
      { id: 'toggle-rail', label: 'Toggle sidebar', hint: 'Ctrl+B', run: () => setRail(!railOpen()) },
      { id: 'density-compact', label: 'Density: compact', hint: 'Appearance', run: () => setDensity('compact') },
      { id: 'density-comfortable', label: 'Density: comfortable', hint: 'Appearance', run: () => setDensity('comfortable') },
    ];
  }
  async function openPalette() {
    if (overlayStack.some((o) => o.scrim.dataset.testid === 'palette-scrim')) return;
    const input = el('input', { class: 'palette__input', type: 'text', placeholder: 'Type a command or replay name…', 'aria-label': 'Command palette search', role: 'combobox', 'aria-expanded': 'true', 'aria-controls': 'palette-list', autocomplete: 'off', 'data-autofocus': '' });
    const list = el('ul', { class: 'palette__list', id: 'palette-list', role: 'listbox', 'aria-label': 'Commands' });
    const box = el('div', { class: 'palette', 'data-testid': 'palette' }, input, list);
    let items = baseCommands();
    let shown = []; let sel = 0;
    const entry = openOverlay(box, { label: 'Command palette', top: true, testid: 'palette-scrim', opener: $('palette-trigger') });
    entry.scrim.dataset.testid = 'palette-scrim';
    const render = () => {
      const q = input.value.trim().toLowerCase();
      shown = items.filter((c) => !q || c.label.toLowerCase().includes(q)).slice(0, 30);
      if (sel >= shown.length) sel = Math.max(0, shown.length - 1);
      list.replaceChildren(...(shown.length ? shown.map((c, i) => {
        const li = el('li', { class: 'palette__item', role: 'option', id: `palette-opt-${i}`, 'aria-selected': i === sel ? 'true' : 'false' },
          el('span', null, c.label), el('span', { class: 'palette__hint' }, c.hint || ''));
        li.addEventListener('mousedown', (e) => { e.preventDefault(); run(c); });
        return li;
      }) : [el('li', { class: 'palette__item', role: 'option', 'aria-selected': 'false' }, el('span', { class: 'palette__hint' }, 'No matches'))]));
      input.setAttribute('aria-activedescendant', shown.length ? `palette-opt-${sel}` : '');
      list.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: 'nearest' });
    };
    const run = (c) => { entry.close(); Promise.resolve().then(() => c.run()); };
    input.addEventListener('input', () => { sel = 0; render(); });
    input.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowDown') { e.preventDefault(); sel = Math.min(sel + 1, shown.length - 1); render(); }
      else if (e.key === 'ArrowUp') { e.preventDefault(); sel = Math.max(sel - 1, 0); render(); }
      else if (e.key === 'Enter') { e.preventDefault(); if (shown[sel]) run(shown[sel]); }
    });
    render();
    loadReplays().then((rs) => {
      if (!entry.scrim.isConnected) return;
      items = [...baseCommands(), ...rs.map((r) => ({ id: `open-${r.id}`, label: `Open replay: ${r.name}`, hint: 'Replay', run: () => navigate(`/replay/${encodeURIComponent(r.id)}`) }))];
      render();
    });
  }

  // ---------- chrome wiring ----------
  function wireChrome() {
    $('palette-trigger').addEventListener('click', openPalette);
    $('status-hint').addEventListener('click', openPalette);
    $('provider-chip').addEventListener('click', () => navigate('/providers'));
    $('status-provider').addEventListener('click', () => navigate('/providers'));
    $('status-parser').addEventListener('click', () => navigate('/settings?section=parser'));
    $('status-build').addEventListener('click', () => navigate('/settings?section=about'));
    $('status-job').addEventListener('click', () => toast(status.job && status.job.text ? String(status.job.text) : 'No active job', 'info'));

    const rail = $('rail');
    rail.addEventListener('click', (e) => {
      const a = e.target.closest('a[href]');
      if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
      e.preventDefault();
      navigate(a.getAttribute('href'));
    });
    rail.addEventListener('keydown', (e) => {
      const items = [...rail.querySelectorAll('.rail__item')];
      const i = items.indexOf(document.activeElement);
      if (i < 0) return;
      let n = null;
      if (e.key === 'ArrowDown') n = (i + 1) % items.length;
      else if (e.key === 'ArrowUp') n = (i - 1 + items.length) % items.length;
      else if (e.key === 'Home') n = 0;
      else if (e.key === 'End') n = items.length - 1;
      if (n == null) return;
      e.preventDefault();
      items.forEach((a, j) => { a.tabIndex = j === n ? 0 : -1; });
      items[n].focus();
    });

    // plain internal links inside spaces also route without reload
    mainEl.addEventListener('click', (e) => {
      const a = e.target.closest && e.target.closest('a[href]');
      if (!a || e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || a.target) return;
      const href = a.getAttribute('href');
      if (!href || !href.startsWith('/') || href.startsWith('//')) return;
      if (!matchRoute(new URL(href, location.origin).pathname)) return;
      e.preventDefault();
      navigate(href);
    });

    window.addEventListener('popstate', route);
    window.addEventListener('unhandledrejection', (e) => {
      const r = e.reason;
      if (r && r.name === 'AbortError') return;
      if (r instanceof ApiError) {
        const rid = r.requestId ? ` · ${r.requestId}` : '';
        toast(`${r.message} (${r.code}${rid})`, 'error');
      } else {
        toast('Something went wrong. See the log for details.', 'error');
      }
    });
    window.addEventListener('keydown', onKey);
    reduceMq.addEventListener?.('change', applyMotion);

    globalShortcuts.set('Ctrl+K', () => { openPalette(); return true; });
    globalShortcuts.set('Ctrl+P', () => { openPalette(); return true; });
    globalShortcuts.set('Ctrl+,', () => { navigate('/settings'); return true; });
    globalShortcuts.set('Ctrl+B', () => { setRail(!railOpen()); return true; });
  }

  function onKey(e) {
    if (e.defaultPrevented) return;
    if (overlayStack.length) return; // overlays own the keyboard
    const name = keyName(e);
    const g = globalShortcuts.get(name);
    if (g && (!isTyping(e.target) || e.ctrlKey || e.metaKey)) { if (g(e) !== false) e.preventDefault(); return; }
    if (isTyping(e.target) && !(e.ctrlKey || e.metaKey)) return;
    const s = spaceShortcuts.get(name);
    if (s) { if (s(e) !== false) e.preventDefault(); return; }
    if (current && current.space.onKey) {
      let handled = false;
      try { handled = current.space.onKey(e); } catch (err) { console.error(err); }
      if (handled) { e.preventDefault(); return; }
    }
    if (name === 'Escape' && current && current.space.id === 'settings') { e.preventDefault(); navigate('/replays'); }
  }

  async function probeBackend() {
    const dot = $('backend-dot'), text = $('backend-text');
    try {
      const h = await api.get('/api/app/health');
      dot.className = 'dot dot--ok';
      text.textContent = h && h.version ? `Backend ok · v${h.version}` : 'Backend ok';
      setStatus({ build: { text: h && h.version ? `v${h.version}` : 'dev' } });
      $('rail-library').title = '';
    } catch {
      dot.className = 'dot dot--error';
      text.textContent = 'Backend unreachable';
    }
    try {
      const d = await api.get('/api/decoder');
      const ok = d && (d.ready ?? d.installed ?? d.available);
      setStatus({ parser: { text: ok === false ? 'not ready' : 'ready', level: ok === false ? 'warn' : 'ok' } });
    } catch {
      setStatus({ parser: { text: 'unavailable', level: 'error' } });
    }
  }

  function start() {
    applyMotion();
    root.dataset.density = lsGet('vp.density', 'comfortable');
    document.body.dataset.rail = lsGet('vp.rail.expanded', false) ? 'open' : 'closed';
    wireChrome();
    setStatus({ provider: { text: 'none', chip: 'No model' } });
    route();
    probeBackend();
  }

  return {
    start, navigate, route, api, ui, el, openPalette,
    registerShortcut: (k, fn) => globalShortcuts.set(k, fn),
  };
}

export default createShell;
