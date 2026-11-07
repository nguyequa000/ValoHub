// ValoHub UI v2 — Analysis space (DESIGN-V2 §4.3).
//
// Left: filters + saved views. Center: heatmap ported from v1 (32px cells on a
// 1024px projection, sqrt intensity, five legend bands). Right: findings with
// deep links, plus the v1 detail panels (utility counts, identity enrichment,
// analysis AI review). Below: round outcomes, economy and duel matrix.
//
// Data: GET /api/replays/{id}/analysis (heat + findings), GET /api/replays/{id}
// (map/roster/round outcomes/utilityCounts), GET .../identity and .../review.
// Chart slots render only when their payload field exists; otherwise they show
// a labelled "Not recorded in this replay" card. Nothing is synthesised.
//
// No innerHTML, ever. Replay strings are untrusted and go in as text nodes.

export const MAP_SIZE = 1024;
export const CELL_SIZE = 32;
export const BAND_COUNT = 5;

export const LAYERS = [
  { id: 'positions', label: 'Player positions', rgb: '255,194,89' },
  { id: 'deaths', label: 'Deaths', rgb: '255,70,85' },
  { id: 'util', label: 'Utility / ultimates', rgb: '212,142,255' },
];

const LAYER_BY_ID = new Map(LAYERS.map((layer) => [layer.id, layer]));
const TEAM_LABEL = ['Team A', 'Team B'];
const GROUP_TABS = [['layer', 'Layer'], ['rounds', 'Rounds'], ['player', 'Player'], ['team', 'Team']];
const SEVERITY_CLASSES = new Set(['critical', 'warning', 'tip', 'info']);
const UUID_RE = /^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$/i;

// ---------------------------------------------------------------- pure helpers

export function layerLabel(layerId) {
  return (LAYER_BY_ID.get(layerId) || LAYER_BY_ID.get('positions')).label;
}

export function layerRgb(layerId) {
  return (LAYER_BY_ID.get(layerId) || LAYER_BY_ID.get('positions')).rgb;
}

export function roundMatches(round, filter) {
  if (filter == null || filter === 'all') return true;
  if (typeof filter === 'number') return Number(round) === filter;
  if (typeof filter === 'object') return Number(round) >= filter.from && Number(round) <= filter.to;
  return true;
}

/** Aggregate analysis.heat bins for one layer + round selection. Same maths as v1. */
export function collectCells(heat, { layer = 'positions', round = 'all' } = {}) {
  const counts = new Map();
  if (!Array.isArray(heat)) return counts;
  for (const entry of heat) {
    if (!entry || entry.kind !== layer) continue;
    if (!roundMatches(entry.round, round)) continue;
    const bins = Array.isArray(entry.bins) ? entry.bins : [];
    for (const bin of bins) {
      if (!Array.isArray(bin) || bin.length !== 3) continue;
      const [x, y, count] = bin;
      if (!Number.isFinite(x) || !Number.isFinite(y) || !Number.isFinite(count) || count <= 0) continue;
      const key = `${x},${y}`;
      counts.set(key, (counts.get(key) || 0) + count);
    }
  }
  return counts;
}

/** Peak cell + per-band cell counts/ranges for the legend. */
export function summarizeCells(cells) {
  let total = 0;
  let maximum = 0;
  let peak = null;
  for (const [key, count] of cells) {
    total += count;
    if (count > maximum) {
      maximum = count;
      const [x, y] = key.split(',').map(Number);
      peak = { x, y, count };
    }
  }
  const bands = Array.from({ length: BAND_COUNT }, (_, index) => ({ index, cells: 0, low: null, high: null }));
  const scale = Math.max(1, maximum);
  for (const count of cells.values()) {
    const index = Math.min(BAND_COUNT - 1, Math.floor(Math.sqrt(count / scale) * BAND_COUNT));
    const band = bands[index];
    band.cells += 1;
    band.low = band.low == null ? count : Math.min(band.low, count);
    band.high = band.high == null ? count : Math.max(band.high, count);
  }
  return { cells: cells.size, total, maximum, peak, bands };
}

export function cellAlpha(count, maximum) {
  return 0.18 + 0.66 * Math.sqrt(count / Math.max(1, maximum));
}

export function cellFill(layerId, count, maximum) {
  return `rgba(${layerRgb(layerId)},${cellAlpha(count, maximum).toFixed(3)})`;
}

/** Findings deep-link contract: /replay/{id}?r=&t= with t in round seconds. */
export function findingLink(replayId, finding) {
  const id = encodeURIComponent(String(replayId == null ? '' : replayId));
  const params = [];
  const round = Number(finding && finding.round);
  if (Number.isFinite(round) && round > 0) params.push(`r=${Math.trunc(round)}`);
  const ms = Number(finding && finding.tMs);
  const seconds = Number.isFinite(ms) && ms > 0 ? Number((ms / 1000).toFixed(3)) : 0;
  params.push(`t=${seconds}`);
  return `/replay/${id}?${params.join('&')}`;
}

export function scoreOf(matchDoc) {
  const rounds = Array.isArray(matchDoc && matchDoc.rounds) ? matchDoc.rounds : [];
  let team0 = 0;
  let team1 = 0;
  for (const round of rounds) {
    if (round && round.winner === 0) team0 += 1;
    else if (round && round.winner === 1) team1 += 1;
  }
  return team0 + team1 === 0 ? null : { team0, team1, total: team0 + team1 };
}

export function severityClass(value) {
  const key = String(value == null ? '' : value).toLowerCase();
  if (key === 'critical' || key === 'danger' || key === 'high') return 'critical';
  if (key === 'warning' || key === 'warn' || key === 'medium') return 'warning';
  if (key === 'tip' || key === 'ok' || key === 'low') return 'tip';
  return 'info';
}

export function formatOffset(ms) {
  const value = Number.isFinite(ms) && ms > 0 ? ms : 0;
  const minutes = Math.floor(value / 60000);
  const seconds = (value - minutes * 60000) / 1000;
  return `${minutes}:${seconds.toFixed(1).padStart(4, '0')}`;
}

export function formatDuration(ms) {
  const value = Number.isFinite(ms) && ms > 0 ? ms : 0;
  const minutes = Math.floor(value / 60000);
  const seconds = Math.floor((value - minutes * 60000) / 1000);
  return `${minutes}:${String(seconds).padStart(2, '0')}`;
}

export function teamLabel(team) {
  return team === 0 || team === 1 ? TEAM_LABEL[team] : 'Unknown';
}

/** Optional economy payload: [{round, teams:[a,b]}] or [{round, team0, team1}]. */
export function economySeries(analysisPayload, matchPayload) {
  const source = (analysisPayload && analysisPayload.economy) || (matchPayload && matchPayload.economy);
  if (!source || typeof source !== 'object') return null;
  const rows = Array.isArray(source) ? source : (Array.isArray(source.rounds) ? source.rounds : null);
  if (!rows) return null;
  const series = [];
  for (const row of rows) {
    if (!row || !Number.isFinite(Number(row.round))) continue;
    const pair = Array.isArray(row.teams) ? row.teams : [row.team0, row.team1];
    const team0 = Number.isFinite(Number(pair[0])) ? Number(pair[0]) : null;
    const team1 = Number.isFinite(Number(pair[1])) ? Number(pair[1]) : null;
    if (team0 == null && team1 == null) continue;
    series.push({ round: Number(row.round), team0, team1 });
  }
  if (!series.length) return null;
  return {
    rounds: series,
    unit: typeof source.unit === 'string' ? source.unit.slice(0, 24) : '',
    source: typeof source.source === 'string' ? source.source.slice(0, 40) : '',
  };
}

