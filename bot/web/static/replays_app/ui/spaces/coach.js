// Coach space (/coach, /coach/{id}): sessions | report | context.
// Model output and replay data are only ever placed in text nodes (textContent).
import { el } from '../shell.js';

const ME_KEY = (id) => `me.${id}`; // ctx.state namespaces it as vp.coach.me.<id>
const enc = encodeURIComponent;
const SEVERITIES = ['major', 'minor', 'info'];

export function sev(v) { return SEVERITIES.includes(v) ? v : 'info'; }

export function errorMessage(err) {
  const s = err && err.status;
  const m = (err && err.message) || 'Request failed';
  const rid = err && err.requestId ? ` (request ${err.requestId})` : '';
  if (s === 409 || s === 423) return `A review is already running for this replay. Wait for it to finish, then press Retry.${rid}`;
  if (s === 404 || s === 410) return `This replay was not found. It may have been deleted — open the Library to pick another.${rid}`;
  if (s >= 500) return `The coach backend failed (${s}). Retry, or check Settings → Parser. ${m}${rid}`;
  if (s === 0) return 'Backend unreachable. Check that ValoHub is running, then Retry.';
  return `${m}${rid}`;
}

// Normalise a valoplant_coach_api/v1 payload into a renderable model (pure).
export function normalizeReport(data) {
  if (!data || typeof data !== 'object') throw new Error('Invalid coach response');
  const llm = data.llm || {};
  const rules = (data.rules && data.rules.findings) || [];
  const narr = data.narration || null;
  const toRefs = (refs) => (Array.isArray(refs) ? refs : [])
    .filter((r) => r && Number.isInteger(r.round) && Number.isFinite(r.tSec != null ? r.tSec : r.t) && (r.tSec != null ? r.tSec : r.t) >= 0)
    .map((r) => ({ round: r.round, seconds: Number.isFinite(r.tSec) ? r.tSec : r.t / 1000 }));
  const ruleItems = rules.map((f) => ({
    title: String(f.kind || 'finding').replace(/_/g, ' '), detail: String(f.text || ''), severity: sev(f.severity), refs: toRefs(f.refs), source: 'rules',
  }));
  const mistakes = ((narr && narr.mistakes) || []).map((f) => ({ title: String(f.title || 'Finding'), detail: String(f.detail || ''), severity: sev(f.severity), refs: toRefs(f.refs), source: 'model' }));
  const drills = ((narr && narr.drills) || []).map((f) => ({ title: String(f.title || 'Drill'), detail: String(f.detail || ''), severity: 'info', refs: toRefs(f.refs), source: 'model' }));
  const available = !!llm.available;
  return {
    player: data.player || null,
    rounds: data.roundsAnalyzed || 0,
    summary: (narr && narr.summary) || `Rules-only review: ${ruleItems.length} replay-backed finding${ruleItems.length === 1 ? '' : 's'}. Open any moment to inspect the round.`,
    rulesOnly: !available,
    reason: available ? '' : String(llm.reason || 'Narration is optional.'),
    backend: llm.backend || '', model: llm.model || '',
    ruleItems, mistakes, drills,
  };
}

export function reportText(r, id) {
  const lines = [];
  lines.push(`Coach review · ${(r.player && (r.player.agent || r.player.slot)) || 'Player'} · ${r.rounds} rounds`);
  lines.push(r.rulesOnly ? `Rules-only (no model): ${r.reason}` : `Narrated by ${r.backend} · ${r.model}`);
  lines.push('', r.summary);
  const sect = (t, items) => {
    if (!items.length) return;
    lines.push('', t);
    for (const f of items) {
      lines.push(`- [${f.severity}] ${f.title}: ${f.detail}`);
      for (const ref of f.refs) lines.push(`    R${ref.round} ${ref.seconds.toFixed(1)}s  /replay/${enc(id)}?r=${ref.round}&t=${Math.round(ref.seconds * 10) / 10}`);
    }
  };
  sect('Findings (replay rules)', r.ruleItems);
  sect('Coach perspective', r.mistakes);
  sect('Practice drills', r.drills);
  return lines.join('\n');
}

export function estimateTokens(r) { return Math.ceil(reportText(r, 'x').length / 4); }

