// AI Providers space (DESIGN-V2 §4.5): two groups, per-row state, verify-then-save connect sheet,
// cloud consent sheet, OAuth poll, model picker.
// DOM is built with el() only (no innerHTML). A key is read from its input once; it is never stored,
// logged, put in a URL/DOM attribute or echoed back by us. Only {configured,last4,models} come back
// from the server, so the masked hint is always the server's last4.
import { el } from '../shell.js';

const API = '/api/ai/providers';
const HEALTH = '/api/app/health';
const DEFAULT_API = '/api/ai/default';
const SLOW_MS = 3000;
const GIVE_UP_MS = 8000;
const HEALTH_POLL_MS = 5000;
const CACHE_KEY = 'model-cache';

// Module-local state: one instance of this space is mounted at a time; unmount() clears all of it.
const S = {
  root: null,
  ctx: null,
  disposed: true,
  providers: [],
  default: null,
  offlineDefault: true,
  error: null,          // {kind:'missing'|'down', message:{head,meta}}
  lastLoaded: 0,
  busy: new Set(),
  timers: new Set(),
  intervals: new Set(),
  healthPoll: null,
  modal: null,
  cache: null,
};

// ------------------------------------------------------------------ helpers

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
  S.healthPoll = null;
}
function words(v) { return typeof v === 'string' ? v : ''; }
function num(v) { return Number.isFinite(v) ? v : null; }
function fmtNum(n) { return typeof n === 'number' ? n.toLocaleString('en-US') : ''; }
function hostOf(url) {
  if (typeof url !== 'string' || !url) return '';
  try { return new URL(url).host; } catch { return ''; }
}
function modelsOf(view) { return Array.isArray(view && view.models) ? view.models : []; }
function modelCount(view) {
  const n = num(view && view.model_count);
  return n !== null ? n : modelsOf(view).length;
}