/** Optional duel payload: {players:[{id,name,agent,team}], matrix:[[kills]]}. */
export function duelsSeries(analysisPayload) {
  const block = analysisPayload && analysisPayload.duels;
  if (!block || !Array.isArray(block.players) || !Array.isArray(block.matrix)) return null;
  const players = block.players.map((entry) => {
    if (typeof entry === 'string') return { id: entry, name: '', agent: '', team: null };
    if (!entry || typeof entry !== 'object') return null;
    const id = typeof entry.id === 'string' ? entry.id : '';
    if (!id) return null;
    return {
      id,
      name: typeof entry.name === 'string' ? entry.name : '',
      agent: typeof entry.agent === 'string' ? entry.agent : '',
      team: entry.team === 0 || entry.team === 1 ? entry.team : null,
    };
  }).filter(Boolean);
  if (!players.length) return null;
  const matrix = players.map((_, row) => players.map((__, column) => {
    const value = Array.isArray(block.matrix[row]) ? block.matrix[row][column] : 0;
    return Number.isFinite(Number(value)) && Number(value) > 0 ? Number(value) : 0;
  }));
  return { players, matrix };
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function textMentions(text, token) {
  if (!token) return false;
  return new RegExp(`(^|[^A-Za-z0-9_])${escapeRegExp(token)}([^A-Za-z0-9_]|$)`).test(text);
}

export function findingMatches(finding, { player = '', team = '', players = [] } = {}) {
  if (!finding) return false;
  const text = `${finding.title || ''} ${finding.detail || ''}`;
  if (player) {
    const entry = players.find((candidate) => candidate && candidate.id === player);
    const names = [player, entry && entry.name].filter(Boolean);
    if (!names.some((name) => textMentions(text, name))) return false;
  }
  if (team !== '' && team != null) {
    const teamIndex = Number(team);
    const letter = TEAM_LABEL[teamIndex] ? TEAM_LABEL[teamIndex].slice(-1) : '';
    if (textMentions(text, `Team ${teamIndex}`)) return true;
    if (letter && textMentions(text, `Team ${letter}`)) return true;
    const slots = players.filter((candidate) => candidate && candidate.team === teamIndex).map((candidate) => candidate.id);
    if (!slots.some((slot) => textMentions(text, slot))) return false;
  }
  return true;
}

export function findingsMarkdown({ replayId, mapName, score, layerLabel: layerName, roundLabel, legend, findings }) {
  const lines = [];
  lines.push(`# Analysis — ${mapName || 'Replay'}`);
  if (score) lines.push(`Score: ${TEAM_LABEL[0]} ${score.team0}–${score.team1} ${TEAM_LABEL[1]}`);
  lines.push(`Replay: \`${replayId || ''}\``);
  lines.push('');
  lines.push('## Heatmap');
  lines.push(`- Layer: ${layerName}`);
  lines.push(`- Rounds: ${roundLabel}`);
  lines.push('- Cell size: 32 × 32 minimap px (1024 px projection)');
  if (legend && legend.maximum > 0 && legend.peak) {
    lines.push(`- Peak cell: ${legend.maximum} sample${legend.maximum === 1 ? '' : 's'} at ${legend.peak.x},${legend.peak.y}`);
    lines.push(`- Cells: ${legend.cells} (${legend.total} samples)`);
    for (const band of legend.bands) {
      if (!band.cells) continue;
      lines.push(`- Band ${band.index + 1}: ${band.cells} cell${band.cells === 1 ? '' : 's'} · ${band.low === band.high ? band.low : `${band.low}–${band.high}`} samples`);
    }
  } else {
    lines.push('- Peak cell: none — the replay did not record this layer for the selection');
  }
  lines.push('');
  lines.push(`## Findings (${findings.length})`);
  if (!findings.length) {
    lines.push('No findings recorded for this selection.');
  } else {
    for (const finding of findings) {
      const title = finding.title || 'Finding';
      const severity = String(finding.severity || 'info');
      const round = Number.isFinite(Number(finding.round)) ? `R${finding.round} · ` : '';
      lines.push(`- **${round}${formatOffset(Number(finding.tMs))} — ${title}** (${severity})`);
      if (finding.detail) lines.push(`  ${finding.detail}`);
      lines.push(`  Jump: ${findingLink(replayId, finding)}`);
    }
  }
  return `${lines.join('\n')}\n`;
}

// ---------------------------------------------------------------- DOM helpers

function el(tag, attrs, ...children) {
  const node = document.createElement(tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      if (key === 'class') node.className = value;
      else if (key === 'text') node.textContent = value;
      else if (key === 'dataset') Object.assign(node.dataset, value);
      else if (key.startsWith('on') && typeof value === 'function') node.addEventListener(key.slice(2).toLowerCase(), value);
      else node.setAttribute(key, value === true ? '' : String(value));
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

const SVG_NS = 'http://www.w3.org/2000/svg';
function svgEl(tag, attrs, ...children) {
  const node = document.createElementNS(SVG_NS, tag);
  if (attrs) {
    for (const [key, value] of Object.entries(attrs)) {
      if (value == null || value === false) continue;
      node.setAttribute(key, String(value));
    }
  }
  for (const child of children.flat(Infinity)) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

function mapId(value) {
  return typeof value === 'string' && UUID_RE.test(value) ? value : '';
}

function formatDate(value) {
  if (typeof value !== 'string' || !value) return 'Date unknown';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return 'Date unknown';
  try {
    return date.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' });
  } catch {
    return date.toISOString().slice(0, 10);
  }
}

async function mapLimit(items, limit, fn) {
  const out = new Array(items.length);
  let cursor = 0;
  const workers = Array.from({ length: Math.min(Math.max(1, limit), items.length) }, async () => {
    while (cursor < items.length) {
      const index = cursor;
      cursor += 1;
      out[index] = await fn(items[index], index);
    }
  });
  await Promise.all(workers);
  return out;
}

function unavailableCard(title, body) {
  return el('div', { class: 'an__unavailable' },
    el('span', { class: 'an__unavailable-art', 'aria-hidden': 'true', text: '◌' }),
    el('p', { class: 'an__unavailable-title', text: title }),
    el('p', { class: 'an__sub', text: body }));
}

// ---------------------------------------------------------------- runtime

let live = false;
let rootEl = null;
let ctxRef = null;
let replayId = '';
let matchDoc = null;
let analysis = null;
let mapImage = null;
let mapReady = false;
let observer = null;
let narrowQuery = null;
let narrowHandler = null;
let filtersEl = null;
let lastSummary = null;
let lastCells = null;
let identityState = { data: null, message: 'Not checked yet.', busy: false, error: false };
let reviewState = { data: null, message: 'Not checked yet.', busy: false, error: false };

const state = {
  layer: 'positions',
  roundMode: 'all',
  from: 1,
  to: 1,
  single: 1,
  player: '',
  team: '',
};

function roundsMax() {
  const fromMatch = Array.isArray(matchDoc && matchDoc.rounds)
    ? matchDoc.rounds.map((round) => Number(round && round.n)).filter((n) => Number.isFinite(n) && n > 0)
    : [];
  if (fromMatch.length) return Math.max(...fromMatch);
  const count = Number(analysis && analysis.rounds);
  return Number.isFinite(count) && count > 0 ? Math.trunc(count) : 0;
}

function roundNumbers() {
  const fromMatch = Array.isArray(matchDoc && matchDoc.rounds)
    ? matchDoc.rounds.map((round) => Number(round && round.n)).filter((n) => Number.isFinite(n) && n > 0)
    : [];
  if (fromMatch.length) return [...new Set(fromMatch)].sort((a, b) => a - b);
  const max = roundsMax();
  return Array.from({ length: max }, (_, index) => index + 1);
}

function roster() {
  const players = Array.isArray(matchDoc && matchDoc.players) ? matchDoc.players : [];
  return players
    .filter((player) => player && typeof player.id === 'string')
    .map((player) => ({
      id: player.id,
      name: typeof player.name === 'string' ? player.name : '',
      agent: typeof player.agent === 'string' ? player.agent : '',
      team: player.team === 0 || player.team === 1 ? player.team : null,
    }))
    .sort((a, b) => {
      const teamA = a.team == null ? 9 : a.team;
      const teamB = b.team == null ? 9 : b.team;
      if (teamA !== teamB) return teamA - teamB;
      return a.id.localeCompare(b.id, undefined, { numeric: true });
    });
}

function currentRoundFilter() {
  if (state.roundMode === 'single') return Number(state.single) || 'all';
  if (state.roundMode === 'range') return { from: Number(state.from), to: Number(state.to) };
  return 'all';
}

function roundLabelText() {
  if (state.roundMode === 'single') return `Round ${state.single}`;
  if (state.roundMode === 'range') return `Rounds ${state.from}–${state.to}`;
  return `All ${roundsMax()} rounds`;
}

function filtersToJSON() {
  return {
    layer: state.layer,
    roundMode: state.roundMode,
    from: state.from,
    to: state.to,
    single: state.single,
    player: state.player,
    team: state.team,
  };
}

function sanitizeFilters(raw) {
  const max = roundsMax();
  const source = raw && typeof raw === 'object' ? raw : {};
  const layer = LAYERS.some((entry) => entry.id === source.layer) ? source.layer : 'positions';
  const mode = ['all', 'range', 'single'].includes(source.roundMode) ? source.roundMode : 'all';
  const clamp = (value, fallback) => {
    const n = Number(value);
    if (!Number.isFinite(n)) return fallback;
    const rounded = Math.trunc(n);
    if (max <= 0) return Math.max(1, rounded);
    return Math.min(max, Math.max(1, rounded));
  };
  const from = clamp(source.from, 1);
  let to = clamp(source.to, max > 0 ? max : 1);
  if (to < from) to = from;
  const single = clamp(source.single, from);
  const known = new Set(roster().map((player) => player.id));
  const player = typeof source.player === 'string' && known.has(source.player) ? source.player : '';
  const team = source.team === 0 || source.team === 1 ? String(source.team)
    : (source.team === '0' || source.team === '1' ? source.team : '');
  return { layer, roundMode: mode, from, to, single, player, team };
}

function readSavedViews() {
  const raw = ctxRef ? ctxRef.state.get('savedViews', []) : [];
  if (!Array.isArray(raw)) return [];
  return raw
    .filter((view) => view && typeof view === 'object' && typeof view.name === 'string' && view.filters && typeof view.filters === 'object')
    .slice(0, 20)
    .map((view, index) => ({
      id: typeof view.id === 'string' ? view.id : `view-${index}`,
      name: view.name.slice(0, 60),
      filters: sanitizeFilters(view.filters),
    }));
}

function persistFilters() {
  if (ctxRef) ctxRef.state.set('filters', filtersToJSON());
}

function toast(message, kind = 'info') {
  if (ctxRef && ctxRef.ui && ctxRef.ui.toast) ctxRef.ui.toast(message, kind);
}

/** Deep links go through the shell when it exposes ctx.links.moment(). */
function momentUrl(round, seconds) {
  const r = Number.isFinite(Number(round)) && Number(round) > 0 ? Math.trunc(Number(round)) : 1;
  const t = Number.isFinite(Number(seconds)) ? Number(seconds) : 0;
  const links = ctxRef && ctxRef.links;
  if (links && typeof links.moment === 'function') {
    try { return links.moment(replayId, r, t); } catch { /* fall back to the query form */ }
  }
  return findingLink(replayId, { round: r, tMs: t * 1000 });
}

// ---------------------------------------------------------------- lifecycle

export default {
  id: 'analysis',
  title: 'Analysis',
  accent: 'analysis',

  async mount(root, ctx) {
    live = true;
    rootEl = root;
    ctxRef = ctx;
    replayId = ctx.params && ctx.params.id ? String(ctx.params.id) : '';
    matchDoc = null;
    analysis = null;
    mapImage = null;
    mapReady = false;
    lastSummary = null;
    lastCells = null;
    identityState = { data: null, message: 'Not checked yet.', busy: false, error: false };
    reviewState = { data: null, message: 'Not checked yet.', busy: false, error: false };

    if (!replayId) {
      await mountPicker();
      return;
    }
    await load();
  },

  unmount() {
    live = false;
    teardownView();
    rootEl = null;
    ctxRef = null;
    matchDoc = null;
    analysis = null;
    mapImage = null;
    filtersEl = null;
    lastSummary = null;
    lastCells = null;
  },

  onKey(event) {
    if (!analysis || !event || event.ctrlKey || event.metaKey || event.altKey) return false;
    if (event.key !== '[' && event.key !== ']') return false;
    const index = LAYERS.findIndex((layer) => layer.id === state.layer);
    const next = (index + (event.key === ']' ? 1 : LAYERS.length - 1)) % LAYERS.length;
    setLayer(LAYERS[next].id);
    return true;
  },
};

function teardownView() {
  document.querySelector('[data-testid="analysis-exports"]')?.remove();
  if (observer) { observer.disconnect(); observer = null; }
  if (narrowQuery && narrowHandler) narrowQuery.removeEventListener('change', narrowHandler);
  narrowQuery = null;
  narrowHandler = null;
}

async function load() {
  if (!live || !rootEl || !ctxRef) return;
  ctxRef.ui.skeleton(rootEl, 8);
  try {
    const id = encodeURIComponent(replayId);
    const analysisPromise = ctxRef.api.get(`/api/replays/${id}/analysis`);
    const matchPromise = ctxRef.api.get(`/api/replays/${id}`).catch((error) => {
      if (error && error.status === 404) return null;
      throw error;
    });
    analysis = await analysisPromise;
    matchDoc = await matchPromise;
    if (!live) return;
    if (!analysis || typeof analysis !== 'object') throw new Error('The analysis payload was empty.');
    buildPage();
  } catch (error) {
    if (live) renderError(error);
  }
}

function retry() {
  if (!live || !rootEl || !ctxRef) return;
  load();
}

// ---------------------------------------------------------------- picker (no id)

async function mountPicker() {
  const root = rootEl;
  const ctx = ctxRef;
  const list = el('ul', { class: 'an__picklist', 'aria-label': 'Replays' });
  const card = el('section', { class: 'card card--tinted an__pickcard', 'data-testid': 'analysis-empty' },
    el('header', { class: 'an__pickhead' },
      el('div', null,
        el('h2', { class: 'an__heading', text: 'Choose a replay' }),
        el('p', { class: 'an__sub', text: 'Analysis needs a replay. Pick one from the library; filters and saved views open once it loads.' })),
      el('a', { class: 'btn', href: '/replays', text: 'Library' })),
    list);
  root.replaceChildren(el('div', { class: 'an an--picker', 'data-testid': 'analysis' }, card));

  list.append(el('li', { class: 'an__pickrow an__pickrow--loading', 'aria-busy': 'true' },
    el('span', { class: 'skeleton__row' })));
  try {
    const data = await ctx.api.get('/api/replays');
    if (!live) return;
    const entries = (Array.isArray(data) ? data : (data && data.replays) || [])
      .filter((entry) => entry && typeof entry.id === 'string' && entry.id)
      .slice(0, 50);
    list.replaceChildren();
    if (!entries.length) {
      list.append(el('li', { class: 'an__pickempty' },
        el('p', { text: 'No replays imported yet.' }),
        el('a', { class: 'btn btn--primary', href: '/replays', text: 'Import a replay' })));
      return;
    }
    for (const entry of entries) {
      const replay = String(entry.id);
      const rounds = Number.isFinite(Number(entry.rounds)) ? Number(entry.rounds) : null;
      const meta = [rounds ? `${rounds} rounds` : 'Round count unknown', entry.patch ? `patch ${entry.patch}` : '']
        .filter(Boolean).join(' · ');
      list.append(el('li', { class: 'an__pickitem' },
        el('a', {
          class: 'an__pickrow',
          href: `/analysis/${encodeURIComponent(replay)}`,
          'aria-label': `${entry.mapName || 'Unknown map'}, ${meta}, ${formatDate(entry.createdAt)}`,
        },
          el('span', { class: 'an__pickmap', text: entry.mapName || 'Unknown map' }),
          el('span', { class: 'an__pickscore', 'data-score': replay, text: '—' }),
          el('span', { class: 'an__pickmeta', text: meta }),
          el('span', { class: 'an__pickdate', text: formatDate(entry.createdAt) }))));
    }
    const scored = entries.slice(0, 24);
    await mapLimit(scored, 4, async (entry) => {
      try {
        const doc = await ctx.api.get(`/api/replays/${encodeURIComponent(String(entry.id))}`);
        const score = scoreOf(doc);
        if (!live || !score) return;
        const target = list.querySelector(`[data-score="${CSS.escape(String(entry.id))}"]`);
        if (target) target.textContent = `${score.team0}–${score.team1}`;
      } catch { /* score stays unknown; the row still navigates */ }
    });
  } catch (error) {
    if (!live) return;
    list.replaceChildren(el('li', { class: 'an__error', role: 'alert' },
      el('h3', { text: 'Replays could not be loaded' }),
      el('p', { class: 'an__sub', text: describeError(error) }),
      el('button', {
        type: 'button', class: 'btn btn--primary', 'data-testid': 'analysis-retry', text: 'Retry',
        onclick: () => mountPicker(),
      })));
  }
}

// ---------------------------------------------------------------- page build

function buildPage() {
  teardownView();
  const root = rootEl;
  const players = roster();
  const max = roundsMax();
  const saved = ctxRef.state.get('filters', null);
  Object.assign(state, sanitizeFilters(saved));
  if (max > 0) {
    state.to = Math.min(state.to || max, max);
    state.single = Math.min(state.single || 1, max);
  }

  const filterColumn = buildFilters(players, max);
  const mapCard = buildMapCard();
  const findingsCard = buildFindingsCard();
  const detailCard = buildDetailCard();
  const lower = buildLower();

  root.replaceChildren(el('div', { class: 'an', 'data-testid': 'analysis' },
    filterColumn, mapCard, findingsCard, detailCard, lower));
  filtersEl = filterColumn;

  setupNarrowFilters();
  setupTabs(detailCard);
  loadMapImage();
  syncControls();
  renderSavedViews();
  renderAll();
  renderUtility();
  loadIdentity();
  loadReview();
}

function buildFilters(players, max) {
  const layerSelect = el('select', {
    class: 'select', id: 'an-layer', 'data-testid': 'analysis-layer', 'aria-label': 'Heatmap layer',
    onchange: (event) => setLayer(event.target.value),
  }, LAYERS.map((layer) => el('option', { value: layer.id, text: layer.label })));

  const modeButtons = [['all', 'All'], ['range', 'Range'], ['single', 'Single']].map(([mode, label]) =>
    el('button', {
      type: 'button', 'data-mode': mode, 'aria-pressed': 'false', text: label,
      onclick: () => setRoundMode(mode),
    }));

  const fromInput = el('input', {
    class: 'field an__rangefield', id: 'an-from', type: 'number', min: '1', max: String(Math.max(1, max)),
    inputmode: 'numeric', 'aria-label': 'First round', value: String(state.from),
    onchange: (event) => setRange(Number(event.target.value), state.to),
  });
  const toInput = el('input', {
    class: 'field an__rangefield', id: 'an-to', type: 'number', min: '1', max: String(Math.max(1, max)),
    inputmode: 'numeric', 'aria-label': 'Last round', value: String(state.to),
    onchange: (event) => setRange(state.from, Number(event.target.value)),
  });
  const rangeBox = el('div', { class: 'an__range' },
    el('label', { class: 'an__rangepair' }, el('span', { text: 'From' }), fromInput),
    el('label', { class: 'an__rangepair' }, el('span', { text: 'To' }), toInput));

  const rounds = roundNumbers();
  const singleSelect = el('select', {
    class: 'select', id: 'an-round', 'data-testid': 'analysis-round', 'aria-label': 'Single round',
    onchange: (event) => setSingle(Number(event.target.value)),
  }, rounds.map((n) => el('option', { value: String(n), text: `Round ${n}` })));
  const singleBox = el('div', { class: 'an__single' },
    el('label', { class: 'an__label', for: 'an-round', text: 'Round' }), singleSelect);

  const playerSelect = el('select', {
    class: 'select', id: 'an-player', 'data-testid': 'analysis-player', 'aria-label': 'Player',
    onchange: (event) => setPlayer(event.target.value),
  }, el('option', { value: '', text: 'All players' }), players.map((player) =>
    el('option', {
      value: player.id,
      text: `${player.id}${player.agent ? ` · ${player.agent}` : ''}${player.team != null ? ` · ${teamLabel(player.team)}` : ''}`,
    })));

  const teamSelect = el('select', {
    class: 'select', id: 'an-team', 'data-testid': 'analysis-team', 'aria-label': 'Team',
    onchange: (event) => setTeam(event.target.value),
  },
    el('option', { value: '', text: 'Both teams' }),
    el('option', { value: '0', text: teamLabel(0) }),
    el('option', { value: '1', text: teamLabel(1) }));

  const mobile = el('div', { class: 'segmented an__mobile', role: 'group', 'aria-label': 'Filter section' },
    GROUP_TABS.map(([group, label]) => el('button', {
      type: 'button', 'data-group-tab': group, 'aria-pressed': String(group === 'layer'), text: label,
      onclick: () => setFilterGroup(group),
    })));

  const reset = el('button', {
    type: 'button', class: 'btn btn--ghost', 'data-testid': 'analysis-reset', text: 'Reset',
    onclick: () => resetFilters(),
  });

  const viewInput = el('input', {
    class: 'field an__viewname', type: 'text', maxlength: '60', placeholder: 'View name',
    'aria-label': 'Saved view name',
  });
  const saveForm = el('form', {
    class: 'an__saveform',
    onsubmit: (event) => { event.preventDefault(); saveView(viewInput); },
  }, viewInput, el('button', { type: 'submit', class: 'btn', text: 'Save view' }));
  const viewsList = el('ul', { class: 'an__views-list', 'aria-live': 'polite' });

  return el('aside', { class: 'an__filters', 'data-testid': 'analysis-filters', 'data-active': 'layer', 'aria-label': 'Analysis filters' },
    mobile,
    el('div', { class: 'an__filterhead' },
      el('h2', { class: 'an__heading', text: 'Filters' }), reset),
    el('div', { class: 'an__group', 'data-group': 'layer' },
      el('label', { class: 'an__label', for: 'an-layer', text: 'Layer' }), layerSelect),
    el('div', { class: 'an__group', 'data-group': 'rounds' },
      el('span', { class: 'an__label', text: 'Rounds' }),
      el('div', { class: 'segmented an__mode', role: 'group', 'aria-label': 'Round selection' }, modeButtons),
      rangeBox, singleBox),
    el('div', { class: 'an__group', 'data-group': 'player' },
      el('label', { class: 'an__label', for: 'an-player', text: 'Player' }), playerSelect),
    el('div', { class: 'an__group', 'data-group': 'team' },
      el('label', { class: 'an__label', for: 'an-team', text: 'Team' }), teamSelect),
    el('div', { class: 'an__views' },
      el('h3', { class: 'an__views-title', text: 'Saved views' }),
      viewsList,
      saveForm),
    el('p', { class: 'an__hint', text: 'Heat layers arrive pre-aggregated per round. Player and team scope findings.' }));
}

function buildMapCard() {
  const canvasMap = el('canvas', { class: 'an__canvas an__canvas--map', 'aria-hidden': 'true' });
  const canvasHeat = el('canvas', {
    class: 'an__canvas an__canvas--heat', role: 'img',
    'aria-label': 'Spatial heatmap of replay events',
  });
  const empty = el('div', { class: 'an__mapempty', 'data-testid': 'analysis-empty', hidden: true },
    el('h3', { class: 'an__mapempty-title', text: 'No data recorded' }),
    el('p', { class: 'an__sub', text: 'The replay did not record this data for the selected rounds.' }));
  const stage = el('div', { class: 'an__stage', 'data-testid': 'analysis-map' }, canvasMap, canvasHeat, empty);

  const legend = el('div', { class: 'an__legend', 'data-testid': 'analysis-legend', role: 'group', 'aria-label': 'Heatmap legend' });

  const layerSwitch = el('div', { class: 'segmented an__layerswitch', role: 'group', 'aria-label': 'Heatmap layer' },
    LAYERS.map((layer) => el('button', {
      type: 'button', 'data-layer-switch': layer.id, 'aria-pressed': 'false', text: layer.label,
      onclick: () => setLayer(layer.id),
    })));

  const openReplay = el('a', { class: 'btn', href: `/replay/${encodeURIComponent(replayId)}`, text: 'Open replay' });

  const card = el('section', { class: 'card card--tinted an__mapcard', 'aria-labelledby': 'an-map-title' },
    el('header', { class: 'an__cardhead' },
      el('div', null,
        el('h2', { class: 'an__heading', id: 'an-map-title', text: 'Heatmap' }),
        el('p', { class: 'an__sub', id: 'an-map-sub', text: mapSubtitle() })),
      el('div', { class: 'an__mapactions' }, layerSwitch, openReplay)),
    stage,
    legend,
    el('p', { class: 'an__note', text: 'Cells are 32 × 32 minimap px on a 1024 px projection. Movement is sampled once per second per player. An empty layer means the replay did not record that data.' }));

  // Canvas sizing follows the stage; the observer is released in unmount().
  if (typeof ResizeObserver === 'function') {
    observer = new ResizeObserver(() => drawHeat());
    observer.observe(stage);
  }
  return card;
}

function mapSubtitle() {
  const parts = [];
  parts.push(matchDoc && matchDoc.map && matchDoc.map.name ? String(matchDoc.map.name) : 'Unknown map');
  const score = scoreOf(matchDoc);
  if (score) parts.push(`${score.team0}–${score.team1}`);
  const max = roundsMax();
  if (max) parts.push(`${max} rounds`);
  if (matchDoc && matchDoc.patch) parts.push(`patch ${matchDoc.patch}`);
  return parts.join(' · ');
}

function buildFindingsCard() {
  const list = el('ol', { class: 'an__findinglist' });
  const count = el('span', { class: 'an__count', text: '0' });
  const copy = el('button', {
    type: 'button', class: 'btn', 'data-testid': 'analysis-export-copy', text: 'Export findings', disabled: !analysis,
    onclick: () => copyFindings(),
  });
  const png = el('button', {
    type: 'button', class: 'btn', 'data-testid': 'analysis-export-png', text: 'Save heatmap', disabled: true,
    onclick: () => savePng(),
  });
  const actions = el('div', { class: 'an__export-actions', 'data-testid': 'analysis-exports' }, copy, png);
  document.querySelector('#context-actions')?.append(actions);
  return el('section', { class: 'card an__findings', 'data-testid': 'analysis-findings', 'aria-labelledby': 'an-find-title' },
    el('header', { class: 'an__cardhead' },
      el('div', null,
        el('h2', { class: 'an__heading', id: 'an-find-title', text: 'Findings' }),
        el('p', { class: 'an__sub', text: 'Deterministic moments recorded in the replay.' })),
      count),
    list);
}

function buildDetailCard() {
  const tabs = [['utility', 'Utility'], ['identity', 'Players'], ['review', 'AI review']];
  const tablist = el('div', { class: 'tabs an__tabs', role: 'tablist', 'aria-label': 'Replay detail' },
    tabs.map(([key, label], index) => el('button', {
      type: 'button', role: 'tab', id: `an-tab-${key}`, 'data-panel': key,
      'aria-controls': `an-panel-${key}`, 'aria-selected': String(index === 0),
      tabindex: index === 0 ? '0' : '-1', text: label,
    })));
  return el('section', { class: 'card an__details', 'aria-label': 'Replay detail' },
    tablist,
    el('div', { class: 'an__tabpanels' },
      el('div', { id: 'an-panel-utility', role: 'tabpanel', 'aria-labelledby': 'an-tab-utility', 'data-testid': 'analysis-utility' }),
      el('div', { id: 'an-panel-identity', role: 'tabpanel', 'aria-labelledby': 'an-tab-identity', 'data-testid': 'analysis-identity', hidden: true }),
      el('div', { id: 'an-panel-review', role: 'tabpanel', 'aria-labelledby': 'an-tab-review', 'data-testid': 'analysis-review', hidden: true })));
}

function setupTabs(card) {
  const tabs = [...card.querySelectorAll('[role="tab"]')];
  const select = (tab, focus) => {
    for (const other of tabs) {
      const active = other === tab;
      other.setAttribute('aria-selected', String(active));
      other.tabIndex = active ? 0 : -1;
      const panel = card.querySelector(`#${other.getAttribute('aria-controls')}`);
      if (panel) panel.hidden = !active;
    }
    if (focus) tab.focus();
  };
  for (const tab of tabs) {
    tab.addEventListener('click', () => select(tab, false));
    tab.addEventListener('keydown', (event) => {
      const index = tabs.indexOf(tab);
      let next = null;
      if (event.key === 'ArrowRight') next = tabs[(index + 1) % tabs.length];
      else if (event.key === 'ArrowLeft') next = tabs[(index + tabs.length - 1) % tabs.length];
      else if (event.key === 'Home') next = tabs[0];
      else if (event.key === 'End') next = tabs[tabs.length - 1];
      if (next) { event.preventDefault(); select(next, true); }
    });
  }
}

function buildLower() {
  return el('div', { class: 'an__lower' },
    el('section', { class: 'card an__tablecard', 'aria-labelledby': 'an-table-title' },
      el('header', { class: 'an__cardhead' },
        el('div', null,
          el('h2', { class: 'an__heading', id: 'an-table-title', text: 'Round outcomes' }),
          el('p', { class: 'an__sub', text: 'Click a row to open that round in Replay.' }))),
      el('div', { class: 'an__tablescroll' },
        el('table', { class: 'table an__table', 'data-testid': 'analysis-table' },
          el('thead', null, el('tr', { id: 'an-thead' })),
          el('tbody', { id: 'an-tbody' })))),
    el('section', { class: 'card', 'data-testid': 'analysis-chart-economy', 'aria-labelledby': 'an-econ-title' },
      el('header', { class: 'an__cardhead' },
        el('div', null,
          el('h2', { class: 'an__heading', id: 'an-econ-title', text: 'Economy' }),
          el('p', { class: 'an__sub', text: 'Credits by round, when the replay records them.' }))),
      el('div', { class: 'an__chart', id: 'an-economy' })),
    el('section', { class: 'card', 'data-testid': 'analysis-chart-duels', 'aria-labelledby': 'an-duel-title' },
      el('header', { class: 'an__cardhead' },
        el('div', null,
          el('h2', { class: 'an__heading', id: 'an-duel-title', text: 'Duel matrix' }),
          el('p', { class: 'an__sub', id: 'an-duel-sub', text: 'Kills by killer (row) → victim (column).' }))),
      el('div', { class: 'an__matrixscroll', id: 'an-duels' })));
}

// ---------------------------------------------------------------- controls

function syncControls() {
  if (!rootEl) return;
  const byId = (id) => rootEl.querySelector(`#${id}`);
  const layerSelect = byId('an-layer');
  if (layerSelect) layerSelect.value = state.layer;
  const playerSelect = byId('an-player');
  if (playerSelect) playerSelect.value = state.player;
  const teamSelect = byId('an-team');
  if (teamSelect) teamSelect.value = state.team;
  const singleSelect = byId('an-round');
  if (singleSelect) singleSelect.value = String(state.single);
  const fromInput = byId('an-from');
  if (fromInput) fromInput.value = String(state.from);
  const toInput = byId('an-to');
  if (toInput) toInput.value = String(state.to);

  rootEl.querySelectorAll('[data-mode]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.mode === state.roundMode));
  });
  rootEl.querySelectorAll('[data-layer-switch]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.layerSwitch === state.layer));
  });
  const rangeBox = rootEl.querySelector('.an__range');
  if (rangeBox) rangeBox.hidden = state.roundMode !== 'range';
  const singleBox = rootEl.querySelector('.an__single');
  if (singleBox) singleBox.hidden = state.roundMode !== 'single';
}

function setFilterGroup(group) {
  if (!filtersEl) return;
  filtersEl.dataset.active = group;
  filtersEl.querySelectorAll('[data-group-tab]').forEach((button) => {
    button.setAttribute('aria-pressed', String(button.dataset.groupTab === group));
  });
}

function setupNarrowFilters() {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
  narrowQuery = window.matchMedia('(max-width: 900px)');
  narrowHandler = () => { if (!narrowQuery.matches && filtersEl) filtersEl.dataset.active = 'layer'; };
  if (narrowQuery.addEventListener) narrowQuery.addEventListener('change', narrowHandler);
}

function setLayer(layerId) {
  if (!LAYERS.some((layer) => layer.id === layerId)) return;
  state.layer = layerId;
  syncControls();
  persistFilters();
  renderAll();
}

function setRoundMode(mode) {
  if (!['all', 'range', 'single'].includes(mode)) return;
  state.roundMode = mode;
  syncControls();
  persistFilters();
  renderAll();
}

function setRange(from, to) {
  const max = Math.max(1, roundsMax());
  let start = Number.isFinite(from) ? Math.trunc(from) : state.from;
  let end = Number.isFinite(to) ? Math.trunc(to) : state.to;
  start = Math.min(max, Math.max(1, start));
  end = Math.min(max, Math.max(1, end));
  if (end < start) end = start;
  state.from = start;
  state.to = end;
  syncControls();
  persistFilters();
  renderAll();
}

function setSingle(value) {
  const max = Math.max(1, roundsMax());
  const n = Number.isFinite(value) ? Math.trunc(value) : state.single;
  state.single = Math.min(max, Math.max(1, n));
  syncControls();
  persistFilters();
  renderAll();
}

function setPlayer(value) {
  state.player = roster().some((player) => player.id === value) ? value : '';
  syncControls();
  persistFilters();
  renderAll();
}

function setTeam(value) {
  state.team = value === '0' || value === '1' ? value : '';
  syncControls();
  persistFilters();
  renderAll();
}

function resetFilters() {
  const max = Math.max(1, roundsMax());
  Object.assign(state, { layer: 'positions', roundMode: 'all', from: 1, to: max, single: 1, player: '', team: '' });
  syncControls();
  persistFilters();
  renderAll();
  toast('Filters reset.', 'info');
}

function saveView(input) {
  const name = (input.value || '').trim().slice(0, 60) || `${layerLabel(state.layer)} · ${roundLabelText()}`;
  const views = readSavedViews();
  views.unshift({ id: `view-${Date.now().toString(36)}`, name, filters: filtersToJSON() });
  ctxRef.state.set('savedViews', views.slice(0, 20));
  input.value = '';
  renderSavedViews();
  toast(`Saved view “${name}”.`, 'success');
}

function renderSavedViews() {
  const list = rootEl && rootEl.querySelector('.an__views-list');
  if (!list) return;
  const views = readSavedViews();
  list.replaceChildren();
  if (!views.length) {
    list.append(el('li', { class: 'an__views-empty', text: 'No saved views yet.' }));
    return;
  }
  for (const view of views) {
    list.append(el('li', { class: 'an__saved' },
      el('button', {
        type: 'button', class: 'an__saved-apply', text: view.name,
        'aria-label': `Apply view ${view.name}`,
        onclick: () => applyView(view),
      }),
      el('button', {
        type: 'button', class: 'icon-btn an__saved-del', text: '×',
        'aria-label': `Delete view ${view.name}`,
        onclick: () => deleteView(view),
      })));
  }
}

function applyView(view) {
  Object.assign(state, sanitizeFilters(view.filters));
  syncControls();
  persistFilters();
  renderAll();
  toast(`Applied “${view.name}”.`, 'info');
}

async function deleteView(view) {
  const ok = await ctxRef.ui.confirm({
    title: 'Delete saved view',
    body: `Delete “${view.name}”? The filters stay where they are.`,
    confirmLabel: 'Delete',
    danger: true,
  });
  if (!ok || !live) return;
  ctxRef.state.set('savedViews', readSavedViews().filter((entry) => entry.id !== view.id));
  renderSavedViews();
  toast(`Deleted “${view.name}”.`, 'info');
}

// ---------------------------------------------------------------- heatmap

function renderAll() {
  if (!live || !rootEl) return;
  renderMapSubtitle();
  drawHeat();
  renderFindings();
  renderTable();
  renderEconomy();
  renderDuels();
}

function renderMapSubtitle() {
  const node = rootEl.querySelector('#an-map-sub');
  if (node) node.textContent = mapSubtitle();
}

function currentCells() {
  return collectCells(analysis && analysis.heat, { layer: state.layer, round: currentRoundFilter() });
}

function drawHeat() {
  if (!live || !rootEl) return;
  const stage = rootEl.querySelector('.an__stage');
  if (!stage) return;
  const mapCanvas = stage.querySelector('.an__canvas--map');
  const heatCanvas = stage.querySelector('.an__canvas--heat');
  if (!mapCanvas || !heatCanvas) return;
  const bounds = stage.getBoundingClientRect();
  const width = Math.max(1, Math.round(bounds.width));
  const height = Math.max(1, Math.round(bounds.height));
  if (bounds.width === 0 || bounds.height === 0) return;
  const ratio = Math.min(window.devicePixelRatio || 1, 2);
  for (const canvas of [mapCanvas, heatCanvas]) {
    canvas.width = Math.round(width * ratio);
    canvas.height = Math.round(height * ratio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }
  const background = mapCanvas.getContext('2d');
  const overlay = heatCanvas.getContext('2d');
  if (!background || !overlay) return;
  background.setTransform(ratio, 0, 0, ratio, 0, 0);
  overlay.setTransform(ratio, 0, 0, ratio, 0, 0);

  const side = Math.max(1, Math.min(width - 24, height - 24));
  const fit = { x: (width - side) / 2, y: (height - side) / 2, side };

  background.fillStyle = '#0C121B';
  background.fillRect(0, 0, width, height);
  background.fillStyle = '#1C2839';
  background.fillRect(fit.x, fit.y, side, side);
  if (mapReady && mapImage && mapImage.complete && mapImage.naturalWidth) {
    background.globalAlpha = 0.82;
    background.drawImage(mapImage, fit.x, fit.y, side, side);
    background.globalAlpha = 1;
  }
  background.strokeStyle = '#26344A';
  background.lineWidth = 1;
  background.strokeRect(fit.x + 0.5, fit.y + 0.5, side - 1, side - 1);

  const cells = currentCells();
  const summary = summarizeCells(cells);
  lastCells = cells;
  lastSummary = summary;
  const cellSize = Math.max(1, (CELL_SIZE / MAP_SIZE) * fit.side);
  overlay.clearRect(0, 0, width, height);
  for (const [key, count] of cells) {
    const [x, y] = key.split(',').map(Number);
    overlay.fillStyle = cellFill(state.layer, count, summary.maximum);
    overlay.fillRect(fit.x + (x / MAP_SIZE) * fit.side, fit.y + (y / MAP_SIZE) * fit.side, cellSize, cellSize);
  }
  renderLegend(summary);
  const empty = stage.querySelector('.an__mapempty');
  if (empty) {
    empty.hidden = cells.size !== 0;
    const title = empty.querySelector('.an__mapempty-title');
    if (title) title.textContent = `No ${layerLabel(state.layer).toLowerCase()} recorded`;
    const body = empty.querySelector('p');
    if (body) body.textContent = `The replay did not record this data for ${roundLabelText().toLowerCase()}.`;
  }
  const png = document.querySelector('[data-testid="analysis-export-png"]');
  if (png) png.disabled = !analysis;
  heatCanvas.setAttribute('aria-label', cells.size
    ? `${layerLabel(state.layer)} heatmap, ${roundLabelText().toLowerCase()}, peak cell ${summary.maximum} samples`
    : `${layerLabel(state.layer)} heatmap, no data recorded for ${roundLabelText().toLowerCase()}`);
}

function renderLegend(summary) {
  const legend = rootEl.querySelector('[data-testid="analysis-legend"]');
  if (!legend) return;
  const layerName = layerLabel(state.layer);
  const peakText = summary.maximum > 0 && summary.peak
    ? `${summary.maximum} sample${summary.maximum === 1 ? '' : 's'} · cell ${summary.peak.x},${summary.peak.y}`
    : 'none';
  const meta = el('dl', { class: 'an__legend-meta' },
    el('div', null, el('dt', { text: 'Layer' }), el('dd', { text: layerName, 'data-testid': 'analysis-legend-layer' })),
    el('div', null, el('dt', { text: 'Cell size' }), el('dd', { text: '32 × 32 minimap px', 'data-testid': 'analysis-legend-cell' })),
    el('div', null, el('dt', { text: 'Peak cell' }), el('dd', { text: peakText, 'data-testid': 'analysis-legend-peak' })));
  const bands = el('ol', { class: 'an__legend-bands', 'aria-label': 'Cell counts per intensity band', 'data-testid': 'analysis-legend-bands' });
  const rgb = layerRgb(state.layer);
  summary.bands.forEach((band) => {
    const low = band.low;
    const high = band.high;
    const range = low == null ? '–' : (low === high ? `${low}` : `${low}–${high}`);
    const alpha0 = (0.18 + 0.66 * band.index / BAND_COUNT).toFixed(3);
    const alpha1 = (0.18 + 0.66 * (band.index + 1) / BAND_COUNT).toFixed(3);
    const swatch = el('span', { class: 'an__legend-swatch', 'aria-hidden': 'true' });
    swatch.style.background = `linear-gradient(90deg, rgba(${rgb},${alpha0}), rgba(${rgb},${alpha1}))`;
    bands.append(el('li', { class: 'an__legend-band', title: `${band.cells} cells with ${range} samples` },
      swatch,
      el('span', { class: 'an__legend-range', text: `${range} samples` }),
      el('span', { class: 'an__legend-n', text: `${band.cells} cell${band.cells === 1 ? '' : 's'}` })));
  });
  if (!summary.cells) {
    bands.replaceChildren(el('li', { class: 'an__legend-empty', text: 'No cells for this selection' }));
  }
  legend.replaceChildren(meta, bands);
}

// ---------------------------------------------------------------- findings

function filteredFindings() {
  const list = Array.isArray(analysis && analysis.findings) ? analysis.findings : [];
  const filter = currentRoundFilter();
  const players = roster();
  return list.filter((finding) => finding && roundMatches(Number(finding.round), filter)
    && findingMatches(finding, { player: state.player, team: state.team, players }));
}

function renderFindings() {
  if (!rootEl) return;
  const card = rootEl.querySelector('[data-testid="analysis-findings"]');
  if (!card) return;
  const list = card.querySelector('.an__findinglist');
  const count = card.querySelector('.an__count');
  const all = Array.isArray(analysis && analysis.findings) ? analysis.findings.length : 0;
  const findings = filteredFindings();
  if (count) count.textContent = findings.length === all ? String(all) : `${findings.length}/${all}`;
  list.replaceChildren();
  if (!findings.length) {
    list.append(el('li', { class: 'an__emptyfindings' },
      el('p', { text: all ? 'No findings match these filters.' : 'No findings recorded for this replay.' }),
      el('p', { class: 'an__sub', text: all ? 'Widen the round range, player or team filter.' : 'The replay did not record any key moments.' })));
    return;
  }
  for (const finding of findings) {
    const severity = String(finding.severity || 'info');
    const safeSeverity = SEVERITY_CLASSES.has(severityClass(severity)) ? severityClass(severity) : 'info';
    const round = Number.isFinite(Number(finding.round)) ? `R${finding.round}` : 'R?';
    const seconds = Number.isFinite(Number(finding.tMs)) && Number(finding.tMs) > 0 ? Number(finding.tMs) / 1000 : 0;
    list.append(el('li', { class: `an__finding an__finding--${safeSeverity}` },
      el('div', { class: 'an__findingmeta' },
        el('span', { class: 'chip an__sev', text: severity }),
        el('span', { class: 'an__round', text: round }),
        el('span', { class: 'an__time', text: formatOffset(Number(finding.tMs)) })),
      el('h3', { class: 'an__findingtitle', text: finding.title || 'Finding' }),
      finding.detail ? el('p', { class: 'an__findingdetail', text: finding.detail }) : null,
      el('a', {
        class: 'btn btn--ghost an__jump', href: momentUrl(finding.round, seconds),
        'data-testid': 'finding-jump', text: 'Jump to moment',
        'aria-label': `Jump to ${round} at ${formatOffset(Number(finding.tMs))}`,
      })));
  }
}

// ---------------------------------------------------------------- table

function renderTable() {
  if (!rootEl) return;
  const head = rootEl.querySelector('#an-thead');
  const body = rootEl.querySelector('#an-tbody');
  if (!head || !body) return;
  const team = state.team === '' ? null : Number(state.team);
  const columns = ['Round', 'Sides', 'Winner', 'End', 'Length'];
  if (team != null) columns.push(`${teamLabel(team)} result`);
  head.replaceChildren(...columns.map((label) => el('th', { scope: 'col', text: label })));

  const filter = currentRoundFilter();
  const rounds = Array.isArray(matchDoc && matchDoc.rounds) ? matchDoc.rounds : [];
  const rows = rounds.filter((round) => round && roundMatches(Number(round.n), filter));
  body.replaceChildren();
  if (!rows.length) {
    body.append(el('tr', null, el('td', { colspan: String(columns.length), class: 'an__tdnote', text: 'No rounds recorded for this selection.' })));
    return;
  }
  for (const round of rows) {
    const n = Number(round.n);
    const link = momentUrl(n, 0);
    const attacker = round.attackerTeam === 0 || round.attackerTeam === 1 ? round.attackerTeam : null;
    const defender = attacker == null ? null : 1 - attacker;
    const sides = attacker == null ? 'Unknown' : `${teamLabel(attacker)} ATK · ${teamLabel(defender)} DEF`;
    const winner = round.winner === 0 || round.winner === 1 ? round.winner : null;
    const length = Number.isFinite(Number(round.startMs)) && Number.isFinite(Number(round.endMs))
      ? formatDuration(Number(round.endMs) - Number(round.startMs)) : '—';
    const cells = [
      el('td', null, el('a', { class: 'an__roundlink', href: link, text: `R${n}` })),
      el('td', { text: sides }),
      el('td', null, el('span', {
        class: `an__winbadge ${winner == null ? '' : (winner === 0 ? 'an__winbadge--a' : 'an__winbadge--b')}`,
        text: winner == null ? 'Unknown' : teamLabel(winner),
      })),
      el('td', { text: round.endType || 'Unknown' }),
      el('td', { text: length }),
    ];
    if (team != null) {
      const result = winner == null ? '—' : (winner === team ? 'Win' : 'Loss');
      cells.push(el('td', null, el('span', {
        class: `an__result ${result === 'Win' ? 'an__result--win' : result === 'Loss' ? 'an__result--loss' : ''}`,
        text: result,
      })));
    }
    const row = el('tr', { class: 'an__roundrow', 'data-round': String(n) }, cells);
    row.addEventListener('click', (event) => {
      if (event.target.closest('a')) return;
      ctxRef.navigate(link);
    });
    row.title = `Open round ${n} in Replay`;
    body.append(row);
  }
}

// ---------------------------------------------------------------- economy

function renderEconomy() {
  if (!rootEl) return;
  const host = rootEl.querySelector('#an-economy');
  if (!host) return;
  const series = economySeries(analysis, matchDoc);
  host.replaceChildren();
  if (!series) {
    host.append(unavailableCard('Not recorded in this replay',
      'The analysis payload carries no per-round economy field, so no values are shown.'));
    return;
  }
  const width = 760;
  const height = 220;
  const margin = { top: 16, right: 12, bottom: 30, left: 46 };
  const plotW = width - margin.left - margin.right;
  const plotH = height - margin.top - margin.bottom;
  const max = Math.max(1, ...series.rounds.flatMap((row) => [row.team0 || 0, row.team1 || 0]));
  const step = plotW / Math.max(1, series.rounds.length);
  const barW = Math.max(2, step * 0.3);
  const root = svgEl('svg', {
    class: 'an__svg', viewBox: `0 0 ${width} ${height}`, role: 'img',
    'aria-label': `Economy by round for ${series.rounds.length} rounds`,
  });
  const axis = (x1, y1, x2, y2) => svgEl('line', { x1, y1, x2, y2, stroke: '#26344A', 'stroke-width': '1' });
  root.append(axis(margin.left, margin.top, margin.left, margin.top + plotH),
    axis(margin.left, margin.top + plotH, margin.left + plotW, margin.top + plotH));
  for (const fraction of [0, 0.5, 1]) {
    const y = margin.top + plotH * (1 - fraction);
    root.append(svgEl('text', { x: margin.left - 8, y: y + 4, 'text-anchor': 'end', fill: '#8591A6', 'font-size': '10' },
      String(Math.round(max * fraction))));
  }
  series.rounds.forEach((row, index) => {
    const x = margin.left + step * index + step * 0.1;
    const entry = svgEl('g', null);
    entry.append(svgEl('title', null,
      `Round ${row.round} · ${teamLabel(0)} ${row.team0 == null ? '—' : row.team0} · ${teamLabel(1)} ${row.team1 == null ? '—' : row.team1}`));
    if (row.team0 != null) {
      entry.append(svgEl('rect', {
        x, y: margin.top + plotH - (row.team0 / max) * plotH, width: barW,
        height: Math.max(0, (row.team0 / max) * plotH), fill: '#9B7BFF', rx: '1',
      }));
    }
    if (row.team1 != null) {
      entry.append(svgEl('rect', {
        x: x + barW + 2, y: margin.top + plotH - (row.team1 / max) * plotH, width: barW,
        height: Math.max(0, (row.team1 / max) * plotH), fill: '#38BDF8', rx: '1',
      }));
    }
    entry.append(svgEl('text', { x: x + barW, y: margin.top + plotH + 14, 'text-anchor': 'middle', fill: '#8591A6', 'font-size': '9' }, `R${row.round}`));
    root.append(entry);
  });
  host.append(el('div', { class: 'an__econlegend' },
    el('span', { class: 'an__key an__key--a', text: teamLabel(0) }),
    el('span', { class: 'an__key an__key--b', text: teamLabel(1) }),
    el('span', { class: 'an__sub', text: series.unit ? `unit: ${series.unit}` : 'values as reported' })), root);
}

// ---------------------------------------------------------------- duels

function renderDuels() {
  if (!rootEl) return;
  const host = rootEl.querySelector('#an-duels');
  if (!host) return;
  const duels = duelsSeries(analysis);
  host.replaceChildren();
  if (!duels) {
    const sub = rootEl.querySelector('#an-duel-sub');
    if (sub) sub.textContent = 'Kills by killer (row) → victim (column).';
    host.append(unavailableCard('Not recorded in this replay',
      'The analysis payload carries no duel matrix, so no pairings are shown.'));
    return;
  }
  const { players, matrix } = duels;
  const ordered = players
    .map((player, index) => ({ player, index }))
    .sort((a, b) => {
      const teamA = a.player.team == null ? 9 : a.player.team;
      const teamB = b.player.team == null ? 9 : b.player.team;
      if (teamA !== teamB) return teamA - teamB;
      return a.player.id.localeCompare(b.player.id, undefined, { numeric: true });
    });
  const order = ordered.map((entry) => entry.index);
  let maximum = 0;
  let total = 0;
  for (const row of matrix) for (const value of row) { if (value > maximum) maximum = value; total += value; }
  const cell = 28;
  const labelW = 72;
  const top = 52;
  const width = labelW + order.length * cell + 8;
  const height = top + order.length * 24 + 8;
  const root = svgEl('svg', {
    class: 'an__svg an__matrix', viewBox: `0 0 ${width} ${height}`,
    role: 'img', 'aria-label': `Duel matrix: ${total} kills across ${order.length} players`,
  });
  root.append(svgEl('title', null, 'Kills by killer (row) against victim (column).'));
  order.forEach((columnIndex, column) => {
    const player = players[columnIndex];
    const x = labelW + column * cell + cell / 2;
    root.append(svgEl('text', {
      x, y: top - 26, 'text-anchor': 'middle', fill: player.team === 1 ? '#38BDF8' : '#9B7BFF',
      'font-size': '9', 'font-weight': '600',
    }, player.id));
    root.append(svgEl('text', { x, y: top - 14, 'text-anchor': 'middle', fill: '#6F7C91', 'font-size': '8' },
      (player.agent || '').slice(0, 9)));
  });
  order.forEach((rowIndex, row) => {
    const player = players[rowIndex];
    const y = top + row * 24;
    root.append(svgEl('text', { x: labelW - 8, y: y + 16, 'text-anchor': 'end', fill: '#E7ECF5', 'font-size': '10' },
      `${player.id}${player.agent ? ` · ${player.agent}` : ''}`));
    order.forEach((columnIndex, column) => {
      const value = matrix[rowIndex] && matrix[rowIndex][columnIndex] ? matrix[rowIndex][columnIndex] : 0;
      const x = labelW + column * cell;
      const highlighted = !!state.player && (player.id === state.player || players[columnIndex].id === state.player);
      const rect = svgEl('rect', {
        x, y, width: cell - 2, height: 22, rx: '3',
        fill: value ? `rgba(155,123,255,${(0.16 + 0.7 * Math.sqrt(value / Math.max(1, maximum))).toFixed(2)})` : '#16202F',
        stroke: highlighted ? '#9B7BFF' : '#26344A',
        'stroke-width': highlighted ? '1' : '0.5',
      });
      rect.append(svgEl('title', null, `${player.id} → ${players[columnIndex].id}: ${value} kill${value === 1 ? '' : 's'}`));
      root.append(rect);
      if (value) {
        root.append(svgEl('text', {
          x: x + (cell - 2) / 2, y: y + 15, 'text-anchor': 'middle', fill: '#E7ECF5', 'font-size': '10',
          'font-family': 'Cascadia Mono, Consolas, monospace',
        }, String(value)));
      }
    });
  });
  const sub = rootEl.querySelector('#an-duel-sub');
  if (sub) sub.textContent = `Kills by killer (row) → victim (column). ${total} kill${total === 1 ? '' : 's'} from the analysis payload.`;
  host.append(root);
}

// ---------------------------------------------------------------- utility panel

function renderUtility() {
  if (!rootEl) return;
  const host = rootEl.querySelector('[data-testid="analysis-utility"]');
  if (!host) return;
  const counts = matchDoc && matchDoc.utilityCounts;
  if (!counts || typeof counts !== 'object' || !Object.keys(counts).length) {
    host.replaceChildren(unavailableCard('Utility usage not recorded in this replay',
      'The match payload carries no per-player ability counts.'));
    return;
  }
  const players = roster();
  const slots = players.length ? players.map((player) => player.id)
    : Object.keys(counts).sort((a, b) => a.localeCompare(b, undefined, { numeric: true }));
  const rows = slots.map((slot) => {
    const entry = counts[slot] && typeof counts[slot] === 'object' ? counts[slot] : {};
    const abilities = Object.entries(entry.abilities && typeof entry.abilities === 'object' ? entry.abilities : {})
      .filter(([, count]) => Number.isFinite(Number(count)) && Number(count) > 0)
      .sort((a, b) => Number(b[1]) - Number(a[1]));
    const full = abilities.map(([kind, count]) => `${kind} ×${count}`).join(' · ') || 'No utility recorded';
    const agent = typeof entry.agent === 'string' ? entry.agent : (players.find((player) => player.id === slot) || {}).agent || '';
    return el('li', { class: 'an__utilrow', title: full, 'aria-label': `${slot}, ${agent || 'agent unknown'}, total ${Number(entry.total) || 0}. ${full}` },
      el('span', { class: 'an__utilname', text: slot }),
      el('span', { class: 'an__utilagent', text: agent || '—' }),
      el('span', { class: 'an__utiltotal', text: String(Number(entry.total) || 0) }),
      el('span', { class: 'an__utilbreak', text: abilities.slice(0, 4).map(([kind, count]) => `${kind} ×${count}`).join(' · ') || 'No utility recorded' }));
  });
  host.replaceChildren(
    el('div', { class: 'an__utilhead' },
      el('span', { text: 'Player' }), el('span', { text: 'Agent' }), el('span', { text: 'Uses' }), el('span', { text: 'Utility (hover for all)' })),
    el('ul', { class: 'an__utillist' }, rows));
}

// ---------------------------------------------------------------- identity panel

async function loadIdentity() {
  if (!rootEl) return;
  identityState = { data: null, message: 'Checking player identity…', busy: true, error: false };
  renderIdentity();
  try {
    const data = await ctxRef.api.get(`/api/replays/${encodeURIComponent(replayId)}/identity`);
    if (!live) return;
    identityState = { data, message: '', busy: false, error: false };
  } catch (error) {
    if (!live) return;
    identityState = { data: null, message: describeError(error), busy: false, error: true };
  }
  renderIdentity();
}

function renderIdentity() {
  if (!rootEl) return;
  const host = rootEl.querySelector('[data-testid="analysis-identity"]');
  if (!host) return;
  host.replaceChildren();
  if (identityState.busy) {
    host.append(el('p', { class: 'an__sub', role: 'status', text: identityState.message }));
    return;
  }
  if (identityState.error) {
    host.append(unavailableCard('Identity unavailable', identityState.message));
    return;
  }
  const data = identityState.data || {};
  const players = Array.isArray(data.players) ? data.players : [];
  host.append(el('p', { class: 'an__sub', role: 'status', text: data.available
    ? `Identity matched${data.refreshedAt ? ` · updated ${data.refreshedAt}` : ''}.`
    : `Identity unavailable: ${data.reason || 'no verified match found'}. Replay slots remain visible.` }));
  if (!players.length) {
    host.append(el('p', { class: 'an__sub', text: 'No player names to show. Replay slots only.' }));
    return;
  }
  host.append(el('ul', { class: 'an__identitylist' }, players.map((player) => {
    const display = (player && (player.riotId || player.displayName || player.slot)) || 'Unknown slot';
    const confidence = player && typeof player.confidence === 'string' ? player.confidence : 'unmatched';
    return el('li', { class: 'an__identityrow' },
      el('span', { class: 'an__identityslot', text: player && player.slot ? String(player.slot) : '—' }),
      el('span', { class: 'an__identityname', text: String(display) }),
      el('span', { class: 'chip an__identityconf', text: confidence }));
  })));
}

// ---------------------------------------------------------------- AI review panel

async function loadReview() {
  if (!rootEl) return;
  reviewState = { data: null, message: 'Checking for a saved review…', busy: true, error: false };
  renderReview();
  try {
    const data = await ctxRef.api.get(`/api/replays/${encodeURIComponent(replayId)}/review`);
    if (!live) return;
    reviewState = { data, message: '', busy: false, error: false };
  } catch (error) {
    if (!live) return;
    if (error && error.status === 404) reviewState = { data: null, message: 'No saved review for this replay.', busy: false, error: false };
    else reviewState = { data: null, message: describeError(error), busy: false, error: true };
  }
  renderReview();
}

async function generateReview() {
  if (!live || reviewState.busy) return;
  reviewState = { data: reviewState.data, message: 'Waiting for a local model…', busy: true, error: false };
  renderReview();
  try {
    const data = await ctxRef.api.post(`/api/replays/${encodeURIComponent(replayId)}/review`, { backend: 'auto' });
    if (!live) return;
    reviewState = { data, message: '', busy: false, error: false };
  } catch (error) {
    if (!live) return;
    const message = error && error.status === 503
      ? 'No local LLM reachable. Start LM Studio or Ollama and try again.'
      : describeError(error);
    reviewState = { data: null, message, busy: false, error: true };
  }
  renderReview();
}

function renderReview() {
  if (!rootEl) return;
  const host = rootEl.querySelector('[data-testid="analysis-review"]');
  if (!host) return;
  host.replaceChildren();
  host.append(el('p', { class: 'an__sub', text: 'Optional local-model review of the replay summary. Separate from Coach.' }));
  const status = el('p', { class: 'an__sub', role: 'status', text: reviewState.busy ? reviewState.message : (reviewState.message || '') });
  const generate = el('button', {
    type: 'button', class: 'btn', text: reviewState.busy ? 'Generating…' : 'Generate review',
    disabled: reviewState.busy, onclick: () => generateReview(),
  });
  host.append(el('div', { class: 'an__reviewbar' }, generate, status));
  if (reviewState.error && !reviewState.data) return;
  const review = reviewState.data;
  if (!review) return;
  host.append(el('p', { class: 'an__reviewmeta', text: `${review.backend || 'Local model'} · ${review.model || 'default model'}${review.generatedAt ? ` · ${review.generatedAt}` : ''}` }));
  host.append(el('p', { class: 'an__reviewsummary', text: review.summary || 'Review generated.' }));
  const findings = Array.isArray(review.findings) ? review.findings : [];
  if (!findings.length) {
    host.append(el('p', { class: 'an__sub', text: 'No findings in this review.' }));
    return;
  }
  host.append(el('ol', { class: 'an__reviewlist' }, findings.map((finding) => el('li', { class: 'an__reviewfinding' },
    el('strong', { text: `${Number.isFinite(Number(finding.round)) ? `R${finding.round} · ` : ''}${finding.title || 'Finding'}` }),
    finding.detail ? el('span', { class: 'an__sub', text: finding.detail }) : null))));
}

// ---------------------------------------------------------------- exports

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = el('a', { href: url, download: filename });
  document.body.append(link); link.click(); link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

function copyFindings() {
  if (!analysis) return;
  const cell = (value) => String(value ?? '—').replaceAll('|', '\\|').split(String.fromCharCode(10)).join(' ').split(String.fromCharCode(13)).join(' ');
  // Roster is stable for a replay; resolve it once for the whole export.
  const players = roster();
  const lines = [
    `# ValoHub findings — ${cell(matchDoc?.map?.name || 'Unknown map')} (${cell(replayId)})`, '',
    `Filters: ${layerLabel(state.layer)} · ${roundLabelText()} · player ${cell(state.player || 'All')} · team ${cell(state.team === '' ? 'Both' : teamLabel(Number(state.team)))}`, '',
    '| Severity | Round | Time | Player | Finding |', '| --- | --- | --- | --- | --- |',
    ...filteredFindings().map((f) => {
      const explicit = f.player?.name || f.player?.id || f.player || f.playerId;
      // Current findings identify players in prose, not in a player/playerId field.
      const text = [f.title, f.detail].filter(Boolean).join(' — ');
      // Use exact-token matches (P1 must not match P10), ordered as the prose reads.
      const firstMention = (p) => Math.min(...[p.id, p.name].filter(Boolean).map((token) => {
        const escaped = String(token).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
        const match = new RegExp(`(^|[^A-Za-z0-9_])${escaped}([^A-Za-z0-9_]|$)`).exec(text);
        return match ? match.index + match[1].length : Infinity;
      }));
      const mentioned = players.map((p) => ({ player: p, index: firstMention(p) }))
        .filter((m) => Number.isFinite(m.index)).sort((a, b) => a.index - b.index).map((m) => m.player);
      const player = explicit
        ? (players.find((p) => p.id === explicit)?.name || explicit)
        : mentioned.map((p) => p.name || p.id).join(', ');
      return `| ${cell(f.severity || 'info')} | ${cell(f.round)} | ${formatOffset(Number(f.tMs))} | ${cell(player || '—')} | ${cell(text)} |`;
    }), '',
    `Generated locally by ValoHub ${document.querySelector('#status-build .seg-text')?.textContent || 'dev'}`, '',
  ];
  downloadBlob(new Blob([lines.join('\n')], { type: 'text/markdown;charset=utf-8' }), `valohub-findings-${replayId}.md`);
  toast('Findings Markdown saved.', 'success');
}

function savePng() {
  if (!analysis || !lastCells) {
    toast('Nothing to export for this layer.', 'error');
    return;
  }
  const canvas = document.createElement('canvas');
  canvas.width = MAP_SIZE;
  canvas.height = MAP_SIZE + 128;
  const g = canvas.getContext('2d');
  if (!g) { toast('PNG export is not available.', 'error'); return; }
  g.fillStyle = '#0B0F16';
  g.fillRect(0, 0, canvas.width, canvas.height);
  g.fillStyle = '#0B0F16';
  g.fillRect(0, 0, MAP_SIZE, MAP_SIZE);
  if (mapReady && mapImage && mapImage.complete && mapImage.naturalWidth) {
    g.globalAlpha = 0.82;
    g.drawImage(mapImage, 0, 0, MAP_SIZE, MAP_SIZE);
    g.globalAlpha = 1;
  }
  for (const [key, count] of lastCells) {
    const [x, y] = key.split(',').map(Number);
    g.fillStyle = cellFill(state.layer, count, lastSummary.maximum);
    g.fillRect(x, y, CELL_SIZE, CELL_SIZE);
  }
  g.fillStyle = '#111725';
  g.fillRect(0, MAP_SIZE, MAP_SIZE, 128);
  g.fillStyle = '#E7ECF5';
  g.font = '600 15px "Segoe UI", system-ui, sans-serif';
  g.fillText(`${layerLabel(state.layer)} · ${roundLabelText()} · peak ${lastSummary.maximum} samples`, 16, MAP_SIZE + 27);
  g.fillStyle = '#A9B4C6';
  g.font = '12px "Segoe UI", system-ui, sans-serif';
  g.fillText(`ValoHub · replay ${replayId} · 32 × 32 px cells on a 1024 px projection`, 16, MAP_SIZE + 48);
  g.fillText(String(matchDoc?.map?.name || 'Unknown map'), 16, MAP_SIZE + 72);
  lastSummary.bands.forEach((band, i) => {
    g.fillStyle = cellFill(state.layer, (i + 1) ** 2, 25);
    g.fillRect(16 + i * 200, MAP_SIZE + 88, 24, 20);
    g.fillStyle = '#E7ECF5';
    g.fillText(`Band ${i + 1}: ${band.low ?? 0}–${band.high ?? 0}`, 48 + i * 200, MAP_SIZE + 103);
  });
  const filename = `valohub-heatmap-${replayId}-${state.layer}.png`;
  try {
    canvas.toBlob((blob) => {
      if (!blob) { toast('PNG export failed.', 'error'); return; }
      const url = URL.createObjectURL(blob);
      const link = el('a', { href: url, download: filename });
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 2000);
      toast('Heatmap PNG saved.', 'success');
    }, 'image/png');
  } catch {
    toast('The map image could not be exported as PNG.', 'error');
  }
}

// ---------------------------------------------------------------- image / errors

function loadMapImage() {
  const uuid = mapId(matchDoc && matchDoc.map && matchDoc.map.uuid);
  if (!uuid) return;
  const image = new Image();
  image.decoding = 'async';
  image.onload = () => { if (!live) return; mapReady = true; drawHeat(); };
  image.onerror = () => { if (!live) return; mapReady = false; drawHeat(); };
  image.src = `/replays-assets/maps/${uuid}.png`;
  mapImage = image;
}

function describeError(error) {
  if (!error) return 'The analysis could not be loaded.';
  if (error.status === 404) return 'This replay has no saved analysis yet. Re-import it from the Library, then retry.';
  if (error.status === 0) return 'Cannot reach the ValoHub backend. Check that the app is running, then retry.';
  if (error.status >= 500) return `The analysis service failed (${error.status}). Retry, or check the app log.`;
  return error.message || `Request failed${error.status ? ` (${error.status})` : ''}.`;
}

function renderError(error) {
  if (!rootEl || !ctxRef) return;
  teardownView();
  const notFound = error && error.status === 404;
  rootEl.replaceChildren(el('div', { class: 'an an--errorpage', 'data-testid': 'analysis' },
    el('section', { class: 'card an__errorcard', role: 'alert' },
      el('h2', { class: 'an__heading', text: notFound ? 'Replay not found' : 'Analysis unavailable' }),
      el('p', { class: 'an__sub', text: describeError(error) }),
      el('p', { class: 'an__sub an__sub--mono', text: replayId ? `Replay ${replayId}` : 'No replay selected' }),
      el('div', { class: 'an__actions' },
        el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'analysis-retry', text: 'Retry', onclick: retry }),
        el('a', { class: 'btn', href: '/replays', text: 'Back to Library' })))));
}