function apiErr(res, data) {
  const e = data && data.error;
  const err = new Error(typeof e === 'string' ? e : (e && e.message) || res.statusText || `HTTP ${res.status}`);
  err.status = res.status; err.requestId = (e && e.request_id) || res.headers.get('x-request-id');
  return err;
}

// Read a response that may be JSON or SSE (token/usage/done/error). A stream without `done` fails.
export async function readCoachResponse(res, { onToken } = {}) {
  const ct = (res.headers.get('content-type') || '').toLowerCase();
  if (!ct.includes('text/event-stream')) {
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw apiErr(res, data);
    return { kind: 'json', data };
  }
  if (!res.ok) throw apiErr(res, {});
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = ''; let text = ''; let done = false; let result = null;
  const flush = (block) => {
    let event = 'message'; const d = [];
    for (const line of block.split(/\r?\n/)) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) d.push(line.slice(5).replace(/^ /, ''));
    }
    if (!d.length) return;
    let p = d.join('\n'); try { p = JSON.parse(p); } catch { /* text */ }
    if (event === 'token') { const t = typeof p === 'string' ? p : (p && p.text) || ''; text += t; if (onToken) onToken(t); }
    else if (event === 'done') { done = true; if (p && typeof p === 'object' && p.schema) result = p; }
    else if (event === 'error') { const e = new Error((p && p.message) || 'Stream error'); e.status = 502; e.requestId = p && p.request_id; throw e; }
  };
  for (;;) {
    const { value, done: end } = await reader.read();
    if (end) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.search(/\r?\n\r?\n/)) >= 0) { flush(buf.slice(0, i)); buf = buf.slice(i).replace(/^\r?\n\r?\n/, ''); }
  }
  if (buf.trim()) flush(buf);
  if (!done) throw Object.assign(new Error('The review stream ended before it finished. Nothing was saved — Retry.'), { status: 502 });
  return { kind: 'stream', text, data: result };
}

export default {
  id: 'coach',
  title: 'Coach',
  accent: 'coach',
  async mount(root, ctx) {
    const me = {
      alive: true, ctx, root, id: ctx.params && ctx.params.id,
      phase: 'idle', error: null, players: [], player: '', providers: null, providersError: null,
      sessions: [], active: -1, streamText: '', abort: null, token: 0,
    };
    this._me = me;
    if (!me.id) return indexRoute(me);
    root.replaceChildren();
    ctx.ui.skeleton(root, 4);
    await load(me);
  },
  unmount() {
    const me = this._me;
    if (!me) return;
    me.alive = false; me.token++;
    if (me.abort) me.abort.abort();
    this._me = null;
  },
};

// ---------- index route ----------
async function indexRoute(me) {
  const { ctx, root } = me;
  root.replaceChildren(el('div', { class: 'coach-index', 'data-testid': 'coach' }, el('h2', { text: 'Coach' }), el('p', { class: 'coach-muted', text: 'Loading replays…' })));
  let list;
  try {
    const d = await ctx.api.get('/api/replays');
    list = (d && Array.isArray(d.replays) ? d.replays : []).filter((r) => r && typeof r.id === 'string');
  } catch (err) {
    if (!me.alive) return;
    root.replaceChildren(el('div', { class: 'coach-index', 'data-testid': 'coach' }, errorCard(errorMessage(err), () => indexRoute(me))));
    return;
  }
  if (!me.alive) return;
  if (!list.length) {
    const host = el('div', { class: 'coach-index', 'data-testid': 'coach' });
    root.replaceChildren(host);
    ctx.ui.empty(host, { icon: '✦', title: 'No replays to review', body: 'Import a replay in the Library, then pick it here.', actionLabel: 'Go to Library', onAction: () => ctx.navigate('/replays') });
    host.querySelector('.empty')?.setAttribute('data-testid', 'coach-empty');
    return;
  }
  const rows = list.map((r) => el('a', { class: 'card coach-pick', href: `/coach/${enc(r.id)}`, 'data-testid': 'coach-pick' },
    el('strong', { text: String(r.name || r.map || r.id) }), el('span', { class: 'coach-muted', text: r.id })));
  root.replaceChildren(el('div', { class: 'coach-index', 'data-testid': 'coach' }, el('h2', { text: 'Coach — choose a replay' }), el('div', { class: 'coach-picklist' }, rows)));
}