// The shell never hands spaces a `put` (SHELL-CONTRACT lists ctx.api.put; shell.js ships get/post/del/stream).
// PUT /api/ai/default is the backend route for the default model, so this one call uses fetch() with the
// same same-origin + error-envelope handling as ctx.api.
async function putJson(path, body) {
  let res;
  try {
    res = await fetch(path, {
      method: 'PUT', credentials: 'same-origin',
      headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    throw Object.assign(new Error('Backend unreachable'), { status: 0, code: 'network' });
  }
  const raw = await res.text();
  let data = null;
  if (raw) { try { data = JSON.parse(raw); } catch { data = null; } }
  if (!res.ok) {
    const d = data && typeof data === 'object' ? data : {};
    const err = d.error && typeof d.error === 'object' ? d.error : d;
    throw Object.assign(new Error(err.message || `HTTP ${res.status}`), {
      status: res.status,
      code: err.code || (typeof d.error === 'string' ? d.error : `http_${res.status}`),
      requestId: err.request_id || res.headers.get('x-request-id') || null,
    });
  }
  return data;
}

const ERROR_TEXT = {
  invalid_key: 'Invalid key (401) — the provider rejected this key.',
  forbidden: 'Forbidden (403) — this key lacks access (permissions or budget).',
  rate_limited: 'Rate limited (429) — the provider is throttling this key; try again shortly.',
  unreachable: 'Unreachable — the provider did not answer. Check the network or the provider status page.',
  model_gone: 'That model is not available from this provider.',
  consent_required: 'This provider needs your consent before it can be used.',
  reauth_required: 'The stored credential is no longer valid — connect this provider again.',
  not_configured: 'This provider is not connected yet.',
  secrets_unavailable: 'Secure credential storage is unavailable, so the key was not saved.',
  no_key_required: 'This provider does not use an API key.',
  oauth_unsupported: 'This provider does not offer OAuth sign-in.',
  unknown_provider: 'Unknown provider.',
  invalid_request: 'The request was rejected — check the key format and the fields.',
  upstream_error: 'The provider returned an unexpected response.',
  flow_not_found: 'No sign-in flow is running. Start the sign-in again.',
  network: 'The backend is not responding.',
  timeout: 'The provider did not answer within 8 seconds.',
  http_404: 'This endpoint is not available in this build.',
};

function explain(err) {
  const code = words(err && err.code) || 'error';
  const head = ERROR_TEXT[code] || words(err && err.message) || 'The request failed.';
  const meta = [
    code !== 'error' ? code : null,
    err && err.status ? `HTTP ${err.status}` : null,
    err && err.requestId ? `request ${err.requestId}` : null,
  ].filter(Boolean).join(' · ');
  return { head, meta };
}

// ------------------------------------------------------------------ row state

function providerState(view) {
  if (view.kind === 'local') return localState(view);
  const state = words(view.state) || 'not_connected';
  const apiText = words(view.state_text);
  const n = modelCount(view);
  switch (state) {
    case 'connected':
      return n > 0
        ? { level: 'ok', glyph: '✓', word: `Connected · ${n} model${n === 1 ? '' : 's'}`, note: '' }
        : { level: 'warn', glyph: '!', word: 'Connected · no models',
            note: 'No models for this key — check the account in the provider console.' };
    case 'not_connected':
      return view.configured
        ? { level: 'idle', glyph: '○', word: 'Not connected', note: 'A credential is stored but not verified yet — press Test.' }
        : { level: 'idle', glyph: '○', word: 'Needs key',
            note: view.oauth_supported ? 'Sign in with OAuth or paste an API key.' : 'Paste an API key to connect.' };
    case 'invalid_key':
      return { level: 'error', glyph: '!', word: apiText || 'Invalid key (401)', note: 'Replace the key to reconnect.' };
    case 'forbidden':
      return { level: 'error', glyph: '!', word: apiText || 'Forbidden (403)',
               note: 'This key has no access — check its permissions or the account budget.' };
    case 'rate_limited':
      return { level: 'warn', glyph: '!', word: apiText || 'Rate limited (429)', note: 'Throttled by the provider; try again shortly.' };
    case 'unreachable':
      return { level: 'error', glyph: '!', word: apiText || 'Unreachable', note: 'The provider did not answer.' };
    case 'model_gone':
      return { level: 'warn', glyph: '!', word: apiText || 'Model unavailable', note: 'Pick a different model.' };
    case 'reauth_required':
      return { level: 'warn', glyph: '!', word: apiText || 'Sign in again', note: 'The stored credential was rejected.' };
    case 'consent_required':
      return { level: 'warn', glyph: '!', word: 'Needs consent', note: 'Enable this provider before any cloud request.' };
    case 'upstream_error': {
      const m = /\((\d{3})\)/.exec(apiText);
      const status = m ? Number(m[1]) : null;
      if (status === 402) {
        return { level: 'error', glyph: '!', word: 'Credits required (402)',
                 note: 'The account is out of credit or over budget — check billing with the provider.' };
      }
      if (status === 403) {
        return { level: 'error', glyph: '!', word: 'Forbidden (403)',
                 note: 'The provider refused this request — check the key permissions or budget.' };
      }
      return { level: 'error', glyph: '!', word: apiText || 'Unexpected response',
               note: status ? `The provider answered ${status}.` : '' };
    }
    default:
      return { level: 'warn', glyph: '!', word: apiText || state, note: '' };
  }
}

function localState(view) {
  const eps = Array.isArray(view.endpoints) ? view.endpoints : [];
  const live = eps.filter((e) => e && e.reachable === true);
  if (live.length) {
    const n = live.reduce((sum, e) => sum + (num(e.model_count) || 0), 0);
    return {
      level: 'ok', glyph: '✓', word: `Connected · ${n} model${n === 1 ? '' : 's'}`,
      note: `${live.length} of ${eps.length} local server${eps.length === 1 ? '' : 's'} answered.`,
    };
  }
  if (eps.some((e) => e && e.reachable === false)) {
    return { level: 'error', glyph: '!', word: words(view.state_text) || 'Unreachable',
             note: 'Start llama.cpp, LM Studio or Ollama, then Refresh.' };
  }
  return { level: 'warn', glyph: '…', word: 'Checking…', note: '' };
}

function endpointState(ep) {
  if (ep.blocked === 'cloud_host') {
    return { level: 'warn', glyph: '!', word: 'Not contacted (cloud host)',
             note: 'A non-loopback host is never probed as a local server.' };
  }
  if (ep.reachable === true) {
    const n = num(ep.model_count) || 0;
    return { level: 'ok', glyph: '✓', word: n ? `Connected · ${n} model${n === 1 ? '' : 's'}` : 'Connected · no models', note: '' };
  }
  if (ep.reachable === false) {
    return { level: 'error', glyph: '!', word: 'Unreachable',
             note: ep.code ? `Last error: ${words(ep.code)}` : 'No server answered on this host.' };
  }
  return { level: 'warn', glyph: '…', word: 'Checking…', note: '' };
}

// ------------------------------------------------------------------ data

function providerById(id) {
  return S.providers.find((v) => v && v.id === id) || null;
}

function mergeProvider(view) {
  if (!view || !view.id) return;
  const i = S.providers.findIndex((v) => v && v.id === view.id);
  if (i >= 0) S.providers[i] = { ...S.providers[i], ...view };
  else S.providers.push(view);
}

function saveCache() {
  if (!S.ctx) return;
  const providers = S.providers.map((v) => ({
    id: v.id,
    models: modelsOf(v).slice(0, 300).map((m) => ({ id: m.id, context: num(m.context), endpoint: m.endpoint || null })),
  }));
  // model names and context lengths only — never a credential (SHELL-CONTRACT: localStorage is UI-only)
  S.ctx.state.set(CACHE_KEY, { at: Date.now(), providers });
}

function cachedFor(id) {
  const cache = S.cache;
  if (!cache || !Array.isArray(cache.providers)) return [];
  const hit = cache.providers.find((p) => p && p.id === id);
  return hit && Array.isArray(hit.models) ? hit.models : [];
}

async function reload({ probe = true, keepLocal = true } = {}) {
  const previousLocal = providerById('local');
  try {
    const data = await S.ctx.api.get(`${API}?probe=${probe ? 1 : 0}`);
    const list = Array.isArray(data && data.providers) ? data.providers.filter((v) => v && typeof v.id === 'string') : [];
    if (keepLocal) {
      const next = list.find((v) => v.id === 'local');
      if (previousLocal && next && !(Array.isArray(next.endpoints) && next.endpoints.length)) {
        const carried = Array.isArray(previousLocal.endpoints) ? previousLocal.endpoints : [];
        next.endpoints = carried;
        if (!modelsOf(next).length) next.models = previousLocal.models || [];
        const live = carried.filter((e) => e && e.reachable === true);
        next.state = live.length ? 'connected' : 'unreachable';
        next.state_text = live.length ? 'Connected' : 'No local model server answered';
      }
    }
    S.providers = list;
    S.default = data && data.default && typeof data.default === 'object' ? data.default : null;
    S.offlineDefault = !data || data.offline_default !== false;
    S.error = null;
    S.lastLoaded = Date.now();
    saveCache();
  } catch (err) {
    const kind = err && err.status === 404 ? 'missing' : 'down';
    S.error = { kind, message: explain(err) };
    if (kind === 'down') waitForBackend();
  }
  render();
  publishStatus();
}

function waitForBackend() {
  if (S.healthPoll) return;
  S.healthPoll = every(async () => {
    try { await S.ctx.api.get(HEALTH); } catch { return; }
    if (S.healthPoll) { clearInterval(S.healthPoll); S.intervals.delete(S.healthPoll); S.healthPoll = null; }
    S.error = null;
    await reload({ probe: true });
  }, HEALTH_POLL_MS);
}

// ------------------------------------------------------------------ actions

function bestModel(view) {
  if (!view) return null;
  if (words(view.default_model)) return view.default_model;
  const models = modelsOf(view);
  return models.length ? models[0].id : null;
}

async function setDefault(providerId, model) {
  if (!S.ctx) return;
  try {
    const body = { provider: providerId };
    if (model) body.model = model;
    const res = await putJson(DEFAULT_API, body);
    S.default = res && res.default ? res.default : body;
    for (const v of S.providers) v.default = !!(S.default && S.default.provider === v.id);
    S.ctx.ui.toast(`Default model: ${model || providerId}`, 'success');
    render();
    publishStatus();
  } catch (err) {
    S.ctx.ui.toast(explain(err).head, 'error');
  }
}

function replaceStateText(id, st) {
  const host = S.root && S.root.querySelector(`[data-testid="provider-state-${id}"]`);
  if (host) host.replaceChildren(glyphEl(st), el('span', { class: 'provider-state__word' }, st.word));
}

async function testProvider(view, endpoint) {
  if (!S.ctx || S.busy.has(view.id)) return;
  const id = view.id;
  const pool = endpoint ? modelsOf(view).filter((m) => m && m.endpoint === endpoint.id) : modelsOf(view);
  const body = pool.length ? { model: pool[0].id } : {};
  S.busy.add(id);
  replaceStateText(id, { level: 'warn', glyph: '…', word: 'Checking…' });
  const slow = later(() => {
    if (S.busy.has(id)) replaceStateText(id, { level: 'warn', glyph: '…', word: 'Checking… (slow)' });
  }, SLOW_MS);
  try {
    const res = await S.ctx.api.post(`${API}/${encodeURIComponent(id)}/test`, body);
    const ms = num(res && res.latency_ms);
    S.ctx.ui.toast(
      `${view.label} answered${res && res.model ? ` · ${res.model}` : ''}${ms !== null ? ` · ${ms} ms` : ''}`,
      'success');
    clearTimeout(slow); S.timers.delete(slow);
    S.busy.delete(id);
    await reload({ probe: false, keepLocal: true });
  } catch (err) {
    const code = words(err && err.code) || 'upstream_error';
    const e = explain(err);
    mergeProvider({ id, state: code, state_text: e.head.split(' — ')[0] });
    S.ctx.ui.toast(e.head, 'error');
  } finally {
    clearTimeout(slow); S.timers.delete(slow);
    S.busy.delete(id);
    render();
  }
}

async function disconnect(view) {
  if (!S.ctx) return;
  const ok = await S.ctx.ui.confirm({
    title: `Disconnect ${view.label}?`,
    body: `The stored credential is deleted from this PC${view.oauth_supported ? ' and the token is revoked where the provider supports it' : ''}. Coach falls back to a local model.`,
    confirmLabel: 'Disconnect',
    danger: true,
  });
  if (!ok) return;
  try {
    await S.ctx.api.del(`${API}/${encodeURIComponent(view.id)}`);
    mergeProvider({
      id: view.id, configured: false, last4: '', models: [], model_count: 0, credential: null,
      state: 'not_connected', state_text: 'Not connected', consented: false,
    });
    S.ctx.ui.toast(`${view.label} disconnected`, 'success');
    render();
    publishStatus();
    await reload({ probe: false, keepLocal: true });
  } catch (err) {
    S.ctx.ui.toast(explain(err).head, 'error');
  }
}

// ------------------------------------------------------------------ connect / consent sheets

function providerHost(view) {
  if (!view) return '';
  if (words(view.host)) return view.host;
  if (words(view.base_url)) return hostOf(view.base_url);
  return '';
}

function openForProvider(view) {
  if (!S.ctx) return;
  if (view.kind !== 'local' && view.consented === false) {
    openConsent(view, () => openConnect(view));
    return;
  }
  openConnect(view);
}

function showError(box, err) {
  const e = explain(err);
  box.hidden = false;
  box.replaceChildren(
    el('strong', { class: 'sheet__error-head' }, e.head),
    e.meta ? el('span', { class: 'sheet__codemeta' }, e.meta) : null);
}

function openConsent(view, next) {
  const host = providerHost(view) || view.label;
  const errBox = el('p', { class: 'sheet__error', role: 'alert', hidden: true });
  const enable = el('button', {
    type: 'button', class: 'btn btn--primary', 'data-testid': 'consent-enable',
  }, `Enable ${view.label}`);
  const cancel = el('button', {
    type: 'button', class: 'btn', 'data-testid': 'consent-cancel',
  }, 'Cancel');
  const content = el('div', { class: 'sheet', 'data-testid': 'consent-sheet' },
    el('h2', { class: 'modal__title' }, `Enable ${view.label}?`),
    el('p', { class: 'sheet__lead' },
      `Coach will send a request from this PC to ${host}. Nothing leaves this device until you enable it.`),
    el('dl', { class: 'sheet__facts' },
      el('div', { class: 'sheet__fact' },
        el('dt', null, 'Sent'),
        el('dd', null, 'The replay summary (player names replaced) and your coach instructions, plus the model request.')),
      el('div', { class: 'sheet__fact' },
        el('dt', null, 'Stays on this device'),
        el('dd', null, 'The replay file (.vrf) and raw event data. The replay file stays on this device.')),
      el('div', { class: 'sheet__fact' },
        el('dt', null, 'Recorded'),
        el('dd', null, `Consent is recorded by the app for ${host} only. Your key stays in the app's protected storage and is never stored by the page.`))),
    words(view.data_note) ? el('p', { class: 'sheet__hint' }, view.data_note) : null,
    errBox,
    el('div', { class: 'modal__actions' }, cancel, enable));
  const entry = S.ctx.ui.openModal(content, { label: `Enable ${view.label}` });
  S.modal = entry;
  enable.addEventListener('click', async () => {
    if (enable.disabled) return;
    enable.disabled = true;
    enable.textContent = 'Enabling…';
    try {
      await S.ctx.api.post(`${API}/${encodeURIComponent(view.id)}/consent`, { accepted: true });
      if (!content.isConnected) return;
      entry.close();
      S.ctx.ui.toast(`${view.label} enabled for this PC`, 'success');
      next();
    } catch (err) {
      enable.disabled = false;
      enable.textContent = `Enable ${view.label}`;
      showError(errBox, err);
    }
  });
  cancel.addEventListener('click', () => entry.close());
}

function openConnect(view) {
  const pid = view.id;
  const host = providerHost(view);
  const errBox = el('p', { class: 'sheet__error', role: 'alert', hidden: true, 'data-testid': 'connect-error' });
  const okBox = el('p', { class: 'sheet__ok', role: 'status', hidden: true, 'data-testid': 'connect-success' });

  const input = el('input', {
    class: 'field sheet__input', type: 'password', id: `connect-key-${pid}`,
    'data-testid': 'connect-key', autocomplete: 'off', autocapitalize: 'off', spellcheck: 'false',
    placeholder: view.configured ? 'Paste a replacement key' : 'Paste your API key',
    'aria-describedby': `connect-key-help-${pid}`,
  });
  const baseInput = view.kind === 'custom'
    ? el('input', {
      class: 'field sheet__input', type: 'text', id: `connect-base-${pid}`,
      'data-testid': 'connect-base-url', placeholder: 'http://127.0.0.1:8000/v1',
      value: words(view.base_url), autocomplete: 'off', spellcheck: 'false',
    })
    : null;
  const verify = el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'connect-verify' }, 'Verify & save');
  const cancel = el('button', { type: 'button', class: 'btn', 'data-testid': 'connect-cancel' }, 'Cancel');
  const oauthBtn = view.oauth_supported
    ? el('button', { type: 'button', class: 'btn btn--ghost', 'data-testid': 'connect-oauth' }, `Connect with ${view.label}`)
    : null;
  const oauthStatus = el('div', { class: 'sheet__waiting', hidden: true, role: 'status' });
  const oauthOpen = el('a', {
    class: 'btn btn--ghost', 'data-testid': 'connect-oauth-open', target: '_blank',
    rel: 'noopener noreferrer', hidden: true,
  }, 'Open browser again');

  let oauthState = '';
  let oauthDeadline = 0;
  let oauthTick = null;
  let oauthPoll = null;

  const live = () => !S.disposed && content.isConnected;

  function stopOauth() {
    if (oauthTick) { clearInterval(oauthTick); S.intervals.delete(oauthTick); oauthTick = null; }
    if (oauthPoll) { clearInterval(oauthPoll); S.intervals.delete(oauthPoll); oauthPoll = null; }
  }

  function waitingNode(extra) {
    const left = Math.max(0, Math.round((oauthDeadline - Date.now()) / 1000));
    const line = el('span', { class: 'sheet__waiting-text' },
      `Waiting for authorization… ${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left`);
    return extra ? el('div', null, line, el('span', { class: 'sheet__hint' }, extra)) : line;
  }

  async function pollOauth() {
    if (!oauthState) return;
    try {
      const st = await S.ctx.api.get(`/api/ai/oauth/callback?state=${encodeURIComponent(oauthState)}`);
      if (!live()) { stopOauth(); return; }
      const status = words(st && st.status);
      if (status === 'pending') return;
      stopOauth();
      oauthOpen.hidden = true;
      if (status === 'connected') {
        oauthStatus.hidden = false;
        oauthStatus.replaceChildren(el('span', { class: 'sheet__ok' }, `Connected · ${view.label} authorized`));
        S.ctx.ui.toast(`${view.label} connected`, 'success');
        await reload({ probe: false, keepLocal: true });
        later(() => { if (entry.scrim && entry.scrim.isConnected) entry.close(); }, 900);
        return;
      }
      const code = words(st && st.code);
      if (code === 'access_denied') {
        oauthStatus.hidden = false;
        oauthStatus.replaceChildren(waitingNode('Sign-in cancelled in the browser. Nothing was saved.'));
        if (oauthBtn) oauthBtn.disabled = false;
        return;
      }
      if (status === 'expired' || code === 'expired') {
        oauthStatus.hidden = false;
        oauthStatus.replaceChildren(waitingNode('The sign-in window expired. Start again or use a key.'));
        if (oauthBtn) oauthBtn.disabled = false;
        return;
      }
      showError(errBox, { code: code || status || 'upstream_error' });
      if (oauthBtn) oauthBtn.disabled = false;
    } catch (err) {
      if (!live()) { stopOauth(); return; }
      if (err && err.status === 404) {
        stopOauth();
        showError(errBox, { code: 'flow_not_found' });
        if (oauthBtn) oauthBtn.disabled = false;
      }
      // transient failures keep polling; the flow expires server-side
    }
  }

  async function startOauth() {
    if (!oauthBtn || oauthBtn.disabled) return;
    oauthBtn.disabled = true;
    errBox.hidden = true;
    oauthStatus.hidden = false;
    oauthStatus.replaceChildren(el('span', { class: 'sheet__hint' }, 'Opening your browser…'));
    try {
      const started = await S.ctx.api.post(`${API}/${encodeURIComponent(pid)}/oauth/start`, { consent: true });
      if (!live()) return;
      oauthState = words(started && started.state);
      oauthDeadline = Date.now() + (num(started && started.expires_in) || 300) * 1000;
      const url = words(started && started.authorize_url);
      if (url) { oauthOpen.href = url; oauthOpen.hidden = false; }
      const note = started && started.browser_opened === false
        ? 'The browser did not open automatically — use "Open browser again".'
        : 'Finish the sign-in in your browser; this sheet updates when it completes.';
      oauthStatus.replaceChildren(waitingNode(note));
      oauthTick = every(() => {
        if (!oauthStatus.isConnected || Date.now() > oauthDeadline) { stopOauth(); return; }
        const node = oauthStatus.querySelector('.sheet__waiting-text');
        if (node) node.textContent = waitingNode('').textContent;
      }, 1000);
      oauthPoll = every(pollOauth, 1500);
      pollOauth();
    } catch (err) {
      if (!live()) return;
      showError(errBox, err);
      oauthStatus.hidden = true;
      oauthBtn.disabled = false;
    }
  }
  if (oauthBtn) oauthBtn.addEventListener('click', startOauth);

  let abandoned = false;
  function restore() {
    verify.disabled = false;
    verify.textContent = 'Verify & save';
  }

  async function runVerify() {
    if (verify.disabled) return;
    const key = input.value;
    input.value = '';                    // read once; the key never stays in the DOM afterwards
    if (!key.trim() && view.kind !== 'custom') {
      showError(errBox, { code: 'key_required', message: 'Paste an API key first.' });
      return;
    }
    errBox.hidden = true;
    okBox.hidden = true;
    abandoned = false;
    verify.disabled = true;
    verify.textContent = 'Checking…';
    const slow = later(() => {
      if (verify.disabled) verify.textContent = 'Checking… (slow — Cancel to stop waiting)';
    }, SLOW_MS);
    const giveUp = later(() => {
      if (!verify.disabled) return;
      abandoned = true;
      restore();
      showError(errBox, { code: 'timeout' });
    }, GIVE_UP_MS);
    try {
      const body = { key: key.trim(), consent: true };
      if (view.kind === 'custom' && baseInput) body.base_url = baseInput.value.trim();
      const res = await S.ctx.api.post(`${API}/${encodeURIComponent(pid)}/key`, body);
      if (abandoned || !live()) return;
      const n = modelCount(res || {});
      okBox.hidden = false;
      okBox.textContent = n > 0 ? `Connected · ${n} model${n === 1 ? '' : 's'}` : 'Connected · no models';
      mergeProvider(res || { id: pid });
      S.ctx.ui.toast(`${view.label} connected`, 'success');
      render();
      publishStatus();
      later(() => { if (entry.scrim && entry.scrim.isConnected) entry.close(); }, 1200);
    } catch (err) {
      if (abandoned || !live()) return;
      showError(errBox, err);
    } finally {
      clearTimeout(slow); S.timers.delete(slow);
      clearTimeout(giveUp); S.timers.delete(giveUp);
      if (!abandoned) restore();
    }
  }
  verify.addEventListener('click', runVerify);

  const getKey = view.console_url
    ? el('button', {
      type: 'button', class: 'btn btn--ghost', 'data-testid': 'connect-get-key',
      title: 'Opens the provider console in your browser',
      onclick: async () => {
        try {
          await S.ctx.api.post(`${API}/${encodeURIComponent(pid)}/open-console`, {});
        } catch {
          if (!live()) return;
          errBox.hidden = false;
          errBox.replaceChildren(
            el('span', { class: 'sheet__hint' }, 'The system browser could not be opened automatically: '),
            el('a', { class: 'btn btn--ghost', href: view.console_url, target: '_blank', rel: 'noopener noreferrer' }, 'Open provider console'));
        }
      },
    }, 'Get key')
    : null;

  const content = el('div', { class: 'sheet', 'data-testid': 'connect-sheet' },
    el('h2', { class: 'modal__title' }, view.configured ? `Manage ${view.label}` : `Connect ${view.label}`),
    el('p', { class: 'sheet__lead' }, host ? `${view.label} at ${host}` : view.label),
    view.configured && words(view.last4)
      ? el('p', { class: 'sheet__hint' }, `Stored key ••••${view.last4}. Replacing it verifies the new key before anything is saved.`)
      : null,
    oauthBtn ? el('div', { class: 'sheet__oauth' },
      el('p', { class: 'sheet__label' }, 'Sign in'), oauthBtn, oauthOpen, oauthStatus) : null,
    oauthBtn ? el('p', { class: 'sheet__divider' }, 'or use an API key') : null,
    el('div', { class: 'sheet__field' },
      el('label', { class: 'sheet__label', for: `connect-key-${pid}` },
        view.kind === 'custom' ? 'API key (optional for a loopback server)' : 'API key'),
      input),
    el('p', { class: 'sheet__help', id: `connect-key-help-${pid}` },
      'Verify & save checks the key with the provider first; nothing is stored unless it passes.'),
    baseInput
      ? el('div', { class: 'sheet__field' },
        el('label', { class: 'sheet__label', for: `connect-base-${pid}` }, 'Base URL (loopback or private network only)'),
        baseInput)
      : null,
    okBox,
    errBox,
    el('div', { class: 'modal__actions' }, getKey, cancel, verify));

  cancel.addEventListener('click', () => { abandoned = true; stopOauth(); entry.close(); });
  const entry = S.ctx.ui.openModal(content, { label: `${view.configured ? 'Manage' : 'Connect'} ${view.label}` });
  S.modal = entry;
}

// ------------------------------------------------------------------ model picker

function openModelPicker(focusId) {
  if (!S.ctx) return;
  const search = el('input', {
    class: 'field model-picker__search', type: 'search', placeholder: 'Search models…',
    'aria-label': 'Search models', autocomplete: 'off', 'data-autofocus': '',
  });
  const listHost = el('div', { class: 'model-picker__groups' });
  const status = el('p', { class: 'sheet__hint', hidden: true, role: 'status' });
  const retry = el('button', { type: 'button', class: 'btn', 'data-testid': 'model-picker-retry' }, 'Retry');

  function paint() {
    const q = search.value.trim().toLowerCase();
    const groups = [];
    for (const view of S.providers) {
      const isLocal = view.kind === 'local';
      const models = modelsOf(view);
      if (!models.length) continue;
      const label = isLocal ? 'On this PC' : view.label;
      const rows = models.filter((m) => !q || String(m.id).toLowerCase().includes(q) || label.toLowerCase().includes(q));
      if (!rows.length) continue;
      groups.push({ view, label, isLocal, rows });
    }
    if (!groups.length) {
      listHost.replaceChildren(el('p', { class: 'providers__gap' },
        q ? 'No model matches that search.' : 'No model lists yet — connect a provider and press Retry.'));
      return;
    }
    listHost.replaceChildren(...groups.map(({ view, label, isLocal, rows }) => el('section', {
      class: 'model-picker__group', 'data-provider': view.id,
    },
    el('div', { class: 'model-picker__grouphead' },
      el('span', { class: 'model-picker__grouptitle' }, label),
      badgeEl(isLocal ? 'local' : 'cloud'),
      !isLocal && words(view.last4) ? el('span', { class: 'provider-row__keyhint' }, `key ••••${view.last4}`) : null),
    rows.map((m) => {
      const isDefault = !!(S.default && S.default.provider === view.id && S.default.model === m.id);
      return el('button', {
        type: 'button', class: 'model-picker__item', 'data-model': String(m.id),
        'aria-selected': isDefault ? 'true' : 'false',
      },
      el('span', { class: 'model-picker__id' }, String(m.id)),
      num(m.context) !== null ? el('span', { class: 'model-picker__ctx' }, `${fmtNum(num(m.context))} ctx`) : null,
      m.endpoint ? el('span', { class: 'providers__tag' }, String(m.endpoint)) : null,
      isDefault
        ? el('span', { class: 'providers__tag providers__tag--on' }, 'default')
        : el('span', { class: 'model-picker__use' }, 'Use as default'));
    }))));
  }

  async function refresh() {
    retry.disabled = true;
    retry.textContent = 'Refreshing…';
    const failures = [];
    await Promise.all(S.providers.map(async (view) => {
      try {
        const path = `${API}/${encodeURIComponent(view.id)}/models${view.kind === 'local' ? '' : '?refresh=1'}`;
        const data = await S.ctx.api.get(path);
        const models = Array.isArray(data && data.models) ? data.models : [];
        mergeProvider({ id: view.id, models, model_count: models.length });
      } catch (err) {
        failures.push({ view, err });
      }
    }));
    retry.disabled = false;
    retry.textContent = 'Retry';
    if (failures.length) {
      const first = failures[0];
      const cached = cachedFor(first.view.id);
      if (cached.length) mergeProvider({ id: first.view.id, models: cached.map((m) => ({ ...m })), model_count: cached.length });
      status.hidden = false;
      status.replaceChildren(el('span', { class: 'sheet__error-head' },
        `${explain(first.err).head} Showing the saved list${cached.length ? '' : ' (empty)'} — Retry when it is reachable.`));
    } else {
      status.hidden = true;
    }
    paint();
  }
  retry.addEventListener('click', refresh);

  const content = el('div', { class: 'sheet sheet--wide', 'data-testid': 'model-picker' },
    el('h2', { class: 'modal__title' }, 'Default model'),
    el('p', { class: 'sheet__lead' }, 'Every model carries a LOCAL or CLOUD badge and the provider it belongs to.'),
    el('div', { class: 'model-picker__bar' }, search, retry),
    status,
    listHost,
    el('div', { class: 'modal__actions' },
      el('button', { type: 'button', class: 'btn' }, 'Close')));

  const entry = S.ctx.ui.openModal(content, { label: 'Default model' });
  S.modal = entry;
  content.querySelector('.modal__actions .btn').addEventListener('click', () => entry.close());
  listHost.addEventListener('click', (e) => {
    const btn = e.target.closest ? e.target.closest('.model-picker__item') : null;
    if (!btn) return;
    const id = btn.getAttribute('data-model');
    const group = btn.closest('.model-picker__group');
    const providerId = group && group.getAttribute('data-provider');
    if (providerId && id) setDefault(providerId, id);
  });
  search.addEventListener('input', paint);
  paint();
  if (focusId) {
    const view = providerById(focusId);
    if (view && !modelsOf(view).length) refresh();
  }
}