function errorCard(msg, retry) {
  return el('div', { class: 'card coach-error', role: 'alert', 'data-testid': 'coach-error' },
    el('p', { text: msg }),
    el('button', { class: 'btn', type: 'button', 'data-testid': 'coach-retry', onclick: retry }, 'Retry'));
}

// ---------- data ----------
async function load(me) {
  const { ctx } = me;
  const token = ++me.token;
  const base = `/api/replays/${enc(me.id)}`;
  let match; let meResp = null; let cached = null;
  try {
    [match, meResp] = await Promise.all([ctx.api.get(base), ctx.api.get(`${base}/me`).catch(() => null)]);
  } catch (err) {
    if (!me.alive || token !== me.token) return;
    me.root.replaceChildren(el('div', { class: 'coach-wrap', 'data-testid': 'coach' }, errorCard(errorMessage(err), () => { me.root.replaceChildren(); ctx.ui.skeleton(me.root, 4); load(me); })));
    return;
  }
  if (!me.alive || token !== me.token) return;
  me.players = ((match && match.players) || []).filter((p) => p && p.id != null).map((p) => ({ id: String(p.id), label: `${p.id} · ${p.name || p.agent || 'Unknown'} · Team ${p.team === 0 ? 'A' : p.team === 1 ? 'B' : '?'}` }));
  const local = ctx.state.get(ME_KEY(me.id), '');
  const pick = [meResp && meResp.player, local].find((v) => v && me.players.some((p) => p.id === v));
  me.player = pick || '';
  try { me.providers = await ctx.api.get('/api/ai/providers'); } catch (err) { me.providersError = errorMessage(err); }
  if (!me.alive || token !== me.token) return;
  if (me.player) {
    try { cached = normalizeReport(await ctx.api.get(`${base}/coach?player=${enc(me.player)}`)); } catch { /* none cached: offer Generate */ }
  }
  if (!me.alive || token !== me.token) return;
  if (cached) { me.sessions.push({ at: Date.now(), player: me.player, report: cached, saved: true }); me.active = 0; me.phase = 'done'; }
  render(me);
}

function defaultProvider(me) {
  const p = me.providers;
  if (!p || !Array.isArray(p.providers) || !p.default) return null;
  const row = p.providers.find((x) => x.id === p.default.provider);
  return row ? { id: row.id, label: row.label, kind: row.kind, model: p.default.model || row.default_model || '' } : null;
}

// ---------- generate ----------
async function generate(me) {
  if (!me.player || me.phase === 'running') return;
  const { ctx } = me;
  me.phase = 'running'; me.error = null; me.streamText = '';
  const ab = new AbortController(); me.abort = ab;
  const token = ++me.token;
  ctx.ui.setStatus({ job: { state: 'running', text: 'Coach review…' } });
  render(me);
  try {
    let res;
    try {
      res = await fetch(`/api/replays/${enc(me.id)}/coach`, {
        method: 'POST', credentials: 'same-origin', signal: ab.signal,
        headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream, application/json' },
        body: JSON.stringify({ player: me.player }),
      });
    } catch (e) { if (e && e.name === 'AbortError') throw e; throw Object.assign(new Error('Backend unreachable'), { status: 0 }); }
    const out = await readCoachResponse(res, { onToken: (t) => { if (me.alive && token === me.token) appendToken(me, t); } });
    if (!me.alive || token !== me.token) return;
    if (!out.data) throw Object.assign(new Error('Stream finished without a report. Retry.'), { status: 502 });
    const report = normalizeReport(out.data);
    me.sessions.push({ at: Date.now(), player: me.player, report, saved: false });
    me.active = me.sessions.length - 1; me.phase = 'done';
  } catch (err) {
    if (!me.alive || token !== me.token) return;
    if (err && err.name === 'AbortError') { me.phase = me.active >= 0 ? 'done' : 'idle'; ctx.ui.toast('Review stopped.', 'info'); }
    else { me.phase = 'error'; me.error = errorMessage(err); }
  } finally {
    me.abort = null;
    if (me.alive) { ctx.ui.setStatus({ job: null }); render(me); }
  }
}

function appendToken(me, t) {
  me.streamText += t;
  const pre = me.root.querySelector('[data-stream]');
  if (pre) pre.append(document.createTextNode(t));
}