// ------------------------------------------------------------------ render helpers

function glyphEl(st) {
  return el('span', { class: 'provider-state__glyph', 'aria-hidden': 'true' }, st.glyph);
}
function stateEl(id, st) {
  return el('span', {
    class: 'provider-state', 'data-testid': `provider-state-${id}`, 'data-level': st.level,
    role: 'status', 'aria-live': 'polite',
  }, glyphEl(st), el('span', { class: 'provider-state__word' }, st.word));
}
function badgeEl(kind) {
  const local = kind === 'local';
  return el('span', { class: `badge badge--${local ? 'local' : 'cloud'}` }, local ? 'LOCAL' : 'CLOUD');
}
function actionButton({ testid, label, cls = 'btn', title, disabled, reasonId, onclick }) {
  return el('button', {
    type: 'button', class: cls, 'data-testid': testid, title: title || null,
    disabled: !!disabled, 'aria-describedby': disabled && reasonId ? reasonId : null,
    onclick: disabled ? null : onclick,
  }, label);
}
function rowEl({ id, name, host, kind, st, meta, actions, children, extraClass = '' }) {
  return el('div', {
    class: `provider-row provider-row--v2 ${extraClass}`.trim(), 'data-testid': `provider-row-${id}`, 'data-provider': id,
  },
  el('div', { class: 'provider-row__head' },
    el('span', { class: 'provider-row__icon', 'aria-hidden': 'true' }, kind === 'local' ? '▤' : '☁'),
    el('span', { class: 'provider-row__name' }, name),
    badgeEl(kind),
    st ? stateEl(id, st) : null),
  el('div', { class: 'provider-row__meta' },
    host ? el('span', { class: 'provider-row__host' }, host) : null,
    meta ? el('span', { class: 'provider-row__keyhint' }, meta) : null),
  st && st.note ? el('p', { class: 'provider-row__note', id: `provider-note-${id}` }, st.note) : null,
  children || null,
  actions ? el('div', { class: 'provider-row__actions' }, actions) : null);
}
function errorCard(message, retry) {
  const missing = S.error && S.error.kind === 'missing';
  return el('div', { class: 'providers__fail', role: 'alert' },
    el('div', { class: 'providers__fail-art', 'aria-hidden': 'true' }, '!'),
    el('div', { class: 'providers__fail-body' },
      el('h3', { class: 'providers__fail-title' }, missing ? 'Backend not ready' : 'Backend not responding'),
      el('p', { class: 'providers__fail-text' },
        missing
          ? '/api/ai/providers is not served by this build, so provider state cannot be shown.'
          : `${message.head}${message.meta ? ` (${message.meta})` : ''} Retrying in the background.`),
      el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'providers-retry', onclick: retry }, 'Retry')));
}
function placeholder(text) {
  return el('p', { class: 'providers__gap' }, text);
}
function defaultLabel() {
  const d = S.default;
  if (!d || !d.provider) return 'No default model';
  const view = providerById(d.provider);
  return `${view ? view.label : d.provider}${d.model ? ` · ${d.model}` : ''}`;
}
function publishStatus() {
  if (!S.ctx) return;
  const d = S.default;
  const view = d && d.provider ? providerById(d.provider) : null;
  if (view) {
    const chipModel = d.model ? String(d.model) : 'auto';
    S.ctx.ui.setStatus({
      provider: {
        text: `${view.label} · ${chipModel}`,
        chip: `${view.label} · ${chipModel}`.slice(0, 34),
        level: words(view.state) === 'connected' ? 'ok' : 'warn',
      },
    });
  } else {
    S.ctx.ui.setStatus({ provider: { text: 'none', chip: 'No model', level: null } });
  }
}