// ---------- render ----------
function render(me) {
  const { ctx, root } = me;
  const running = me.phase === 'running';
  const sel = el('select', { class: 'select', 'aria-label': 'Player', 'data-testid': 'coach-player', disabled: running || !me.players.length },
    el('option', { value: '' }, me.players.length ? 'Choose your player…' : 'No players in this replay'),
    me.players.map((p) => el('option', { value: p.id, selected: p.id === me.player }, p.label)));
  sel.addEventListener('change', () => {
    me.player = sel.value;
    if (me.player) {
      ctx.state.set(ME_KEY(me.id), me.player);
      ctx.api.put(`/api/replays/${enc(me.id)}/me`, { player: me.player }).catch(() => { /* remembered locally */ });
    }
    render(me);
  });
  const genBtn = el('button', { class: 'btn btn--primary', type: 'button', 'data-testid': 'coach-generate', disabled: running || !me.player, title: !me.player ? 'Choose your player first — the review is written for one player.' : (running ? 'A review is already running.' : 'Generate a review for the selected player.'), onclick: () => generate(me) }, running ? 'Generating…' : 'Generate review');
  const stopBtn = running ? el('button', { class: 'btn btn--danger', type: 'button', 'data-testid': 'coach-stop', onclick: () => me.abort && me.abort.abort() }, 'Stop') : null;

  const sessions = el('aside', { class: 'coach-col coach-sessions', 'data-testid': 'coach-sessions', 'aria-label': 'Reviews' },
    el('div', { class: 'coach-col-head' }, el('h3', { text: 'Reviews' }),
      el('button', { class: 'btn', type: 'button', 'data-testid': 'coach-new', disabled: running, onclick: () => { me.active = -1; me.phase = 'idle'; me.error = null; render(me); } }, 'New review')),
    me.sessions.length
      ? el('ul', { class: 'coach-list' }, me.sessions.map((s, i) => el('li', {},
        el('button', { class: `coach-session${i === me.active ? ' is-active' : ''}`, type: 'button', 'aria-current': i === me.active ? 'true' : null, disabled: running,
          onclick: () => { me.active = i; me.phase = 'done'; render(me); } },
        el('span', { text: `${s.player} · ${new Date(s.at).toLocaleTimeString()}` }),
        el('span', { class: 'coach-muted', text: `${s.report.rulesOnly ? 'Rules-only' : s.report.model || 'Model'}${s.saved ? ' · saved' : ''}` })))))
      : el('p', { class: 'coach-muted', text: 'No reviews yet for this replay.' }));

  const report = el('section', { class: 'coach-col coach-report-col', 'aria-label': 'Report' },
    el('div', { class: 'coach-controls' }, sel, genBtn, stopBtn), reportBody(me));

  root.replaceChildren(el('div', { class: 'coach-wrap', 'data-testid': 'coach' }, sessions, report, contextRail(me)));
}

function reportBody(me) {
  const { ctx } = me;
  if (me.phase === 'running') {
    return el('div', { class: 'card coach-progress', 'data-testid': 'coach-report', 'aria-busy': 'true' },
      el('p', { text: 'Reviewing replay… the rules run first; model narration may take a minute.' }),
      el('div', { class: 'progress', role: 'progressbar', 'aria-label': 'Review in progress' }, el('div', { class: 'progress__bar progress__bar--indeterminate' })),
      el('pre', { class: 'coach-pre', 'data-stream': '', 'aria-live': 'polite' }, me.streamText));
  }
  if (me.phase === 'error') return errorCard(me.error || 'Review failed.', () => generate(me));
  const s = me.sessions[me.active];
  if (!s) {
    return el('div', { class: 'card coach-emptycard', 'data-testid': 'coach-empty' },
      el('h3', { text: 'No review yet' }),
      el('p', { text: me.player ? 'Generate a review for the selected player. It works without any model: replay rules always run.' : 'Choose your player, then generate a review.' }));
  }
  const r = s.report;
  const attribution = r.rulesOnly
    ? el('p', { class: 'coach-attrib' }, el('span', { class: 'chip', text: 'Rules-only (no model)' }), ' ', el('span', { class: 'coach-muted', text: r.reason }))
    : el('p', { class: 'coach-attrib coach-muted', text: `Narrated by ${r.backend} · ${r.model}` });
  const group = (title, items) => items.length ? el('div', { class: 'coach-group' }, el('h4', { text: title }), items.map((f) => finding(me, f))) : null;
  return el('article', { class: 'coach-report', 'data-testid': 'coach-report' },
    el('div', { class: 'coach-report-head' },
      el('h3', { text: `${(r.player && (r.player.agent || r.player.slot)) || 'Player'} · ${r.rounds} rounds` }),
      el('button', { class: 'btn', type: 'button', 'data-testid': 'coach-copy', onclick: async () => {
        try { await navigator.clipboard.writeText(reportText(r, me.id)); ctx.ui.toast('Report copied.', 'success'); }
        catch { ctx.ui.toast('Could not copy — clipboard blocked.', 'error'); }
      } }, 'Copy report')),
    attribution,
    el('p', { class: 'coach-summary', 'data-testid': 'coach-summary', text: r.summary }),
    el('div', { 'data-testid': 'coach-findings' },
      r.ruleItems.length ? null : el('p', { class: 'coach-muted', text: 'No replay-backed findings for this player.' }),
      group('Findings · replay rules', r.ruleItems), group('Coach perspective', r.mistakes), group('Practice drills', r.drills)));
}