function localGroupRow(view) {
  const st = localState(view);
  const n = modelsOf(view).length;
  const reason = n
    ? 'Local servers are detected automatically; stop a server to remove its row. Nothing leaves this PC.'
    : 'No local model is available yet, so Test and Use as default stay disabled.';
  return rowEl({
    id: 'local', name: view.label || 'On this PC', host: 'managed by ValoHub', kind: 'local', st,
    meta: `${n} model${n === 1 ? '' : 's'} detected`,
    actions: [
      actionButton({ testid: 'provider-connect-local', label: 'Manage', cls: 'btn btn--primary',
                     onclick: () => openModelPicker('local') }),
      actionButton({ testid: 'provider-test-local', label: 'Test', disabled: !n, reasonId: 'provider-note-local',
                     title: n ? 'Send a 1-token chat to the first local model' : 'No local models to test',
                     onclick: () => testProvider(view, null) }),
      actionButton({ testid: 'provider-default-local', label: view.default ? 'Default model' : 'Use as default',
                     disabled: !n, reasonId: 'provider-note-local',
                     onclick: () => { if (n) setDefault('local', bestModel(view)); } }),
      actionButton({ testid: 'provider-disconnect-local', label: 'Disconnect', cls: 'btn btn--ghost btn--danger',
                     disabled: true, reasonId: 'provider-note-local',
                     title: 'Local servers are detected automatically; stop the server to remove it' }),
    ],
    children: el('p', { class: 'provider-row__reason', id: 'provider-note-local' }, reason),
  });
}

function endpointRow(localView, ep, models) {
  const epModels = models.filter((m) => m && m.endpoint === ep.id);
  const id = String(ep.id);
  const noteId = `provider-endpoint-note-${id}`;
  return rowEl({
    id, name: words(ep.label) || id, host: words(ep.host), kind: 'local',
    st: endpointState(ep), extraClass: 'provider-row--sub',
    meta: epModels.length ? `${epModels.length} model${epModels.length === 1 ? '' : 's'}` : null,
    actions: [
      actionButton({ testid: `provider-connect-${id}`, label: 'Manage', cls: 'btn btn--ghost',
                     onclick: () => openModelPicker(id) }),
      actionButton({ testid: `provider-test-${id}`, label: 'Test', cls: 'btn btn--ghost',
                     disabled: !epModels.length, reasonId: noteId,
                     onclick: () => testProvider(localView, ep) }),
      actionButton({ testid: `provider-default-${id}`, label: 'Use as default', cls: 'btn btn--ghost',
                     disabled: !epModels.length, reasonId: noteId,
                     onclick: () => { if (epModels.length) setDefault('local', epModels[0].id); } }),
    ],
    children: epModels.length ? null : el('p', { class: 'provider-row__reason', id: noteId },
      ep.reachable === false ? 'This server did not answer, so it has no models to choose.' : 'No models listed for this server.'),
  });
}