function finding(me, f) {
  const links = f.refs.map((ref) => el('a', { class: 'coach-ref', href: me.ctx.links.moment(me.id, ref.round, ref.seconds), 'aria-label': `Jump to moment: round ${ref.round}, ${ref.seconds.toFixed(1)} seconds`, 'data-testid': 'coach-moment' }, `Jump to moment · R${ref.round} ${ref.seconds.toFixed(1)}s`));
  return el('div', { class: 'card coach-finding' },
    el('div', { class: 'coach-finding-head' }, el('strong', { text: f.title }), el('span', { class: `chip coach-sev coach-sev--${f.severity}`, text: f.severity })),
    f.detail ? el('p', { text: f.detail }) : null,
    links.length ? el('div', { class: 'coach-refs' }, links) : null);
}

function contextRail(me) {
  const dp = defaultProvider(me);
  const s = me.sessions[me.active];
  const rows = [];
  rows.push(el('div', { class: 'coach-ctx-row', 'data-testid': 'coach-provider' },
    el('span', { class: 'coach-muted', text: 'Provider' }),
    el('strong', { text: dp ? `${dp.label}${dp.model ? ` · ${dp.model}` : ''}` : 'None configured' }),
    dp ? el('span', { class: 'chip', text: dp.kind === 'cloud' ? 'CLOUD' : 'LOCAL' }) : null));
  rows.push(el('a', { class: 'btn', href: '/providers', 'data-testid': 'coach-change-provider' }, dp ? 'Change' : 'Set up a provider'));
  if (me.providersError) rows.push(el('p', { class: 'coach-muted', text: `Could not read providers: ${me.providersError}` }));
  if (dp && dp.kind === 'cloud') {
    rows.push(el('p', { class: 'coach-muted', 'data-testid': 'coach-token-estimate', text: s ? `Estimate: ~${estimateTokens(s.report).toLocaleString()} tokens (characters ÷ 4, not billed usage).` : 'Estimate appears after a review (characters ÷ 4, not billed usage).' }));
  }
  if (!dp) {
    rows.push(el('div', { class: 'card coach-noprov' },
      el('strong', { text: 'No model configured' }),
      el('p', { text: 'Reviews still work: replay rules run without a model and are labeled Rules-only. For narration, connect one of:' }),
      el('ul', {}, el('li', { text: 'Managed llama.cpp (on this PC)' }), el('li', { text: 'LM Studio — 127.0.0.1:1234' }), el('li', { text: 'Ollama — 127.0.0.1:11434' })),
      el('a', { class: 'btn btn--primary', href: '/providers', text: 'Open AI Providers' })));
  }
  rows.push(el('button', { class: 'btn', type: 'button', disabled: me.phase === 'running' || !me.player, onclick: () => generate(me) }, 'Regenerate'));
  if (s) rows.push(el('p', { class: 'coach-muted', text: `Player ${s.player}` }));
  return el('aside', { class: 'coach-col coach-context', 'data-testid': 'coach-context', 'aria-label': 'Context' }, el('h3', { text: 'Context' }), rows);
}