function cloudRow(view) {
  const st = providerState(view);
  const n = modelCount(view);
  const configured = !!view.configured || view.kind === 'custom';
  const needsKey = !view.configured && words(view.state) === 'not_connected';
  const noteId = `provider-note-${view.id}`;
  const keyHint = words(view.last4) ? `key ••••${view.last4}` : (view.configured ? 'key stored' : 'no key stored');
  const notes = [];
  if (needsKey) notes.push('No key yet: Test and Use as default stay disabled until the provider is connected.');
  if (configured && n === 0) notes.push('No models for this key — open the provider console to check the account.');
  return rowEl({
    id: view.id, name: view.label, host: providerHost(view) || 'custom endpoint', kind: 'cloud', st,
    meta: [keyHint, view.default ? `default · ${view.default_model || 'auto'}` : null].filter(Boolean).join(' · '),
    actions: [
      actionButton({
        testid: `provider-connect-${view.id}`, label: words(view.state) === 'connected' ? 'Manage' : 'Connect',
        cls: 'btn btn--primary', onclick: () => openForProvider(view),
      }),
      actionButton({
        testid: `provider-test-${view.id}`, label: 'Test', disabled: !configured, reasonId: noteId,
        title: configured ? 'Verify the key and send a 1-token chat' : 'Connect this provider first',
        onclick: () => testProvider(view, null),
      }),
      actionButton({
        testid: `provider-default-${view.id}`, label: view.default ? 'Default model' : 'Use as default',
        disabled: n === 0 && !words(view.default_model), reasonId: noteId,
        title: n === 0 && !words(view.default_model) ? 'No models for this key yet' : 'Make this provider the Coach default',
        onclick: () => (n > 0 || words(view.default_model) ? setDefault(view.id, bestModel(view)) : openModelPicker(view.id)),
      }),
      actionButton({
        testid: `provider-disconnect-${view.id}`, label: 'Disconnect', cls: 'btn btn--ghost btn--danger',
        disabled: !configured, reasonId: noteId,
        title: configured ? 'Delete the stored key from this PC' : 'No credential is stored',
        onclick: () => disconnect(view),
      }),
    ],
    children: [
      notes.length ? el('p', { class: 'provider-row__reason', id: noteId }, notes.join(' ')) : null,
      view.console_url
        ? el('button', {
          type: 'button', class: 'btn btn--ghost provider-row__inline',
          onclick: async () => {
            try { await S.ctx.api.post(`${API}/${encodeURIComponent(view.id)}/open-console`, {}); } catch {
              window.open(view.console_url, '_blank', 'noopener');
            }
          },
        }, `Open ${view.label} console`)
        : null,
    ],
  });
}

function render() {
  if (!S.root || S.disposed) return;
  const active = document.activeElement;
  const keepFocus = active && S.root.contains(active) ? active.getAttribute('data-testid') : null;

  const head = el('div', { class: 'providers__head' },
    el('p', { class: 'providers__intro' },
      'Local models run on this PC. Cloud providers are opt-in per provider and only ever receive the coach summary — never the replay file.'),
    el('div', { class: 'providers__toolbar' },
      el('span', { class: 'providers__stamp' }, S.lastLoaded ? `Updated ${new Date(S.lastLoaded).toLocaleTimeString()}` : 'Not loaded'),
      el('span', { class: 'providers__stamp' }, `Default: ${defaultLabel()}`),
      el('button', { type: 'button', class: 'btn btn--ghost', 'data-testid': 'providers-refresh', onclick: () => reload({ probe: true }) }, 'Refresh')));

  const local = el('section', {
    class: 'providers__group', 'data-testid': 'provider-group-local', 'aria-labelledby': 'provider-group-local-title',
  }, el('div', { class: 'providers__grouphead' },
    el('h2', { class: 'providers__grouptitle', id: 'provider-group-local-title' }, 'On this PC'),
    el('span', { class: 'providers__groupnote' }, 'Detected automatically — no key leaves this device')));

  const cloud = el('section', {
    class: 'providers__group', 'data-testid': 'provider-group-cloud', 'aria-labelledby': 'provider-group-cloud-title',
  }, el('div', { class: 'providers__grouphead' },
    el('h2', { class: 'providers__grouptitle', id: 'provider-group-cloud-title' }, 'Cloud providers'),
    el('span', { class: 'providers__groupnote' }, 'Each provider needs your consent before its first request')));

  if (S.error) {
    head.append(errorCard(S.error.message, () => reload({ probe: true })));
    local.append(placeholder('Provider state is unavailable while the backend is not responding.'));
    cloud.append(placeholder('Provider state is unavailable while the backend is not responding.'));
  } else if (!S.providers.length) {
    local.append(placeholder('No local provider row was returned by the API.'));
    cloud.append(placeholder('No cloud provider row was returned by the API.'));
  } else {
    const localView = providerById('local');
    if (localView) {
      local.append(localGroupRow(localView));
      const eps = Array.isArray(localView.endpoints) ? localView.endpoints : [];
      const models = modelsOf(localView);
      if (!eps.length) {
        local.append(placeholder('No local model server is installed or answering. Start llama.cpp, LM Studio or Ollama, then Refresh.'));
      } else {
        eps.forEach((ep) => local.append(endpointRow(localView, ep, models)));
      }
    } else {
      local.append(placeholder('No local provider row was returned by the API.'));
    }
    const clouds = S.providers.filter((v) => v.kind !== 'local');
    if (!clouds.length) cloud.append(placeholder('No cloud provider row was returned by the API.'));
    clouds.forEach((view) => cloud.append(cloudRow(view)));
  }

  S.root.replaceChildren(el('div', { class: 'providers-space', 'data-testid': 'providers' },
    head,
    el('div', { class: 'providers__list', 'data-testid': 'providers-list' }, local, cloud),
    el('p', { class: 'providers__foot' },
      S.offlineDefault
        ? 'Cloud is off until you enable a provider. ValoHub never falls back to the cloud silently.'
        : 'This build does not default to offline mode — check every cloud provider before use.')));

  if (keepFocus) {
    const next = S.root.querySelector(`[data-testid="${keepFocus}"]`);
    if (next && typeof next.focus === 'function') next.focus({ preventScroll: true });
  }
}

// ------------------------------------------------------------------ space module

export default {
  id: 'providers',
  title: 'AI Providers',
  accent: 'providers',
  async mount(root, ctx) {
    S.root = root;
    S.ctx = ctx;
    S.disposed = false;
    S.providers = [];
    S.error = null;
    S.default = null;
    S.busy.clear();
    S.cache = ctx.state.get(CACHE_KEY, null);
    ctx.ui.skeleton(root, 4);
    // Fast path: cached/known registry first so the space paints well under the mount budget,
    // then probe local endpoints in the background and re-render when the probe answers.
    await reload({ probe: false });
    if (!S.disposed) {
      reload({ probe: true }).catch((err) => { console.error('provider probe failed', err); });
    }
  },
  unmount() {
    S.disposed = true;
    clearAll();
    if (S.modal) {
      try { S.modal.close(); } catch { /* already closed */ }
      S.modal = null;
    }
    S.root = null;
    S.ctx = null;
    S.providers = [];
  },
};
