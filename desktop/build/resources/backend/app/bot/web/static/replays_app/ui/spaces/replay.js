// ValoHub UI v2 — Replay workspace (`/replay/{id}`).
//
// Ported from the v1 engine in bot/web/static/replays_app/viewer.js + utility.js:
// canvas map fit/projection, sampled player tracks, nudged agent-portrait markers, trails,
// labels, HP bars, heading chevrons, utility paths/landings/areas, feed, round strip, transport.
//
// What is re-written rather than copied:
//   - no globals (except the opt-in v1 QA hooks), no window.UtilityLayer.draw, no window.onerror,
//     no innerHTML: the module owns a private state object and every replay-derived string is
//     written with textContent;
//   - deep links: `?r=&t=` (v2 shell contract) and `#r=&t=` (v1 links + hashchange) are both read;
//     both are written back on state changes through a 400 ms-throttled history.replaceState;
//   - sharing uses a portable text card (no ephemeral host/port), with a selectable modal fallback;
//   - all keys go through `ctx.shortcuts` (v1 listened on document);
//   - the inspector (Rounds/Events/Players/Coach) is data built per mount, not static HTML.
//
// Geometry calibration is intentionally identical to v1 (see resize()/projectPoint()):
//   side = max(1, min(w - 32, h - 32)); origin centred; px = x + (xScalarToAdd + y*xMultiplier)*side;
//   py = y + (yScalarToAdd + x*xMultiplier... swapped axes) — the world axes are swapped by contract.
//
// Testids owned by this module: replay, replay-header, replay-back, replay-map, replay-hud,
// replay-legend, replay-transport, replay-play, replay-prev, replay-next, replay-scrub,
// replay-ticks, replay-speed, replay-layers, replay-inspector, replay-tab-{rounds,events,players,coach},
// replay-rounds, replay-events, replay-players, replay-share, replay-bookmark.

const ASSET_BASE = '/static/replays_app/assets/';
const TEAM_A = '#65c9e8';
const TEAM_B = '#ff6874';
const NEUTRAL = '#9aa5ab';
const INK = '#09121b';
const ABILITY_PALETTE = ['#6de5c3', '#f4c46e', '#67bfff', '#f389ac', '#b59bff'];

// ---------------------------------------------------------------------------------------------
// Pure helpers (exported so `node` can unit-test the time/seek/projection math without a DOM).
// ---------------------------------------------------------------------------------------------

export function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

export function formatClock(ms) {
  const seconds = Math.max(0, Number(ms) || 0) / 1000;
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}.${Math.floor(seconds * 10) % 10}`;
}

export function roundDuration(round) {
  if (!round || !Number.isFinite(round.startMs) || !Number.isFinite(round.endMs)) return 0;
  return Math.max(0, round.endMs - round.startMs);
}

// v1 calibration: a square, 32px-inset fit centred in the stage.
export function computeMapFit(width, height) {
  const side = Math.max(1, Math.min(width - 32, height - 32));
  return { side, x: (width - side) / 2, y: (height - side) / 2 };
}

export function hasMapProjection(map) {
  if (!map) return false;
  return ['xMultiplier', 'yMultiplier', 'xScalarToAdd', 'yScalarToAdd'].every((key) => Number.isFinite(map[key]))
    && map.xMultiplier !== 0 && map.yMultiplier !== 0;
}

// World (x, y) -> canvas CSS px. The axes are swapped on purpose (v1 contract §2).
export function projectPoint(map, fit, x, y) {
  if (!hasMapProjection(map) || !Number.isFinite(x) || !Number.isFinite(y)) return null;
  return {
    x: fit.x + (map.xScalarToAdd + y * map.xMultiplier) * fit.side,
    y: fit.y + (map.yScalarToAdd + x * map.yMultiplier) * fit.side,
  };
}

// Linear sample of a normalized player track; missing samples are never interpolated across a gap
// and a stale point is dropped after 600 ms (exactly the v1 rule).
export function sampleTrack(track, tMs, tickMs, frames) {
  if (!track || !Array.isArray(track.x) || !Array.isArray(track.y)) return null;
  const ms = Number(tickMs);
  if (!Number.isFinite(ms) || ms <= 0) return null;
  const index = clamp(tMs / ms, 0, Math.max(0, Number(frames) - 1));
  const lo = Math.floor(index);
  const hi = Math.ceil(index);
  const alpha = index - lo;
  if (!Number.isFinite(track.x[lo]) || !Number.isFinite(track.y[lo])) return null;
  let next = hi;
  if (!Number.isFinite(track.x[hi]) || !Number.isFinite(track.y[hi])) next = lo;
  if (next === lo && tMs - lo * ms > 600) return null;
  const alive = track.alive?.[lo];
  if (alive === 0 || track.alive?.[next] === 0) return null;
  const x = track.x[lo] + (track.x[next] - track.x[lo]) * (next === lo ? 0 : alpha);
  const y = track.y[lo] + (track.y[next] - track.y[lo]) * (next === lo ? 0 : alpha);
  const yaw0 = track.yaw?.[lo];
  const yaw1 = track.yaw?.[next];
  let yaw = Number.isFinite(yaw0) ? yaw0 : null;
  if (yaw !== null && Number.isFinite(yaw1)) {
    const diff = ((yaw1 - yaw0 + 540) % 360) - 180;
    yaw += diff * (next === lo ? 0 : alpha);
  }
  const health = track.hp?.[lo];
  return { x, y, yaw, hp: Number.isFinite(health) ? clamp(health, 0, 100) : null };
}

export function seekBy(currentMs, deltaMs, durationMs) {
  return clamp((Number(currentMs) || 0) + (Number(deltaMs) || 0), 0, Math.max(0, Number(durationMs) || 0));
}

export function roundIndexFor(rounds, roundNumber) {
  if (!Array.isArray(rounds) || !Number.isFinite(roundNumber)) return -1;
  return rounds.findIndex((meta) => meta && meta.n === roundNumber);
}

export function deepLink(pathname, roundNumber, tMs) {
  const seconds = Math.max(0, Number(tMs) || 0) / 1000;
  return `${pathname}?r=${Number(roundNumber)}&t=${seconds.toFixed(1)}`;
}

// v1 wrote `#r=&t=`; v2 also exposes `?r=&t=`. Both are parsed, both are written, so either
// style of shared link (and the analysis/coach deep links) keeps working.
export function hashLink(roundNumber, tMs) {
  const seconds = Math.max(0, Number(tMs) || 0) / 1000;
  return `#r=${Number(roundNumber)}&t=${seconds.toFixed(1)}`;
}

export function parseMomentHash(hash) {
  const raw = String(hash || '').replace(/^#/, '');
  if (!raw) return { r: NaN, t: NaN };
  const params = new URLSearchParams(raw);
  const read = (key) => {
    const value = params.get(key);
    return value == null || value === '' ? NaN : Number(value);
  };
  return { r: read('r'), t: read('t') };
}

export function percentOf(tMs, durationMs) {
  const total = Number(durationMs) || 0;
  if (total <= 0) return 0;
  return clamp((Number(tMs) || 0) / total * 100, 0, 100);
}

// Utility throw clustering for the scrub lane: throws closer than `minPx` share one 24px button.
export function clusterThrows(items, durationMs, widthPx, minPx = 24) {
  const total = durationMs > 0 ? durationMs : 1;
  const width = Math.max(1, Number(widthPx) || 1);
  const sorted = (Array.isArray(items) ? items : [])
    .filter((item) => item && Number.isFinite(item.t) && item.t >= 0 && item.t <= total)
    .slice()
    .sort((a, b) => a.t - b.t);
  const clusters = [];
  for (const item of sorted) {
    const px = item.t / total * width;
    const last = clusters[clusters.length - 1];
    if (last && px - last.px < minPx) last.items.push(item);
    else clusters.push({ px, t: item.t, items: [item] });
  }
  return clusters;
}

export function teamLabelOf(team) {
  return team === 0 ? 'Team A' : team === 1 ? 'Team B' : '';
}

export function teamColorOf(team) {
  return team === 0 ? TEAM_A : team === 1 ? TEAM_B : NEUTRAL;
}

export function sideLabel(meta, team) {
  const base = teamLabelOf(team);
  if (!base || !meta) return base;
  if (meta.attackerTeam === team) return `${base} · ATK`;
  if (meta.attackerTeam === 1 - team) return `${base} · DEF`;
  return base;
}

export function outcomeLabel(meta) {
  if (!meta) return 'Unknown';
  const winner = teamLabelOf(meta.winner) || 'Unknown winner';
  return `${winner} · ${display(meta.endType, 'Unknown end')} · ${display(meta.confidence, 'unknown')} confidence`;
}

// ---------------------------------------------------------------------------------------------
// DOM helpers — mirrors the shell's el(); no innerHTML anywhere in this module.
// ---------------------------------------------------------------------------------------------

function h(tag, attrs, ...children) {
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
  for (const child of children.flat()) {
    if (child == null || child === false) continue;
    node.append(child.nodeType ? child : document.createTextNode(String(child)));
  }
  return node;
}

const display = (value, fallback = 'Unknown') => value === null || value === undefined || value === '' ? fallback : String(value);

// ---------------------------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------------------------

let st = null;

function createState(root, ctx) {
  const id = ctx.params && ctx.params.id ? String(ctx.params.id) : '';
  return {
    root, ctx, id, destroyed: false,
    match: null, byId: new Map(), assetIndex: {}, rounds: [],
    round: null, roundIndex: 0, elapsed: 0, playing: false, lastFrame: 0, speed: 1,
    raf: 0, dirty: false, resizeRaf: 0, urlTimer: 0, lastUrlAt: 0,
    generation: 0, roundCache: new Map(), loadingRounds: new Map(),
    projection: true, mapImage: null, mapFit: { x: 0, y: 0, side: 1 }, dpr: 1, view: { w: 1, h: 1 },
    deaths: new Map(), visibleMarkers: [], utilHits: [], sprites: new Map(), portraits: new Map(),
    abilityIcons: new Map(), widthCache: new Map(), artReady: false, rotate: false,
    utilStats: { paths: 0, landings: 0, areas: 0, bounces: 0, unowned: 0, tMs: 0, lastDrawMs: 0 },
    layers: { trails: true, abilities: true, paths: true, areas: true },
    layerInputs: {},
    filters: { player: '', team: '' },
    counts: { alive: { 0: 0, 1: 0, u: 0 }, total: { 0: 0, 1: 0, u: 0 } },
    ariaKey: '', ariaForce: false,
    labels: { placed: [], omitted: [], badges: [], states: [] },
    tab: 'rounds', collapsed: false, mapMax: false, coachLoaded: false,
    coachPlayer: '', coachPending: false, qaInstalled: false,
    feedBucket: -1, lastRoundN: 0,
    els: {}, tabs: [],
    listeners: new AbortController(),
    ro: null, mq: null,
  };
}

// ---------------------------------------------------------------------------------------------
// DOM construction
// ---------------------------------------------------------------------------------------------

function buildDom() {
  const els = st.els;

  const back = h('a', {
    class: 'btn btn--ghost replay__back', 'data-testid': 'replay-back', href: '/replays', 'aria-label': 'Back to Library',
  }, h('span', { 'aria-hidden': 'true' }, '‹'), ' Library');

  els.headerIdent = h('div', { class: 'replay__ident' },
    h('strong', { class: 'replay__map' }, 'Replay'),
    h('span', { class: 'replay__meta' }, ''));

  const switchTo = (path, label, current) => h('button', {
    type: 'button', class: 'replay__switch-btn', 'aria-pressed': current ? 'true' : 'false',
    onclick: () => { if (path) st.ctx.navigate(path); },
  }, label);
  const switcher = h('div', { class: 'segmented replay__switch', role: 'group', 'aria-label': 'Workspace' },
    switchTo(null, 'Replay', true),
    switchTo(`/analysis/${encodeURIComponent(st.id)}`, 'Analysis', false),
    switchTo(`/coach/${encodeURIComponent(st.id)}`, 'Coach', false));

  els.bookmark = h('button', {
    type: 'button', class: 'btn btn--ghost', 'data-testid': 'replay-bookmark', 'aria-pressed': 'false', disabled: true,
    title: 'Loading replay…',
    onclick: () => toggleBookmark(),
  }, 'Bookmark round');
  els.share = h('button', {
    type: 'button', class: 'btn', 'data-testid': 'replay-share', disabled: true, title: 'Loading replay…', onclick: () => shareMoment(),
  }, 'Share moment');

  const header = h('header', { class: 'replay__header', 'data-testid': 'replay-header' },
    back,
    els.headerIdent,
    switcher,
    h('div', { class: 'replay__actions' }, els.bookmark, els.share));
  els.header = header;

  // --- map stage -------------------------------------------------------------------------
  els.mapCanvas = h('canvas', { class: 'replay__canvas replay__canvas--map', 'aria-hidden': 'true' });
  els.actors = h('canvas', {
    class: 'replay__canvas replay__canvas--actors', role: 'img',
    'aria-label': 'Player positions and spike on map',
  });
  els.stage = h('section', { class: 'replay__stage', 'data-testid': 'replay-map' },
    els.mapCanvas, els.actors);

  els.hudRound = h('span', { class: 'replay__hud-round' }, 'ROUND —');
  els.hudClock = h('span', { class: 'replay__hud-clock' }, '0:00.0');
  els.hudDuration = h('span', { class: 'replay__hud-duration' }, '/ 0:00.0');
  els.hudAlive = h('span', { class: 'replay__hud-alive' }, '');
  els.hudOutcome = h('span', { class: 'replay__hud-outcome' }, '');
  els.quality = h('span', { class: 'replay__quality', hidden: true });
  els.stage.append(h('div', { class: 'replay__hud', 'data-testid': 'replay-hud' },
    els.hudRound, els.hudClock, els.hudDuration, els.hudAlive, els.hudOutcome, els.quality));

  els.legendAText = h('span', null, 'Team A');
  els.legendBText = h('span', null, 'Team B');
  els.legend = h('div', { class: 'replay__legend', 'data-testid': 'replay-legend' },
    h('span', { class: 'replay__legend-item' }, h('i', { class: 'replay__dot replay__dot--a', 'aria-hidden': 'true' }), els.legendAText),
    h('span', { class: 'replay__legend-item' }, h('i', { class: 'replay__dot replay__dot--b', 'aria-hidden': 'true' }), els.legendBText),
    h('span', { class: 'replay__legend-item' }, h('i', { class: 'replay__legend-spike', 'aria-hidden': 'true' }, '◆'), 'Spike'));
  els.stage.append(els.legend);

  els.warning = h('div', { class: 'replay__warning', role: 'status', hidden: true });
  els.stage.append(els.warning);

  els.actorTip = h('div', { class: 'replay__tip replay__tip--actor', role: 'tooltip', hidden: true });
  els.utilTip = h('div', { class: 'replay__tip replay__tip--utility', role: 'tooltip', hidden: true });
  els.stage.append(els.actorTip, els.utilTip);

  els.loading = h('div', { class: 'replay__loading', role: 'status' }, 'Loading replay…');
  els.stage.append(els.loading);

  // --- transport -------------------------------------------------------------------------
  els.prev = h('button', { type: 'button', class: 'icon-btn replay__nav', 'data-testid': 'replay-prev', 'aria-label': 'Previous round', title: 'Previous round ([)', disabled: true }, '‹');
  els.play = h('button', { type: 'button', class: 'btn btn--primary replay__play', 'data-testid': 'replay-play', 'aria-label': 'Play', title: 'Play or pause (Space)', disabled: true }, '▶');
  els.next = h('button', { type: 'button', class: 'icon-btn replay__nav', 'data-testid': 'replay-next', 'aria-label': 'Next round', title: 'Next round (])', disabled: true }, '›');
  els.clock = h('strong', { class: 'replay__clock' }, '0:00.0');
  els.duration = h('span', { class: 'replay__duration' }, ' / 0:00.0');

  els.scrub = h('input', {
    type: 'range', class: 'replay__scrub', 'data-testid': 'replay-scrub',
    min: '0', max: '1', step: '50', value: '0', 'aria-label': 'Round time',
  });
  els.ticks = h('div', { class: 'replay__ticks', 'data-testid': 'replay-ticks', role: 'group', 'aria-label': 'Round event and utility markers' });
  els.scrubWrap = h('div', { class: 'replay__scrubwrap' }, els.scrub, els.ticks);

  els.speed = h('select', { class: 'select replay__speed', 'data-testid': 'replay-speed', 'aria-label': 'Playback speed', id: 'replay-speed-select' },
    ...[0.5, 1, 2, 4, 8].map((value) => h('option', { value: String(value), selected: value === 1 }, `${value}×`)));

  const check = (key, label) => {
    const input = h('input', { type: 'checkbox', checked: st.layers[key] });
    st.layerInputs[key] = input;
    input.addEventListener('change', () => {
      st.layers[key] = input.checked;
      persistLayers();
      requestDraw();
    });
    return h('label', { class: 'replay__toggle', title: label }, input, h('span', null, label));
  };
  els.layers = h('div', { class: 'replay__layers', 'data-testid': 'replay-layers' },
    check('trails', 'Trails'),
    check('abilities', 'Abilities'),
    check('paths', 'Utility paths'),
    check('areas', 'Landings & areas'));
  const rotate = h('input', { type: 'checkbox' });
  rotate.addEventListener('change', () => { st.rotate = rotate.checked; requestDraw(); });
  els.layers.append(h('label', { class: 'replay__toggle', title: 'Rotate agent icons to heading' }, rotate, h('span', null, 'Rotate icons')));

  els.playerFilter = h('select', { class: 'select replay__filter', 'aria-label': 'Show utility from player' },
    h('option', { value: '' }, 'All players'));
  els.playerFilter.addEventListener('change', () => { st.filters.player = els.playerFilter.value; requestDraw(); });
  els.teamFilter = h('select', { class: 'select replay__filter', 'aria-label': 'Show utility from team' },
    h('option', { value: '' }, 'All teams'),
    h('option', { value: 'attackers' }, 'Attackers'),
    h('option', { value: 'defenders' }, 'Defenders'));
  els.teamFilter.addEventListener('change', () => { st.filters.team = els.teamFilter.value; requestDraw(); });
  els.layers.append(els.playerFilter, els.teamFilter);

  els.transport = h('div', { class: 'replay__transport', 'data-testid': 'replay-transport', role: 'group', 'aria-label': 'Playback' },
    h('div', { class: 'replay__transport-row' },
      h('div', { class: 'replay__nav-group' }, els.prev, els.play, els.next),
      h('div', { class: 'replay__time' }, els.clock, els.duration),
      els.scrubWrap,
      h('div', { class: 'replay__speedwrap' }, h('label', { class: 'replay__speedlabel', for: 'replay-speed-select' }, 'SPEED'), els.speed)),
    h('div', { class: 'replay__transport-row replay__transport-row--tools' },
      els.layers,
      h('span', { class: 'replay__keys', 'aria-hidden': 'true' },
        h('span', null, h('span', { class: 'kbd' }, 'Space'), ' play'),
        h('span', null, h('span', { class: 'kbd' }, '←'), h('span', { class: 'kbd' }, '→'), ' 1s'),
        h('span', null, h('span', { class: 'kbd' }, 'Shift'), ' + arrows 5s'),
        h('span', null, h('span', { class: 'kbd' }, '['), h('span', { class: 'kbd' }, ']'), ' round'))));

  // --- inspector -------------------------------------------------------------------------
  const tab = (name, label) => h('button', {
    type: 'button', role: 'tab', class: 'replay__tab', id: `replay-tab-${name}`,
    'data-testid': `replay-tab-${name}`, 'aria-controls': `replay-pane-${name}`,
    'aria-selected': name === st.tab ? 'true' : 'false', tabindex: name === st.tab ? '0' : '-1',
  }, label);

  const tabs = [
    { name: 'rounds', label: 'Rounds', tab: tab('rounds', 'Rounds') },
    { name: 'events', label: 'Events', tab: tab('events', 'Events') },
    { name: 'players', label: 'Players', tab: tab('players', 'Players') },
    { name: 'coach', label: 'Coach', tab: tab('coach', 'Coach') },
  ];
  const tablist = h('div', { class: 'replay__tabs', role: 'tablist', 'aria-label': 'Inspector' }, ...tabs.map((entry) => entry.tab));

  els.collapse = h('button', {
    type: 'button', class: 'icon-btn replay__collapse', 'aria-expanded': 'true',
    'aria-controls': 'replay-inspector-body', title: 'Collapse inspector (Ctrl+I)',
    'aria-label': 'Collapse inspector', onclick: () => setCollapsed(!st.collapsed),
  }, '»');

  els.roundsPane = h('div', {
    class: 'replay__pane', role: 'tabpanel', id: 'replay-pane-rounds', 'aria-labelledby': 'replay-tab-rounds',
    'data-testid': 'replay-rounds',
  });
  els.eventsPane = h('div', {
    class: 'replay__pane', role: 'tabpanel', id: 'replay-pane-events', 'aria-labelledby': 'replay-tab-events',
    'data-testid': 'replay-events', hidden: true,
  });
  els.playersPane = h('div', {
    class: 'replay__pane', role: 'tabpanel', id: 'replay-pane-players', 'aria-labelledby': 'replay-tab-players',
    'data-testid': 'replay-players', hidden: true,
  });
  els.coachPane = h('div', {
    class: 'replay__pane', role: 'tabpanel', id: 'replay-pane-coach', 'aria-labelledby': 'replay-tab-coach',
    'data-testid': 'replay-coach', hidden: true,
  });

  els.inspectorBody = h('div', { class: 'replay__inspector-body', id: 'replay-inspector-body' },
    els.roundsPane, els.eventsPane, els.playersPane, els.coachPane);
  els.inspector = h('aside', { class: 'replay__inspector', 'data-testid': 'replay-inspector', 'aria-label': 'Match information' },
    h('div', { class: 'replay__inspector-head' }, tablist, els.collapse),
    els.inspectorBody,
    h('footer', { class: 'replay__legal' },
      h('small', null, 'ValoHub was created under Riot Games\' "Legal Jibber Jabber" policy using assets owned by Riot Games. Riot Games does not endorse or sponsor this project.')));

  els.body = h('div', { class: 'replay__body' }, els.stage, els.inspector);
  els.workspace = h('div', { class: 'replay', 'data-testid': 'replay' }, header, els.body, els.transport);
  st.root.replaceChildren(els.workspace);

  st.tabs = tabs.map((entry) => ({ ...entry, pane: els[`${entry.name}Pane`] }));
  for (const entry of st.tabs) {
    entry.tab.addEventListener('click', () => selectTab(entry.name));
    entry.tab.addEventListener('keydown', (event) => {
      const index = st.tabs.indexOf(entry);
      let next = null;
      if (event.key === 'ArrowRight') next = (index + 1) % st.tabs.length;
      else if (event.key === 'ArrowLeft') next = (index - 1 + st.tabs.length) % st.tabs.length;
      else if (event.key === 'Home') next = 0;
      else if (event.key === 'End') next = st.tabs.length - 1;
      if (next == null) return;
      event.preventDefault();
      selectTab(st.tabs[next].name, true);
    });
  }
}

// ---------------------------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------------------------

function wire() {
  const signal = st.listeners.signal;
  const els = st.els;

  els.play.addEventListener('click', () => setPlaying(!st.playing));
  setTransportEnabled(false);
  els.prev.addEventListener('click', () => stepRound(-1));
  els.next.addEventListener('click', () => stepRound(1));

  els.scrub.addEventListener('input', () => seek(Number(els.scrub.value)));
  els.scrub.addEventListener('change', () => flushUrl());

  els.speed.addEventListener('change', () => {
    st.speed = Number(els.speed.value) || 1;
    st.ctx.state.set('speed', st.speed);
  });

  els.stage.addEventListener('pointermove', onPointerMove);
  els.stage.addEventListener('pointerleave', () => { els.actorTip.hidden = true; els.utilTip.hidden = true; });
  els.stage.addEventListener('click', onStageClick);

  document.addEventListener('click', (event) => {
    if (event.target && event.target.closest && event.target.closest('.replay__tick-pop')) return;
    closeTickPop();
  }, { signal });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeTickPop();
  }, { signal });

  window.addEventListener('resize', scheduleResize, { signal });

  // v1 contract: a `#r=&t=` hash change re-targets the viewer without a remount.
  window.addEventListener('hashchange', () => {
    if (!st || st.destroyed || !st.round) return;
    const moment = parseMomentHash(location.hash);
    if (!Number.isFinite(moment.r)) return;
    const index = roundIndexFor(st.rounds, moment.r);
    if (index < 0) return;
    const offset = Number.isFinite(moment.t) ? moment.t * 1000 : 0;
    if (index === st.roundIndex) seek(offset);
    else chooseRound(index, offset).catch(() => {});
  }, { signal });

  if ('ResizeObserver' in window) {
    const stageRaf = { id: 0 };
    st.ro = new ResizeObserver(() => {
      cancelAnimationFrame(stageRaf.id);
      stageRaf.id = requestAnimationFrame(() => { if (st && !st.destroyed) resize(); });
    });
    st.ro.observe(els.stage);
    st.ro.observe(els.scrubWrap);
    const observer = st.ro;
    st.resizeRafCleanup = () => { cancelAnimationFrame(stageRaf.id); observer.disconnect(); };
  }

  // Compact layout (contract §2): the inspector starts collapsed below 1100px.
  if (window.matchMedia) {
    st.mq = window.matchMedia('(max-width: 1100px)');
    if (st.mq.matches) setCollapsed(true, false);
    const onMq = (event) => { if (event.matches) setCollapsed(true, false); };
    if (st.mq.addEventListener) st.mq.addEventListener('change', onMq, { signal });
  }

  // Keyboard: every key goes through the shell's scoped shortcut registry.
  st.ctx.shortcuts.register({
    'Space': (event) => {
      // Space on a focused button/link is native activation — do not double-toggle playback.
      const native = event.target && event.target.closest && event.target.closest('button,a[href],input,select,textarea,summary');
      if (native) return false;
      setPlaying(!st.playing);
      return true;
    },
    'ArrowLeft': () => { seek(seekBy(st.elapsed, -1000, duration())); return true; },
    'ArrowRight': () => { seek(seekBy(st.elapsed, 1000, duration())); return true; },
    'Shift+ArrowLeft': () => { seek(seekBy(st.elapsed, -5000, duration())); return true; },
    'Shift+ArrowRight': () => { seek(seekBy(st.elapsed, 5000, duration())); return true; },
    'B': (event) => {
      if (event.repeat || event.target?.closest('input,select,textarea,[contenteditable="true"]')) return false;
      toggleBookmark(); return true;
    },
    'J': () => { seek(seekBy(st.elapsed, -5000, duration())); return true; },
    'K': () => { setPlaying(!st.playing); return true; },
    'L': () => { seek(seekBy(st.elapsed, 5000, duration())); return true; },
    '[': () => { stepRound(-1); return true; },
    ']': () => { stepRound(1); return true; },
    '1': () => { selectTab('rounds', false); return true; },
    '2': () => { selectTab('events', false); return true; },
    '3': () => { selectTab('players', false); return true; },
    '4': () => { selectTab('coach', false); return true; },
    'Ctrl+I': () => { setCollapsed(!st.collapsed); return true; },
    'F11': () => { setMapMax(!st.mapMax); return true; },
    'Escape': () => { onEscape(); return true; },
  });
}

function onEscape() {
  if (st.mapMax) { setMapMax(false); return; }
  if (st.els.scrubWrap.querySelector('.replay__tick-pop')) { closeTickPop(); return; }
  st.ctx.navigate('/replays');
}

function setCollapsed(collapsed, focus = true) {
  st.collapsed = !!collapsed;
  st.els.workspace.classList.toggle('replay--inspector-collapsed', st.collapsed);
  st.els.collapse.setAttribute('aria-expanded', st.collapsed ? 'false' : 'true');
  st.els.collapse.textContent = st.collapsed ? '«' : '»';
  const label = st.collapsed ? 'Expand inspector' : 'Collapse inspector';
  st.els.collapse.title = `${label} (Ctrl+I)`;
  st.els.collapse.setAttribute('aria-label', label);
  if (focus) st.els.collapse.focus({ preventScroll: true });
}

function setMapMax(on) {
  st.mapMax = !!on;
  st.els.workspace.classList.toggle('replay--mapmax', st.mapMax);
  scheduleResize();
}

function stepRound(delta) {
  chooseRound(st.roundIndex + delta).catch(() => {});
}

// ---------------------------------------------------------------------------------------------
// UI state
// ---------------------------------------------------------------------------------------------

function selectTab(name, focus = false) {
  if (!st.tabs.some((entry) => entry.name === name)) return;
  st.tab = name;
  for (const entry of st.tabs) {
    const active = entry.name === name;
    entry.tab.setAttribute('aria-selected', active ? 'true' : 'false');
    entry.tab.tabIndex = active ? 0 : -1;
    entry.pane.hidden = !active;
    if (active && focus) entry.tab.focus({ preventScroll: true });
  }
  if (st.collapsed) setCollapsed(false, false);
  if (name === 'events') renderFeed(true);
  if (name === 'players') renderRoster();
  if (name === 'coach') loadCoach();
  if (name === 'rounds') renderRoundMeta();
}

function readBookmarks() {
  const key = `vp.replay.bookmarks.${st.id}`;
  try {
    const rows = JSON.parse(localStorage.getItem(key) || '[]');
    if (!Array.isArray(rows)) throw new Error('Invalid bookmarks');
    return rows.filter((b) => b && typeof b.id === 'string' && Number.isInteger(b.round)
      && b.round > 0 && Number.isFinite(b.t) && b.t >= 0 && typeof b.label === 'string'
      && Number.isFinite(b.createdAt)).sort((a, b) => b.createdAt - a.createdAt).slice(0, 200);
  } catch {
    try { localStorage.setItem(key, '[]'); } catch { /* Storage may be unavailable. */ }
    return [];
  }
}

function writeBookmarks(rows) {
  try { localStorage.setItem(`vp.replay.bookmarks.${st.id}`, JSON.stringify(rows.slice(0, 200))); }
  catch { st.ctx.ui.toast('Bookmarks could not be saved on this device.', 'error'); return false; }
  syncBookmark();
  return true;
}

function toggleBookmark() {
  if (!st.round || st.els.bookmark.disabled) return;
  const rows = readBookmarks();
  const n = st.round.n;
  const saved = rows.some((b) => b.round === n);
  const next = saved ? rows.filter((b) => b.round !== n) : [{
    id: crypto.randomUUID(), round: n, t: st.elapsed / 1000,
    label: `Round ${n}`, createdAt: Date.now(),
  }, ...rows];
  if (writeBookmarks(next)) st.ctx.ui.toast(saved ? `Bookmark removed · round ${n}` : `Round ${n} bookmarked`, 'success');
}

function syncBookmark() {
  const rows = readBookmarks();
  const saved = st.round && rows.some((b) => b.round === st.round.n);
  st.els.bookmark.setAttribute('aria-pressed', saved ? 'true' : 'false');
  st.els.bookmark.title = `${saved ? 'Remove' : 'Add'} bookmark for round ${st.round?.n || 1}`;
  st.els.bookmark.textContent = saved ? 'Round bookmarked ✓' : 'Bookmark round';
  let section = st.els.roundsPane.querySelector('[data-testid="replay-bookmarks"]');
  if (!section) {
    section = h('section', { class: 'replay__bookmarks', 'data-testid': 'replay-bookmarks', 'aria-label': 'Bookmarks' });
    st.els.roundsPane.prepend(section);
  }
  section.replaceChildren(h('h3', null, 'Bookmarks'));
  if (!rows.length) section.append(h('p', null, 'No bookmarks yet — press B or use Bookmark round.'));
  for (const b of rows) section.append(h('div', { class: 'replay__bookmark-row' },
    h('span', null, `R${b.round} · ${formatClock(b.t * 1000)} · ${b.label}`),
    h('button', { type: 'button', class: 'btn', 'aria-label': `Jump to bookmark round ${b.round}`, onclick: () => {
      const index = roundIndexFor(st.rounds, b.round);
      if (index >= 0) chooseRound(index, b.t * 1000).catch(() => {});
    } }, 'Jump'),
    h('button', { type: 'button', class: 'btn btn--ghost', 'aria-label': `Remove bookmark round ${b.round}`,
      onclick: () => writeBookmarks(readBookmarks().filter((row) => row.id !== b.id)) }, 'Remove')));
}

async function shareMoment() {
  if (!st.round) return;
  const ui = st.ctx.ui;
  const text = `ValoHub — ${display(st.match?.map?.name, 'Unknown map')} · R${st.round.n} · ${formatClock(st.elapsed)}\nreplay ${st.id} · round ${st.round.n} · t=${(st.elapsed / 1000).toFixed(1)}`;
  try {
    if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
    await navigator.clipboard.writeText(text);
    ui.toast('Moment card copied to the clipboard', 'success');
  } catch {
    const area = h('textarea', { class: 'field replay__share-text', 'data-testid': 'share-text', readonly: true, rows: '4', 'aria-label': 'Share moment text' });
    area.value = text;
    ui.openModal(h('div', null, h('h2', { class: 'modal__title' }, 'Share moment'),
      h('p', null, 'Clipboard blocked — copy the text below'), area,
      h('button', { type: 'button', class: 'btn', onclick: () => ui.closeModal() }, 'Close')), { label: 'Share moment' });
    area.focus(); area.select();
  }
}

function persistLayers() {
  st.ctx.state.set('layers', { ...st.layers });
}

// ---------------------------------------------------------------------------------------------
// Loading / states
// ---------------------------------------------------------------------------------------------

async function load() {
  const state = st;
  try {
    const match = await state.ctx.api.get(`/api/replays/${encodeURIComponent(state.id)}`);
    if (st !== state || state.destroyed) return;
    if (!match || !['valoplant_match/v1', 'valoplant_match/v2'].includes(match.schema) || !Array.isArray(match.rounds) || !match.rounds.length) {
      throw new Error('Invalid match metadata');
    }
    state.match = match;
    state.rounds = match.rounds;
    state.byId = new Map((match.players || []).map((player) => [String(player.id), player]));
    state.projection = hasMapProjection(match.map);

    const score = { 0: 0, 1: 0 };
    for (const meta of match.rounds) if (meta && (meta.winner === 0 || meta.winner === 1)) score[meta.winner] += 1;
    state.els.headerIdent.replaceChildren(
      h('strong', { class: 'replay__map' }, display(match.map?.name, 'Unknown map')),
      h('span', { class: 'replay__meta' }, `${score[0]}–${score[1]} · ${match.rounds.length} rounds · patch ${display(match.patch, '—')}`));

    if (!state.projection) {
      state.els.warning.hidden = false;
      state.els.warning.textContent = 'Map transform unavailable; player positions cannot be projected safely.';
    }

    loadMapImage();
    loadAssets();
    buildPlayerFilter();
    applySavedPrefs();
    scheduleResize();
    renderTime();
    renderRoundMeta();

    state.ctx.api.get(`/api/replays/${encodeURIComponent(state.id)}/quality`).then((quality) => {
      if (st !== state || !quality || quality.status !== 'suspect') return;
      state.els.quality.hidden = false;
      state.els.quality.textContent = 'SUSPECT';
      state.els.quality.title = quality.suspectReason || 'Decoder output has suspiciously low sample density';
    }).catch(() => {});

    const query = state.ctx.query;
    const fromHash = parseMomentHash(location.hash);
    const wanted = Number.isFinite(fromHash.r) ? fromHash.r : Number.parseInt(query.get('r'), 10);
    const tSec = Number.isFinite(fromHash.t) ? fromHash.t : Number.parseFloat(query.get('t'));
    const index = roundIndexFor(match.rounds, wanted);
    await chooseRound(index < 0 ? 0 : index, Number.isFinite(tSec) ? tSec * 1000 : 0);
    setTransportEnabled(true);
  } catch (error) {
    if (st !== state || state.destroyed) return;
    showFailure(error);
  }
}

function setTransportEnabled(on) {
  const labels = {
    prev: 'Previous round ([)', play: 'Play or pause (Space)', next: 'Next round (])',
    scrub: 'Round time', bookmark: 'Bookmark round', share: 'Share moment',
  };
  for (const key of Object.keys(labels)) {
    const node = st.els[key];
    if (!node) continue;
    node.disabled = !on;
    node.title = on ? labels[key] : 'Loading replay…';
  }
  if (on) syncBookmark();
}

function applySavedPrefs() {
  const speed = Number(st.ctx.state.get('speed', 1));
  if ([0.5, 1, 2, 4, 8].includes(speed)) {
    st.speed = speed;
    st.els.speed.value = String(speed);
  }
  const layers = st.ctx.state.get('layers', null);
  if (layers && typeof layers === 'object') {
    for (const key of Object.keys(st.layers)) {
      if (!(key in layers)) continue;
      st.layers[key] = !!layers[key];
      if (st.layerInputs[key]) st.layerInputs[key].checked = st.layers[key];
    }
  }
}

function loadMapImage() {
  const map = st.match && st.match.map;
  const uuid = map && map.uuid;
  const safe = typeof uuid === 'string' && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(uuid);
  if (!safe) return;
  const image = new Image();
  image.onload = () => { if (st && !st.destroyed) { st.mapImage = image; drawMap(); requestDraw(); } };
  image.onerror = () => { if (st && !st.destroyed) { st.mapImage = null; drawMap(); } };
  image.src = `${ASSET_BASE}maps/${uuid}.png`;
  st.mapImage = image;
}

function loadAssets() {
  const state = st;
  fetch(`${ASSET_BASE}agents/index.json`, { credentials: 'same-origin' })
    .then((response) => response.ok ? response.json() : {})
    .then((data) => { if (st === state && !state.destroyed) state.assetIndex = data && typeof data === 'object' ? data : {}; })
    .catch(() => {})
    .then(() => {
      if (st !== state || state.destroyed) return;
      const waiting = (state.match.players || []).map((player) => portraitFor(player.agent)?.settled).filter(Boolean);
      return Promise.race([Promise.allSettled(waiting), new Promise((resolve) => setTimeout(resolve, 2500))]);
    })
    .then(() => {
      if (st !== state || state.destroyed) return;
      state.artReady = true;
      renderRoster();
      requestDraw();
    });
  window.setTimeout(() => {
    if (st !== state || state.destroyed || state.artReady) return;
    state.artReady = true;
    requestDraw();
  }, 3500);
}

function buildPlayerFilter() {
  const select = st.els.playerFilter;
  const keep = select.value;
  select.replaceChildren(h('option', { value: '' }, 'All players'));
  for (const player of st.match.players || []) {
    select.append(h('option', { value: String(player.id) }, display(player.name || player.agent || player.id)));
  }
  if ([...select.options].some((option) => option.value === keep)) select.value = keep;
  st.filters.player = select.value;
}

function showFailure(error) {
  const state = st;
  const notFound = error && (error.status === 404 || error.code === 'not_found');
  const title = notFound ? 'Replay not found' : 'Replay unavailable';
  const body = notFound
    ? `No replay with id “${state.id}” exists in this library.`
    : (error && error.message ? error.message : 'The replay could not be loaded.');
  const card = h('div', { class: 'empty replay__state', role: notFound ? 'status' : 'alert' },
    h('div', { class: 'empty__art', 'aria-hidden': 'true' }, notFound ? '?' : '!'),
    h('h1', { class: 'empty__title' }, title),
    h('p', { class: 'empty__body' }, body),
    h('div', { class: 'replay__state-actions' },
      h('a', { class: 'btn btn--primary', 'data-testid': 'replay-back', href: '/replays' }, 'Back to Library')));
  if (!notFound) {
    card.append(h('button', {
      type: 'button', class: 'btn', onclick: () => { mount(state.root, state.ctx); },
    }, 'Retry'));
  }
  state.root.replaceChildren(h('div', { class: 'replay replay--state', 'data-testid': 'replay' }, card));
}

// ---------------------------------------------------------------------------------------------
// Rounds + time
// ---------------------------------------------------------------------------------------------

function duration() {
  return roundDuration(st.round);
}

async function fetchRound(index) {
  const meta = st.rounds[index];
  if (!meta) throw new Error('Round out of range');
  const n = meta.n;
  if (st.roundCache.has(n)) return st.roundCache.get(n);
  if (st.loadingRounds.has(n)) return st.loadingRounds.get(n);
  const promise = st.ctx.api.get(`/api/replays/${encodeURIComponent(st.id)}/rounds/${n}`).then((data) => {
    if (!data || !['valoplant_round/v1', 'valoplant_round/v2'].includes(data.schema) || data.n !== n) throw new Error(`Invalid round ${n}`);
    st.roundCache.set(n, data);
    while (st.roundCache.size > 4) st.roundCache.delete(st.roundCache.keys().next().value);
    return data;
  }).finally(() => st.loadingRounds.delete(n));
  st.loadingRounds.set(n, promise);
  return promise;
}

async function chooseRound(index, offsetMs = 0) {
  if (!st || !st.match || index < 0 || index >= st.rounds.length) return;
  const state = st;
  setPlaying(false);
  const token = ++state.generation;
  state.els.loading.hidden = false;
  state.els.loading.textContent = 'Loading round…';
  try {
    const data = await fetchRound(index);
    if (st !== state || state.destroyed || token !== state.generation) return;
    state.round = data;
    state.roundIndex = index;
    state.elapsed = clamp(Number(offsetMs) || 0, 0, roundDuration(data));
    state.els.loading.hidden = true;
    state.lastRoundN = 0;
    state.feedBucket = -1;
    computeDeaths();
    state.ariaForce = true;
    renderRoundMeta();
    renderRounds();
    buildTicks();
    renderTime();
    requestDraw();
    syncBookmark();
    writeUrl();
    for (const near of [index - 1, index + 1]) fetchRound(near).catch(() => {});
  } catch (error) {
    if (st !== state || state.destroyed || token !== state.generation) return;
    state.els.loading.hidden = true;
    state.ctx.ui.toast(`Unable to load round: ${error.message}`, 'error');
  }
}

function seek(ms) {
  if (!st || !st.round) return;
  st.ariaForce = true;
  st.elapsed = clamp(Number(ms) || 0, 0, duration());
  renderTime();
  requestDraw();
  renderFeed();
  scheduleUrl();
}

function setPlaying(on) {
  if (!st || !st.round) return;
  if (on && st.elapsed >= duration()) seek(0);
  st.playing = !!on;
  st.lastFrame = 0;
  st.els.play.textContent = st.playing ? 'Ⅱ' : '▶';
  st.els.play.setAttribute('aria-label', st.playing ? 'Pause' : 'Play');
  st.els.play.title = st.playing ? 'Pause (Space)' : 'Play or pause (Space)';
  if (!st.playing) flushUrl();
}

function renderTime() {
  if (!st) return;
  const total = duration();
  const els = st.els;
  els.clock.textContent = formatClock(st.elapsed);
  els.hudClock.textContent = formatClock(st.elapsed);
  els.duration.textContent = ` / ${formatClock(total)}`;
  els.hudDuration.textContent = `/ ${formatClock(total)}`;
  els.scrub.max = String(Math.max(1, Math.round(total)));
  els.scrub.value = String(Math.round(st.elapsed));
  els.scrub.disabled = !st.round;
  els.hudRound.textContent = st.round ? `ROUND ${st.round.n}` : 'ROUND —';
}

function renderRoundMeta() {
  if (!st || !st.match) return;
  const meta = st.rounds[st.roundIndex];
  if (!meta) return;
  st.els.legendAText.textContent = sideLabel(meta, 0);
  st.els.legendBText.textContent = sideLabel(meta, 1);
  st.els.hudOutcome.textContent = st.round ? `R${st.round.n}: ${outcomeLabel(meta)}` : '';
}

function renderRounds() {
  const pane = st.els.roundsPane;
  const strip = h('div', { class: 'replay__strip' });
  st.rounds.forEach((meta, index) => {
    if (index % 12 === 0) {
      strip.append(h('div', { class: 'replay__half' }, index < 24 ? `HALF ${Math.floor(index / 12) + 1}` : `OVERTIME · SET ${Math.floor((index - 24) / 2) + 1}`));
    }
    const won = meta.winner === 0 || meta.winner === 1;
    strip.append(h('button', {
      type: 'button',
      class: `replay__round${index === st.roundIndex ? ' replay__round--active' : ''}${won ? (meta.winner === 0 ? ' replay__round--a' : ' replay__round--b') : ''}`,
      'aria-current': index === st.roundIndex ? 'true' : 'false',
      title: `Round ${meta.n} · ${sideLabel(meta, 0)} / ${sideLabel(meta, 1)} · ${outcomeLabel(meta)}`,
      'aria-label': `Round ${meta.n} · ${sideLabel(meta, 0)} / ${sideLabel(meta, 1)} · ${outcomeLabel(meta)}`,
      onclick: () => chooseRound(index).catch(() => {}),
    }, String(meta.n)));
  });
  const meta = st.rounds[st.roundIndex];
  pane.replaceChildren(
    h('section', { class: 'replay__section' },
      h('p', { class: 'replay__eyebrow' }, 'MATCH FLOW'),
      h('h2', { class: 'replay__h2' }, 'Rounds', h('span', { class: 'replay__count' }, String(st.rounds.length))),
      strip,
      h('p', { class: 'replay__note' }, st.round ? `R${st.round.n}: ${outcomeLabel(meta)}` : 'Select a round.')));
}

// ---------------------------------------------------------------------------------------------
// Scrub ticks
// ---------------------------------------------------------------------------------------------

function closeTickPop() {
  if (!st) return;
  st.els.scrubWrap.querySelectorAll('.replay__tick-pop').forEach((node) => node.remove());
  st.els.ticks.querySelectorAll('[aria-expanded="true"]').forEach((node) => node.setAttribute('aria-expanded', 'false'));
}

function buildTicks() {
  const wrap = st.els.ticks;
  wrap.replaceChildren();
  if (!st.round) return;
  const total = duration();
  const seen = new Set();
  const entries = [...(st.round.events || []),
    ...['plantT', 'defuseT'].filter((key) => Number.isFinite(st.round.spike?.[key]))
      .map((key) => ({ t: st.round.spike[key], type: key === 'plantT' ? 'plant' : 'defuse' }))];
  // Preserve event times: nearby kills share a counted marker instead of shifting time positions.
  const kills = clusterThrows(entries.filter((event) => event.type === 'kill'), total || 1, wrap.clientWidth || 225, 24);
  for (const cluster of kills) {
    wrap.append(h('span', {
      class: 'replay__tick replay__tick--kill', 'aria-hidden': 'true',
      style: `left:${percentOf(cluster.t, total)}%`,
      title: cluster.items.map((event) => `kill at ${formatClock(event.t)}`).join(' · '),
    }, cluster.items.length > 1 ? `×${cluster.items.length}` : '✕'));
  }
  for (const event of entries) {
    if (!['plant', 'defuse'].includes(event.type) || !Number.isFinite(event.t)) continue;
    if (!(event.t >= 0) || event.t > total) continue;
    const key = `${event.type}-${event.t}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const tick = h('span', {
      class: `replay__tick replay__tick--${event.type}`, 'aria-hidden': 'true',
      style: `left:${percentOf(event.t, total)}%`, title: `${event.type} at ${formatClock(event.t)}`,
    }, { kill: '✕', plant: '◆', defuse: '✓' }[event.type]);
    wrap.append(tick);
  }

  const width = wrap.clientWidth || 225;
  const throws = (Array.isArray(st.round.utility) ? st.round.utility : []).filter((item) => item && item.kind !== 'state');
  const clusters = clusterThrows(throws, total || 1, width, 24);
  const labelOf = (item) => {
    const player = st.byId.get(String(item.actor));
    return `${display(player?.name || player?.agent, 'Unowned')} · ${display(item.ability, item.kind || 'Utility')} · ${formatClock(item.t)}`;
  };
  for (const cluster of clusters) {
    const first = cluster.items[0];
    if (cluster.items.length === 1) {
      const tick = h('button', {
        type: 'button', tabindex: '-1', class: `replay__tick replay__tick--util replay__tick--${first.kind || 'util'}`,
        style: `left:${percentOf(first.t, total)}%`, title: labelOf(first),
        'aria-label': `Seek to ${labelOf(first)}`,
        onclick: (event) => { event.stopPropagation(); closeTickPop(); seek(first.t); },
      }, '◆');
      wrap.append(tick);
    } else {
      const tick = h('button', {
        type: 'button', tabindex: '-1', class: 'replay__tick replay__tick--util replay__tick--cluster',
        style: `left:${percentOf(first.t, total)}%`, 'aria-haspopup': 'true', 'aria-expanded': 'false',
        title: `${cluster.items.length} throws near ${formatClock(first.t)} — click to choose`,
        'aria-label': `${cluster.items.length} throws near ${formatClock(first.t)}; choose one`,
      }, String(cluster.items.length));
      tick.addEventListener('click', (event) => {
        event.stopPropagation();
        const had = tick.getAttribute('aria-expanded') === 'true';
        closeTickPop();
        if (had) return;
        tick.setAttribute('aria-expanded', 'true');
        const pop = h('div', { class: 'replay__tick-pop', role: 'group', 'aria-label': `Throws near ${formatClock(first.t)}` });
        for (const item of cluster.items) {
          pop.append(h('button', {
            type: 'button', class: 'replay__tick-pop-item', onclick: () => { closeTickPop(); seek(item.t); },
          }, labelOf(item)));
        }
        st.els.scrubWrap.append(pop);
        const tickRect = tick.getBoundingClientRect();
        const wrapRect = st.els.scrubWrap.getBoundingClientRect();
        const popWidth = pop.offsetWidth;
        pop.style.left = `${Math.max(0, Math.min(tickRect.left - wrapRect.left, wrapRect.width - popWidth))}px`;
      });
      wrap.append(tick);
    }
  }
}

function scheduleRebuildTicks() {
  if (!st || !st.round) return;
  buildTicks();
}

// ---------------------------------------------------------------------------------------------
// Inspector panes
// ---------------------------------------------------------------------------------------------

function renderFeed(force = false) {
  if (!st || !st.round || st.tab !== 'events') return;
  const bucket = Math.floor(st.elapsed / 500);
  if (!force && bucket === st.feedBucket && st.lastRoundN === st.round.n) return;
  st.feedBucket = bucket;
  st.lastRoundN = st.round.n;

  const spike = st.round.spike || {};
  const spikeRows = [];
  if (Number.isFinite(spike.plantT)) spikeRows.push(`◆ Plant ${formatClock(spike.plantT)}${spike.site ? ` · ${spike.site}` : ''}`);
  if (Number.isFinite(spike.defuseT)) spikeRows.push(`✓ Defuse ${formatClock(spike.defuseT)}`);

  const hasUtility = Array.isArray(st.round.utility) && st.round.utility.length > 0;
  const events = (st.round.events || [])
    .filter((event) => Number.isFinite(event.t) && event.t <= st.elapsed && !(hasUtility && event.type === 'ability'))
    .map((event) => ({ t: event.t, event }));
  const utilities = hasUtility ? st.round.utility
    .filter((item) => item && item.kind !== 'state' && Number.isFinite(item.t) && item.t <= st.elapsed)
    .map((item) => ({ t: item.t, util: item })) : [];
  const entries = [...events, ...utilities].sort((a, b) => b.t - a.t).slice(0, 8);

  const feed = h('div', { class: 'replay__feed', 'aria-live': 'off' });
  for (const entry of entries) {
    if (entry.util) {
      const item = entry.util;
      const player = st.byId.get(String(item.actor));
      const ability = display(item.ability, item.kind || 'Utility');
      const who = display(player?.name || player?.agent, 'Unowned');
      const row = h('button', {
        type: 'button', class: 'replay__feed-item', title: `R${st.round.n} · ${formatClock(item.t)} · ${ability} · click to seek`,
        'aria-label': `R${st.round.n} · ${formatClock(item.t)} · ${who} · ${ability} · press Enter to seek`,
        onclick: () => seek(item.t),
      });
      const icon = iconFor(item.agent, item.ability);
      if (icon) row.append(h('img', { src: icon, alt: '', loading: 'lazy', onerror: (event) => event.target.remove() }));
      else row.append(h('span', { class: 'replay__feed-glyph', 'aria-hidden': 'true' }, '◆'));
      row.append(h('span', null, `${who} · ${ability}`));
      feed.append(row);
      continue;
    }
    const event = entry.event;
    const actor = st.byId.get(String(event.actor));
    const victim = st.byId.get(String(event.target));
    const row = h('button', {
      type: 'button', class: 'replay__feed-item replay__feed-item--event', title: `${event.type} at ${formatClock(event.t)} — click to seek`,
      'aria-label': `${event.type} at ${formatClock(event.t)} — press Enter to seek`,
      onclick: () => seek(event.t),
    });
    const art = event.type === 'kill' && actor ? artCandidates(actor.agent)[0] : null;
    if (art) row.append(h('img', { src: art, alt: '', loading: 'lazy', onerror: (e) => e.target.remove() }));
    const name = (player, fallback) => h('span', {
      class: `replay__feed-player${player && player.team === 0 ? ' replay__feed-player--a' : player && player.team === 1 ? ' replay__feed-player--b' : ''}`,
      title: teamLabelOf(player && player.team),
    }, display(player && (player.name || player.agent) || fallback, 'Spike'));
    if (event.type === 'kill') {
      row.append(name(actor, event.actor), h('span', { class: 'replay__feed-op', 'aria-hidden': 'true' }, '✕'), name(victim, event.target));
    } else {
      row.append(name(actor, event.actor), h('span', { class: 'replay__feed-op' }, ` ${event.ability || event.type}`));
    }
    feed.append(row);
  }
  if (!entries.length) feed.append(h('p', { class: 'replay__note' }, 'No recorded events yet.'));

  st.els.eventsPane.replaceChildren(
    h('section', { class: 'replay__section' },
      h('p', { class: 'replay__eyebrow' }, 'OBJECTIVE'),
      h('h2', { class: 'replay__h2' }, 'Spike timeline'),
      h('p', { class: 'replay__note' }, spikeRows.length ? spikeRows.join('  /  ') : 'No plant or defuse recorded.')),
    h('section', { class: 'replay__section' },
      h('p', { class: 'replay__eyebrow' }, 'ROUND EVENTS'),
      h('h2', { class: 'replay__h2' }, 'Kill feed & events'),
      feed));
}

function renderRoster() {
  if (!st || !st.match) return;
  const roster = h('div', { class: 'replay__roster' });
  for (const player of st.match.players || []) {
    const row = h('div', { class: 'replay__roster-row', title: teamLabelOf(player.team) || 'Unassigned' });
    const portrait = agentEntry(player.agent)?.portrait;
    if (typeof portrait === 'string' && /^agents\/[a-z0-9-]+\.png$/i.test(portrait)) {
      row.append(h('img', { class: 'replay__agent', src: `${ASSET_BASE}${portrait}`, alt: '', loading: 'lazy', onerror: (event) => event.target.remove() }));
    }
    row.append(
      h('i', { class: `replay__dot ${player.team === 1 ? 'replay__dot--b' : player.team === 0 ? 'replay__dot--a' : 'replay__dot--u'}`, 'aria-hidden': 'true' }),
      h('span', { class: 'replay__roster-name' }, display(player.name || player.agent || player.id)),
      h('span', { class: 'replay__role' }, player.team === 0 ? 'ATK' : player.team === 1 ? 'DEF' : '—'));
    roster.append(row);
  }
  st.els.playersPane.replaceChildren(
    h('section', { class: 'replay__section' },
      h('p', { class: 'replay__eyebrow' }, 'ON THE MAP'),
      h('h2', { class: 'replay__h2' }, 'Players'),
      roster));
}

// ---------------------------------------------------------------------------------------------
// Coach tab (ported from coach.js, rendered with DOM nodes only)
// ---------------------------------------------------------------------------------------------

async function loadCoach() {
  if (!st || !st.match || st.coachLoaded) return;
  st.coachLoaded = true;
  const base = `/api/replays/${encodeURIComponent(st.id)}`;
  const pane = st.els.coachPane;
  const select = h('select', { class: 'select replay__coach-picker', 'aria-label': 'This is me', id: 'replay-coach-player' },
    h('option', { value: '' }, 'Choose your player'));
  for (const player of st.match.players || []) {
    select.append(h('option', { value: String(player.id) },
      `${player.id} · ${display(player.name || player.agent, 'Unknown')} · Team ${player.team === 0 ? 'A' : player.team === 1 ? 'B' : '?'}`));
  }
  const button = h('button', { type: 'button', class: 'btn btn--primary', disabled: true }, 'Review my game');
  const status = h('p', { class: 'replay__note', role: 'status' }, 'Choose your player to begin.');
  const result = h('div', { class: 'replay__coach-result', 'aria-live': 'polite' });
  st.els.coachUi = { select, button, status, result };
  pane.replaceChildren(
    h('section', { class: 'replay__section' },
      h('p', { class: 'replay__eyebrow' }, 'PERSONAL COACH'),
      h('h2', { class: 'replay__h2' }, 'Review my game'),
      h('label', { class: 'replay__coach-label', for: 'replay-coach-player' }, 'This is me'),
      select, button, status, result));

  select.addEventListener('change', async () => {
    const choice = select.value;
    st.coachPlayer = choice;
    result.replaceChildren();
    button.disabled = true;
    if (!choice) { status.textContent = 'Choose your player to begin.'; return; }
    status.textContent = 'Saving your player…';
    try {
      const me = await putJson(`${base}/me`, { player: choice });
      if (!st || st.destroyed || select.value !== choice) return;
      status.textContent = `This is me: ${display(me.player, choice)}. Ready to review.`;
      button.disabled = st.coachPending;
    } catch (error) {
      status.textContent = `Could not save player: ${error.message}`;
    }
  });

  button.addEventListener('click', async () => {
    if (!st || st.coachPending || !select.value) return;
    st.coachPending = true;
    button.disabled = true;
    select.disabled = true;
    result.replaceChildren();
    status.textContent = 'Reviewing replay… local narration may take a minute.';
    try {
      const data = await st.ctx.api.post(`${base}/coach`, { player: select.value });
      if (st && !st.destroyed) renderCoachResult(st.els.coachUi, data);
    } catch (error) {
      status.textContent = `Review unavailable: ${error.message}`;
    } finally {
      if (st && !st.destroyed) {
        st.coachPending = false;
        select.disabled = false;
        button.disabled = !select.value;
      }
    }
  });

  try {
    const me = await st.ctx.api.get(`${base}/me`);
    if (!st || st.destroyed) return;
    if (me && me.player && [...select.options].some((option) => option.value === me.player)) {
      select.value = me.player;
      st.coachPlayer = me.player;
    }
    button.disabled = !select.value;
    status.textContent = select.value ? `Selected ${select.selectedOptions[0].textContent}.` : 'Choose your player to begin.';
    if (select.value) {
      try {
        renderCoachResult(st.els.coachUi, await st.ctx.api.get(`${base}/coach?player=${encodeURIComponent(select.value)}`));
      } catch (error) {
        if (!String(error.message).includes('no cached coach result')) status.textContent = `Saved review unavailable: ${error.message}`;
      }
    }
  } catch (error) {
    status.textContent = `Coach unavailable: ${error.message}`;
  }
}

// The shell's ctx.api exposes get/post/del only; the identity pick is a PUT, so it goes direct.
async function putJson(path, body) {
  const response = await fetch(path, {
    method: 'PUT', credentials: 'same-origin',
    headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error || `HTTP ${response.status}`);
  return data;
}

function renderCoachResult(ui, data) {
  if (!ui) return;
  const { result, status } = ui;
  result.replaceChildren();
  if (!data || data.schema !== 'valoplant_coach_api/v1') {
    result.append(h('p', { class: 'replay__note' }, 'Unexpected coach response.'));
    return;
  }
  const narration = data.narration || {};
  const rules = (data.rules && data.rules.findings) || [];
  result.append(h('p', { class: 'replay__coach-summary' },
    display(narration.summary, `Rules-only review: ${rules.length} replay-backed findings.`)));
  if (data.llm && data.llm.available === false) {
    result.append(h('p', { class: 'replay__note replay__note--warn' },
      `Local model unavailable — showing rules-only findings. ${display(data.llm.reason, '')}`));
  } else if (data.llm && data.llm.available) {
    result.append(h('p', { class: 'replay__note' }, `Narration: ${display(data.llm.backend, 'local')} · ${display(data.llm.model, 'unknown')}`));
  }
  const section = (title, items, limit, ruleMode) => {
    result.append(h('h3', { class: 'replay__h3' }, title));
    if (!items || !items.length) {
      result.append(h('p', { class: 'replay__note' }, 'No cited items for this review.'));
      return;
    }
    for (const item of items.slice(0, limit)) {
      const severity = ['major', 'minor', 'info'].includes(item.severity) ? item.severity : 'info';
      const card = h('article', { class: 'replay__finding' },
        h('div', { class: 'replay__finding-head' },
          h('strong', null, ruleMode ? display(item.kind, 'Finding').replaceAll('_', ' ') : display(item.title, 'Finding')),
          h('span', { class: `replay__severity replay__severity--${severity}` }, severity)),
        h('p', { class: 'replay__note' }, ruleMode ? display(item.text, '') : display(item.detail, '')));
      const refs = h('div', { class: 'replay__refs' });
      for (const ref of item.refs || []) {
        if (!Number.isInteger(ref.round) || !Number.isFinite(ref.t) || ref.t < 0) continue;
        const seconds = Number.isFinite(ref.tSec) ? ref.tSec : ref.t / 1000;
        refs.append(h('button', {
          type: 'button', class: 'replay__ref', title: `Jump to round ${ref.round} at ${seconds.toFixed(1)}s`,
          onclick: () => jumpTo(ref.round, ref.t),
        }, `R${ref.round} · ${seconds.toFixed(1)}s`));
      }
      card.append(refs);
      result.append(card);
    }
  };
  section('Top mistakes · replay rules', rules, 3, true);
  if (narration.mistakes && narration.mistakes.length) section('Coach perspective', narration.mistakes, 3, false);
  if (narration.drills && narration.drills.length) section('Practice drills', narration.drills, 6, false);
  else result.append(h('p', { class: 'replay__note' }, 'Practice drills require local narration; replay findings remain available above.'));
  status.textContent = data.llm && data.llm.available ? 'Review ready.' : 'Rules-only review ready.';
}

async function jumpTo(roundNumber, tMs) {
  if (!st) return;
  const index = roundIndexFor(st.rounds, roundNumber);
  if (index < 0) return;
  if (index === st.roundIndex) { seek(tMs); return; }
  await chooseRound(index, tMs);
}

// ---------------------------------------------------------------------------------------------
// URL state
// ---------------------------------------------------------------------------------------------

function writeUrl() {
  if (!st || st.destroyed || !st.round) return;
  st.urlTimer = 0;
  st.lastUrlAt = Date.now();
  const query = deepLink(location.pathname, st.round.n, st.elapsed);
  const next = `${query}${hashLink(st.round.n, st.elapsed)}`;
  if (location.pathname + location.search + location.hash === next) return;
  history.replaceState(history.state, '', next);
}

function flushUrl() {
  if (!st || st.destroyed || !st.round) return;
  if (st.urlTimer) { clearTimeout(st.urlTimer); st.urlTimer = 0; }
  writeUrl();
}

function scheduleUrl() {
  if (!st || st.destroyed || !st.round) return;
  const now = Date.now();
  if (now - st.lastUrlAt >= 400) { writeUrl(); return; }
  if (st.urlTimer) return;
  st.urlTimer = window.setTimeout(writeUrl, Math.max(0, 400 - (now - st.lastUrlAt)));
}

// ---------------------------------------------------------------------------------------------
// Canvas: map, markers, labels, utility
// ---------------------------------------------------------------------------------------------

const MARKER_R = 13;
const PORTRAIT_R = 10;
const SPRITE_BOX = 44;
const NUDGE_CAP = 16;
const BADGE_DIST = MARKER_R * 2;
const RING_REACH = (team) => team === 1 ? MARKER_R + 5.5 : MARKER_R + 1.5;
const ART_RE = /^agents\/(?:map\/)?[a-z0-9-]+\.png$/i;
const ABILITY_RE = /^abilities\/[a-z0-9-]+\.png$/i;

function requestDraw() {
  if (!st || st.destroyed) return;
  st.dirty = true;
}

function scheduleResize() {
  if (!st || st.destroyed || st.resizeRaf) return;
  st.resizeRaf = requestAnimationFrame(() => {
    if (!st || st.destroyed) return;
    st.resizeRaf = 0;
    resize();
  });
}

function resize() {
  if (!st || !st.els.stage) return;
  const bounds = st.els.stage.getBoundingClientRect();
  if (!bounds.width || !bounds.height) return;
  const scale = Math.min(window.devicePixelRatio || 1, 2);
  st.dpr = scale;
  st.sprites.clear();
  st.widthCache.clear();
  for (const canvas of [st.els.mapCanvas, st.els.actors]) {
    canvas.width = Math.max(1, Math.round(bounds.width * scale));
    canvas.height = Math.max(1, Math.round(bounds.height * scale));
    canvas.style.width = `${bounds.width}px`;
    canvas.style.height = `${bounds.height}px`;
  }
  st.els.mapCanvas.getContext('2d').setTransform(scale, 0, 0, scale, 0, 0);
  st.els.actors.getContext('2d').setTransform(scale, 0, 0, scale, 0, 0);
  st.view = { w: bounds.width, h: bounds.height };
  st.mapFit = computeMapFit(bounds.width, bounds.height);
  drawMap();
  requestDraw();
  scheduleRebuildTicks();
}

function drawMap() {
  if (!st || !st.match) return;
  const g = st.els.mapCanvas.getContext('2d');
  const { w, h } = st.view;
  g.fillStyle = '#101820';
  g.fillRect(0, 0, w, h);
  const fit = st.mapFit;
  if (st.mapImage && st.mapImage.complete && st.mapImage.naturalWidth) {
    g.globalAlpha = 0.8;
    g.drawImage(st.mapImage, fit.x, fit.y, fit.side, fit.side);
    g.globalAlpha = 1;
  } else {
    g.fillStyle = '#1c2b34';
    g.fillRect(fit.x, fit.y, fit.side, fit.side);
  }
  g.strokeStyle = '#56646b';
  g.lineWidth = 1;
  g.strokeRect(fit.x, fit.y, fit.side, fit.side);
}

function project(x, y) {
  if (!st || !st.match || !st.projection) return null;
  return projectPoint(st.match.map, st.mapFit, x, y);
}

function sample(track, tMs) {
  if (!st || !st.round) return null;
  return sampleTrack(track, tMs, st.round.tickMs, st.round.frames);
}

function agentEntry(name) {
  if (typeof name !== 'string' || name.startsWith('_') || !Object.hasOwn(st.assetIndex, name)) return null;
  const entry = st.assetIndex[name];
  return entry && typeof entry === 'object' ? entry : null;
}

function artCandidates(name) {
  const entry = agentEntry(name);
  if (!entry) return [];
  return [entry.map, entry.portrait]
    .filter((path) => typeof path === 'string' && ART_RE.test(path))
    .map((path) => `${ASSET_BASE}${path}`);
}

function iconFor(agent, ability) {
  const entry = agentEntry(agent);
  const path = entry && entry.abilities ? entry.abilities[ability] : null;
  return typeof path === 'string' && ABILITY_RE.test(path) ? `${ASSET_BASE}${path}` : null;
}

function loadPortrait(rec) {
  if (rec.i >= rec.urls.length) { rec.state = 'error'; rec.img = null; rec.settle(); requestDraw(); return; }
  const image = new Image();
  rec.img = image;
  rec.state = 'loading';
  image.onload = () => { rec.state = 'ok'; rec.settle(); requestDraw(); };
  image.onerror = () => { rec.i += 1; loadPortrait(rec); };
  image.src = rec.urls[rec.i];
}

function portraitFor(name) {
  const urls = artCandidates(name);
  if (!urls.length) return null;
  const key = urls.join('|');
  let rec = st.portraits.get(key);
  if (!rec) {
    rec = { key, urls, i: 0, img: null, state: 'loading', settle: null };
    rec.settled = new Promise((resolve) => { rec.settle = resolve; });
    st.portraits.set(key, rec);
    loadPortrait(rec);
  }
  return rec;
}

function initialsOf(player) {
  const letters = String(player && (player.agent || player.name || player.id) || '?').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
  return letters.slice(0, 2) || '?';
}

function teamTint(team) {
  return team === 0 ? TEAM_A : team === 1 ? TEAM_B : NEUTRAL;
}

function drawCross(g, color, alpha) {
  g.save();
  g.globalAlpha = alpha;
  g.lineCap = 'round';
  g.strokeStyle = INK;
  g.lineWidth = 5.5;
  g.beginPath();
  g.moveTo(-6, -6); g.lineTo(6, 6); g.moveTo(6, -6); g.lineTo(-6, 6);
  g.stroke();
  g.strokeStyle = color;
  g.lineWidth = 3;
  g.beginPath();
  g.moveTo(-6, -6); g.lineTo(6, 6); g.moveTo(6, -6); g.lineTo(-6, 6);
  g.stroke();
  g.restore();
}

function finishSprite(sprite) {
  if (typeof createImageBitmap === 'function') {
    createImageBitmap(sprite.canvas).then((bitmap) => { sprite.bitmap = bitmap; }).catch(() => {});
  }
  return sprite;
}

function bakeSprite(rec, initials, team, kind) {
  const size = Math.ceil(SPRITE_BOX * st.dpr);
  const k = size / SPRITE_BOX;
  const canvas = document.createElement('canvas');
  canvas.width = canvas.height = size;
  const g = canvas.getContext('2d', { willReadFrequently: true });
  g.imageSmoothingEnabled = true;
  g.imageSmoothingQuality = 'high';
  g.scale(k, k);
  g.translate(SPRITE_BOX / 2, SPRITE_BOX / 2);
  const tint = teamTint(team);
  const art = rec && rec.state === 'ok' ? rec.img : null;
  if (kind === 'x') { drawCross(g, tint, 0.6); return finishSprite({ canvas, fallback: null }); }
  const body = kind === 'dead' ? 0.55 : 1;
  g.globalAlpha = body;
  if (art) {
    g.globalAlpha = body * 0.35;
    g.fillStyle = tint;
    g.beginPath(); g.arc(0, 0, PORTRAIT_R, 0, Math.PI * 2); g.fill();
    g.globalAlpha = body;
    g.save();
    g.beginPath(); g.arc(0, 0, PORTRAIT_R, 0, Math.PI * 2); g.clip();
    g.drawImage(art, -PORTRAIT_R, -PORTRAIT_R, PORTRAIT_R * 2, PORTRAIT_R * 2);
    g.restore();
  } else {
    g.fillStyle = tint;
    g.beginPath(); g.arc(0, 0, PORTRAIT_R, 0, Math.PI * 2); g.fill();
    g.font = 'bold 12px sans-serif';
    g.textAlign = 'center';
    g.textBaseline = 'middle';
    g.lineJoin = 'round';
    g.strokeStyle = INK;
    g.lineWidth = 2.3;
    g.strokeText(initials, 0, 0.5);
    g.fillStyle = '#fff';
    g.fillText(initials, 0, 0.5);
  }
  g.globalAlpha = 1;
  g.strokeStyle = 'rgba(255,255,255,.85)';
  g.lineWidth = 1;
  g.beginPath(); g.arc(0, 0, PORTRAIT_R - 0.5, 0, Math.PI * 2); g.stroke();
  const mid = (PORTRAIT_R + MARKER_R) / 2;
  if (kind === 'dead') g.globalAlpha = 0.6;
  g.strokeStyle = tint;
  g.lineWidth = 3;
  g.beginPath(); g.arc(0, 0, mid, 0, Math.PI * 2); g.stroke();
  if (team === 1) {
    g.lineWidth = 2;
    g.beginPath(); g.arc(0, 0, MARKER_R + 3, 0, Math.PI * 2); g.stroke();
    g.strokeStyle = INK;
    g.lineWidth = 1.5;
    g.beginPath(); g.arc(0, 0, MARKER_R + 4.75, 0, Math.PI * 2); g.stroke();
  } else {
    g.strokeStyle = INK;
    g.lineWidth = 1.5;
    g.beginPath(); g.arc(0, 0, MARKER_R + 0.75, 0, Math.PI * 2); g.stroke();
  }
  g.globalAlpha = 1;
  if (kind === 'dead') drawCross(g, tint, 1);
  return finishSprite({ canvas, fallback: art ? null : 'initials' });
}

function spriteFor(player, kind) {
  const rec = portraitFor(player.agent);
  const initials = initialsOf(player);
  const art = rec ? `${rec.key}@${rec.state}` : `none:${initials}`;
  const key = `${art}|${player.team ?? 'u'}|${kind}|${st.dpr}`;
  let sprite = st.sprites.get(key);
  if (!sprite) { sprite = bakeSprite(rec, initials, player.team, kind); st.sprites.set(key, sprite); }
  return sprite;
}

function blitSprite(g, sprite, x, y, angle) {
  const css = sprite.canvas.width / st.dpr;
  const source = sprite.bitmap || sprite.canvas;
  if (angle !== null && st.rotate) {
    g.save();
    g.translate(x, y);
    g.rotate(angle + Math.PI / 2);
    g.drawImage(source, -css / 2, -css / 2, css, css);
    g.restore();
  } else {
    g.drawImage(source, Math.round((x - css / 2) * st.dpr) / st.dpr, Math.round((y - css / 2) * st.dpr) / st.dpr, css, css);
  }
}

function drawChevron(g, p, angle, color) {
  const tipD = MARKER_R + 15;
  const baseD = MARKER_R + 7;
  const wing = (tipD - baseD) / -Math.cos(2.55);
  const notch = MARKER_R + 10.5;
  const tip = [p.x + Math.cos(angle) * tipD, p.y + Math.sin(angle) * tipD];
  g.beginPath();
  g.moveTo(tip[0], tip[1]);
  g.lineTo(tip[0] + Math.cos(angle + 2.55) * wing, tip[1] + Math.sin(angle + 2.55) * wing);
  g.lineTo(p.x + Math.cos(angle) * notch, p.y + Math.sin(angle) * notch);
  g.lineTo(tip[0] + Math.cos(angle - 2.55) * wing, tip[1] + Math.sin(angle - 2.55) * wing);
  g.closePath();
  g.lineJoin = 'round';
  g.strokeStyle = INK;
  g.lineWidth = 2;
  g.stroke();
  g.fillStyle = color;
  g.fill();
}

function paintMarkers(g, list) {
  for (const marker of list) {
    blitSprite(g, spriteFor(marker.player, 'alive'), marker.q.x, marker.q.y, marker.angle);
    if (marker.hp !== null) {
      g.fillStyle = INK;
      g.fillRect(marker.q.x - 13, marker.q.y + 33, 26, 4);
      g.fillStyle = marker.hp > 30 ? '#6de5a4' : '#ff6874';
      if (marker.hp > 0) g.fillRect(marker.q.x - 12, marker.q.y + 34, Math.max(1.5, 24 * marker.hp / 100), 2);
    }
  }
  for (const marker of list) if (marker.angle !== null) drawChevron(g, marker.q, marker.angle, marker.color);
}

function paintTicks(g, list) {
  for (const marker of list) {
    const dx = marker.q.x - marker.p.x;
    const dy = marker.q.y - marker.p.y;
    const d = Math.hypot(dx, dy);
    const edge = RING_REACH(marker.player.team);
    if (!(d > 2)) continue;
    g.globalAlpha = 1;
    g.lineCap = 'butt';
    if (d > edge) {
      const ex = marker.q.x - dx / d * edge;
      const ey = marker.q.y - dy / d * edge;
      g.strokeStyle = INK;
      g.lineWidth = 3.5;
      g.beginPath(); g.moveTo(marker.p.x, marker.p.y); g.lineTo(ex, ey); g.stroke();
      g.strokeStyle = marker.color;
      g.lineWidth = 1.5;
      g.beginPath(); g.moveTo(marker.p.x, marker.p.y); g.lineTo(ex, ey); g.stroke();
    }
    if (d > 4) {
      g.fillStyle = INK;
      g.beginPath(); g.arc(marker.p.x, marker.p.y, 2.5, 0, Math.PI * 2); g.fill();
      g.fillStyle = marker.color;
      g.beginPath(); g.arc(marker.p.x, marker.p.y, 1.5, 0, Math.PI * 2); g.fill();
    }
  }
}

function nudgeMarkers(list) {
  for (const marker of list) { marker.q = { x: marker.p.x, y: marker.p.y }; marker.displaced = false; }
  if (list.length < 2) return;
  const target = (a, b) => Math.max(30, RING_REACH(a.player.team) + RING_REACH(b.player.team) + 6);
  const active = [];
  for (let i = 0; i < list.length; i += 1) {
    for (let j = i + 1; j < list.length; j += 1) {
      const a = list[i];
      const b = list[j];
      const t = target(a, b);
      const d0 = Math.hypot(a.p.x - b.p.x, a.p.y - b.p.y);
      if (d0 >= t) continue;
      if (d0 + 2 * NUDGE_CAP < t) continue;
      active.push([i, j, t]);
    }
  }
  for (let iter = 0; iter < 60 && active.length; iter += 1) {
    let moved = false;
    for (const [i, j, t] of active) {
      const a = list[i];
      const b = list[j];
      let dx = b.q.x - a.q.x;
      let dy = b.q.y - a.q.y;
      let d = Math.hypot(dx, dy);
      if (d >= t - 0.05) continue;
      if (d < 0.01) {
        const angle = (i * 2.399963) % (Math.PI * 2);
        dx = Math.cos(angle);
        dy = Math.sin(angle);
        d = 1;
      }
      const push = (t - d) / 2 + 0.01;
      const ux = dx / d;
      const uy = dy / d;
      const move = (marker, sx, sy) => {
        let x = marker.q.x + sx;
        let y = marker.q.y + sy;
        const ox = x - marker.p.x;
        const oy = y - marker.p.y;
        const od = Math.hypot(ox, oy);
        if (od > NUDGE_CAP) { x = marker.p.x + ox / od * NUDGE_CAP; y = marker.p.y + oy / od * NUDGE_CAP; }
        marker.q = { x, y };
      };
      move(a, -ux * push, -uy * push);
      move(b, ux * push, uy * push);
      moved = true;
    }
    if (!moved) break;
  }
  for (const marker of list) marker.displaced = Math.hypot(marker.q.x - marker.p.x, marker.q.y - marker.p.y) > 1;
}

// Death records per round: latest kill event per victim, else first alive==0 sample (v1 rule).
function computeDeaths() {
  st.deaths = new Map();
  if (!st.round) return;
  const where = (track, at) => {
    const point = sampleTrack({ ...track, alive: undefined }, Math.max(0, clamp(at, 0, duration()) - 1), st.round.tickMs, st.round.frames);
    return point ? { x: point.x, y: point.y } : {};
  };
  const kills = (st.round.events || []).filter((event) => event.type === 'kill' && Number.isFinite(event.t) && event.target != null).sort((a, b) => a.t - b.t);
  for (const event of kills) {
    const track = st.round.players?.[event.target];
    if (track) st.deaths.set(String(event.target), { t: event.t, ...where(track, event.t) });
  }
  for (const [pid, track] of Object.entries(st.round.players || {})) {
    if (st.deaths.has(pid) || !Array.isArray(track.alive)) continue;
    const first = track.alive.findIndex((value) => value === 0);
    if (first > 0) st.deaths.set(pid, { t: first * st.round.tickMs, ...where(track, first * st.round.tickMs) });
  }
}

function updateAria(counts) {
  if (!st.round) return;
  const meta = st.rounds[st.roundIndex];
  const key = [counts.alive[0], counts.alive[1], counts.alive.u, st.round.n].join('/');
  if (key === st.ariaKey && !st.ariaForce) return;
  st.ariaKey = key;
  st.ariaForce = false;
  const parts = [`${sideLabel(meta, 0)}: ${counts.alive[0]} of ${counts.total[0]} alive`, `${sideLabel(meta, 1)}: ${counts.alive[1]} of ${counts.total[1]} alive`];
  if (counts.total.u) parts.push(`${counts.alive.u} of ${counts.total.u} unassigned alive`);
  st.els.actors.setAttribute('aria-label', `Round ${st.round.n}, ${formatClock(st.elapsed)}. ${parts.join('. ')}.`);
  st.els.hudAlive.textContent = st.projection ? `A ${counts.alive[0]}/${counts.total[0]} · B ${counts.alive[1]}/${counts.total[1]}` : '';
}

function drawActors() {
  if (!st || !st.els.actors) return;
  if (Math.min(window.devicePixelRatio || 1, 2) !== st.dpr) { resize(); return; }
  const canvas = st.els.actors;
  const g = canvas.getContext('2d');
  const { w, h } = st.view;
  g.clearRect(0, 0, w, h);
  st.visibleMarkers = [];
  st.utilHits = [];
  st.labels = { placed: [], omitted: [], badges: [], states: [] };
  if (!st.round || !st.match || !st.projection) {
    canvas.setAttribute('aria-label', st.projection ? 'Player positions and spike on map' : 'Player positions unavailable: map transform missing');
    return;
  }
  const t = st.elapsed;
  const live = [];
  const gone = [];
  const counts = { alive: { 0: 0, 1: 0, u: 0 }, total: { 0: 0, 1: 0, u: 0 } };
  const frameIndex = clamp(Math.round(t / (st.round.tickMs || 100)), 0, Math.max(0, Number(st.round.frames) - 1));

  for (const player of st.match.players || []) {
    const track = st.round.players?.[player.id];
    if (!track) continue;
    const color = teamColorOf(player.team);
    const group = player.team === 0 || player.team === 1 ? player.team : 'u';
    counts.total[group] += 1;

    if (st.layers.trails) {
      g.strokeStyle = color;
      g.globalAlpha = 0.42;
      g.lineWidth = 2;
      g.beginPath();
      let started = false;
      for (let trailT = Math.max(0, t - 2500); trailT <= t; trailT += Math.max(50, st.round.tickMs || 100)) {
        const point = sample(track, trailT);
        const p = point && project(point.x, point.y);
        if (!p) { started = false; continue; }
        if (!started) { g.moveTo(p.x, p.y); started = true; } else g.lineTo(p.x, p.y);
      }
      g.stroke();
      g.globalAlpha = 1;
    }

    const death = st.deaths.get(String(player.id));
    const dead = death && death.t <= t && !(Array.isArray(track.alive) && track.alive[frameIndex] === 1 && t - death.t > 2000);
    if (dead) {
      const dp = Number.isFinite(death.x) && Number.isFinite(death.y) ? project(death.x, death.y) : null;
      if (dp) gone.push({ p: dp, player, since: t - death.t });
      continue;
    }
    const point = sample(track, t);
    const p = point && project(point.x, point.y);
    if (!p) continue;
    counts.alive[group] += 1;
    let angle = null;
    if (point.yaw !== null) {
      const rad = point.yaw * Math.PI / 180;
      const tip = project(point.x + Math.cos(rad) * 260, point.y + Math.sin(rad) * 260);
      if (tip) angle = Math.atan2(tip.y - p.y, tip.x - p.x);
    }
    live.push({ p, q: p, name: player.agent || player.id, color, hp: point.hp, player, angle });
  }

  st.visibleMarkers = live.map(({ p, name, color, hp }) => ({ p, name, color, hp }));
  st.counts = counts;

  if (st.artReady) {
    for (const { p, player, since } of gone) {
      const sprite = spriteFor(player, since <= 1500 ? 'dead' : 'x');
      blitSprite(g, sprite, p.x, p.y, null);
    }
    live.sort((a, b) => a.p.y - b.p.y);
    nudgeMarkers(live);
    paintMarkers(g, live);
    drawLabels(g, live, w, h, gone);
    paintTicks(g, live);
  }
  drawAbilities(g, t);
  drawUtility(g, t);
  drawSpike(g, t);
  updateAria(counts);
}

function labelWidth(name) {
  let value = st.widthCache.get(name);
  if (value === undefined) {
    value = st.els.actors.getContext('2d').measureText(name).width;
    st.widthCache.set(name, value);
  }
  return value;
}

function drawLabels(g, markers, w, h, gone) {
  const log = st.labels = { placed: [], omitted: [], badges: [], states: [], crowded: [], rects: [], deadRects: [] };
  g.font = '600 11px sans-serif';
  g.textAlign = 'center';
  g.textBaseline = 'alphabetic';
  const overlapArea = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left))
    * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
  const bodies = markers.map(({ q, player }) => {
    const r = RING_REACH(player.team) + 0.5;
    return { left: q.x - r, right: q.x + r, top: q.y - r, bottom: q.y + r };
  });
  const deadBodies = gone.map(({ p, player }) => {
    const r = RING_REACH(player.team) + 2;
    return { left: p.x - r - 8, right: p.x + r + 8, top: p.y - r - 8, bottom: p.y + r + 8 };
  });
  const occupied = [];
  const near = BADGE_DIST;
  const parent = markers.map((_, index) => index);
  const find = (index) => {
    let i = index;
    while (parent[i] !== i) i = parent[i] = parent[parent[i]];
    return i;
  };
  for (let i = 0; i < markers.length; i += 1) {
    for (let j = i + 1; j < markers.length; j += 1) {
      if (Math.hypot(markers[i].q.x - markers[j].q.x, markers[i].q.y - markers[j].q.y) <= near) parent[find(i)] = find(j);
    }
  }
  const groups = new Map();
  markers.forEach((marker, index) => {
    const root = find(index);
    if (!groups.has(root)) groups.set(root, []);
    groups.get(root).push(index);
  });
  const clustered = new Map();
  for (const members of groups.values()) {
    if (members.length < 3) continue;
    const badgeId = `badge${log.badges.length}`;
    members.forEach((index) => clustered.set(index, badgeId));
    const cx = members.reduce((sum, index) => sum + markers[index].q.x, 0) / members.length;
    const top = Math.min(...members.map((index) => markers[index].q.y));
    const bx = clamp(cx, 18, w - 18);
    const by = clamp(top - 40, 12, h - 12);
    g.fillStyle = '#101820ee';
    g.fillRect(bx - 15, by - 9, 30, 17);
    g.strokeStyle = '#e8eef2';
    g.lineWidth = 1;
    g.strokeRect(bx - 14.5, by - 8.5, 29, 16);
    g.fillStyle = '#fff';
    g.fillText(`+${members.length}`, bx, by + 4);
    occupied.push({ left: bx - 15, right: bx + 15, top: by - 9, bottom: by + 8 });
    log.badges.push({ id: badgeId, members: members.map((index) => markers[index].player.id), x: bx, y: by });
  }
  markers.forEach(({ player }, index) => {
    if (clustered.has(index)) log.states.push({ id: player.id, state: 'in-badge', badge: clustered.get(index) });
  });
  markers.forEach(({ q, name, color, player }, index) => {
    if (clustered.has(index)) return;
    const width = labelWidth(name) + 12;
    const side = width / 2 + 31;
    const base = [[0, -39], [0, 50], [-side, 0], [side, 0], [-side, -34], [side, -34], [-side, 34], [side, 34],
      [0, -57], [0, 64], [-side - 16, 0], [side + 16, 0], [-side, -52], [side, -52], [-side, 52], [side, 52], [0, -75], [0, 82]];
    let best = null;
    for (const [dx, dy] of base) {
      const x = q.x + dx;
      const y = q.y + dy;
      const box = { left: x - width / 2, right: x + width / 2, top: y - 9, bottom: y + 7 };
      if (box.left < 4 || box.right > w - 4 || box.top < 4 || box.bottom > h - 4) continue;
      // The label pass follows portraits; never place a scrim over a marker.
      if (bodies.some((body) => overlapArea(box, body) > 0)
        || deadBodies.some((body) => overlapArea(box, body) > 0)) continue;
      let cost = 0;
      for (const other of occupied) cost += overlapArea(box, other) * 20;
      for (const body of bodies) cost += overlapArea(box, body);
      for (const body of deadBodies) cost += overlapArea(box, body) * 20;
      if (!best || cost < best.cost) best = { cost, dx, dy, x, y, box };
      if (cost === 0) break;
    }
    if (!best) {
      const reason = 'no candidate label box fits inside the canvas';
      log.omitted.push({ id: player.id, reason });
      log.states.push({ id: player.id, state: 'omitted', reason });
      return;
    }
    const { dx, dy, x, y, box } = best;
    const len = Math.hypot(dx, dy) || 1;
    const start = dy > 0 && Math.abs(dx) < 14 ? 38 : 30;
    g.strokeStyle = color;
    g.globalAlpha = 0.6;
    g.lineWidth = 1;
    g.beginPath();
    g.moveTo(q.x + dx / len * start, q.y + dy / len * start);
    g.lineTo(x, y);
    g.stroke();
    g.globalAlpha = 1;
    g.fillStyle = 'rgba(11,15,22,.78)';
    g.beginPath();
    g.roundRect(box.left, box.top, width, 16, 3);
    g.fill();
    g.fillStyle = color;
    g.fillRect(box.left, box.top, 2, 16);
    g.fillStyle = '#fff';
    g.textAlign = 'left';
    g.fillText(name, box.left + 7, y + 3);
    g.textAlign = 'center';
    occupied.push(box);
    log.rects.push({ id: player.id, left: box.left, right: box.right, top: box.top, bottom: box.bottom });
    log.placed.push({ id: player.id, team: player.team ?? null, name });
    log.states.push({ id: player.id, state: 'labelled' });
  });
}

function drawAbilities(g, t) {
  if (!st.layers.abilities || !st.round) return;
  for (const event of st.round.events || []) {
    if (!['ability', 'ult'].includes(event.type) || !Number.isFinite(event.t)) continue;
    const lifetime = event.type === 'ult' ? 5500 : 3500;
    const age = t - event.t;
    if (age < 0 || age > lifetime) continue;
    const track = st.round.players?.[event.actor];
    const point = Number.isFinite(event.x) && Number.isFinite(event.y)
      ? { x: event.x, y: event.y }
      : track && sampleTrack({ ...track, alive: undefined }, event.t, st.round.tickMs, st.round.frames);
    const p = point && project(point.x, point.y);
    if (!p) continue;
    const label = typeof event.ability === 'string' ? event.ability : '';
    const bucket = [...label].reduce((sum, char) => sum + char.charCodeAt(0), 0) % ABILITY_PALETTE.length;
    const color = event.type === 'ult' ? '#d48eff' : ABILITY_PALETTE[bucket];
    g.globalAlpha = 0.3 + 0.7 * (1 - age / lifetime);
    g.strokeStyle = color;
    g.fillStyle = '#19252e';
    g.lineWidth = 2;
    const radius = event.type === 'ult' ? 13 : 10;
    g.beginPath(); g.arc(p.x, p.y, radius, 0, Math.PI * 2); g.fill(); g.stroke();
    const player = st.byId.get(String(event.actor));
    const iconUrl = player && player.agent && event.ability ? iconFor(player.agent, event.ability) : null;
    if (iconUrl) {
      const image = abilityImage(iconUrl);
      if (image && image.complete && image.naturalWidth) g.drawImage(image, p.x - 8, p.y - 8, 16, 16);
    } else {
      g.fillStyle = color;
      g.font = 'bold 13px sans-serif';
      g.textAlign = 'center';
      g.fillText(event.type === 'ult' ? '✦' : '●', p.x, p.y + 5);
    }
    g.globalAlpha = 1;
  }
}

function abilityImage(url) {
  if (!st.abilityIcons.has(url)) {
    const image = new Image();
    image.onload = () => requestDraw();
    image.src = url;
    st.abilityIcons.set(url, image);
  }
  return st.abilityIcons.get(url);
}

// --- utility layer (ported from utility.js: paths, bounces, landings, effect areas, hit test) ---

function utilityVisible(item, t) {
  let end = item.t;
  for (const bound of [item.effect && item.effect.t1, item.endT, item.land && item.land.t]) {
    if (typeof bound === 'number' && Number.isFinite(bound)) end = Math.max(end, bound);
  }
  return item.t <= t && t <= end;
}

function utilityLabel(item) {
  const parts = [display(item.agent, 'Unknown'), display(item.ability, item.kind || 'Utility')];
  const bounces = Array.isArray(item.bounces) ? item.bounces.length : 0;
  if (bounces > 0) parts.push(`${bounces} bounce${bounces === 1 ? '' : 's'}`);
  const landMs = item.land && Number.isFinite(item.land.t) ? item.land.t - item.t : NaN;
  if (Number.isFinite(landMs)) parts.push(`landed ${(landMs / 1000).toFixed(1)} s after throw`);
  parts.push(item.confidence === 'unowned' || item.actor == null ? 'unowned' : 'recorded');
  return parts.join(' · ');
}

function utilityIcon(g, x, y, color, item) {
  const url = iconFor(item.agent, item.ability);
  if (url) {
    const image = abilityImage(url);
    if (image && image.complete && image.naturalWidth) {
      g.drawImage(image, x - 8, y - 8, 16, 16);
      return;
    }
  }
  g.save();
  g.translate(x, y);
  g.rotate(Math.PI / 4);
  g.fillStyle = color;
  g.fillRect(-5, -5, 10, 10);
  g.restore();
}

function drawUtility(g, t) {
  const started = performance.now();
  const stats = st.utilStats = { paths: 0, landings: 0, areas: 0, bounces: 0, unowned: 0, tMs: t, lastDrawMs: 0 };
  if (!st.round) { stats.lastDrawMs = performance.now() - started; return; }
  const items = Array.isArray(st.round.utility) ? st.round.utility : [];
  const showPaths = st.layers.paths;
  const showAreas = st.layers.areas;
  if (!showPaths && !showAreas) { stats.lastDrawMs = performance.now() - started; return; }
  const rawTeam = String(st.filters.team || 'all');
  const teamFilter = rawTeam === 'attackers' ? '0' : rawTeam === 'defenders' ? '1' : rawTeam;
  const playerFilter = st.filters.player || '';

  for (const item of items) {
    if (!item || item.kind === 'state' || !utilityVisible(item, t)) continue;
    const player = st.byId.get(String(item.actor));
    const unowned = item.actor == null;
    if (unowned) stats.unowned += 1;
    const color = player && player.team === 1 ? TEAM_B : player && player.team === 0 ? TEAM_A : NEUTRAL;
    if (teamFilter !== 'all' && !unowned && (!player || String(player.team) !== String(teamFilter))) continue;
    if (playerFilter && !unowned && item.actor !== playerFilter) continue;

    let hot = null;

    if (showPaths && item.route === 'path' && Array.isArray(item.path) && item.path.length) {
      const points = [];
      for (const entry of item.path) {
        if (!Array.isArray(entry) || entry.length < 3) continue;
        if (item.t + entry[0] > t) break;
        const p = project(entry[1], entry[2]);
        if (p) points.push(p);
      }
      if (points.length > 1) {
        g.save();
        g.strokeStyle = color;
        g.lineWidth = 2;
        g.globalAlpha = item.kind === 'wall' ? 0.55 : 0.9;
        if (item.kind !== 'wall') g.setLineDash([5, 4]);
        g.lineJoin = 'round';
        g.lineCap = 'round';
        g.beginPath();
        g.moveTo(points[0].x, points[0].y);
        for (let i = 1; i < points.length; i += 1) g.lineTo(points[i].x, points[i].y);
        g.stroke();
        g.restore();
        stats.paths += 1;
      }
      const head = points.length ? points[points.length - 1] : null;
      if (head && (!item.land || t < item.land.t)) utilityIcon(g, head.x, head.y, color, item);
      if (Array.isArray(item.bounces)) {
        for (const bounce of item.bounces) {
          if (!Array.isArray(bounce) || bounce.length < 3 || item.t + bounce[0] > t) continue;
          const p = project(bounce[1], bounce[2]);
          if (!p) continue;
          g.save();
          g.lineJoin = 'miter';
          for (const [stroke, width] of [['#0d151c', 5], ['#fff', 2.5]]) {
            g.strokeStyle = stroke;
            g.lineWidth = width;
            g.beginPath();
            g.moveTo(p.x - 4.5, p.y - 4.5); g.lineTo(p.x + 4.5, p.y + 4.5);
            g.moveTo(p.x + 4.5, p.y - 4.5); g.lineTo(p.x - 4.5, p.y + 4.5);
            g.stroke();
          }
          g.restore();
          stats.bounces += 1;
        }
      }
      if (head) hot = head;
    }

    if (showAreas) {
      const land = item.land;
      const landP = land && Number.isFinite(land.x) && Number.isFinite(land.y) ? project(land.x, land.y) : null;
      const effect = item.effect;
      let areaP = null;
      let areaR = 0;
      let areaAlpha = 0;
      if (effect && Number.isFinite(effect.t0) && Number.isFinite(effect.t1) && t >= effect.t0) {
        areaP = Number.isFinite(effect.x) && Number.isFinite(effect.y) ? project(effect.x, effect.y) : null;
        if (areaP && Number.isFinite(effect.r) && effect.r > 0) {
          const probe = project(effect.x + 100, effect.y);
          if (probe) {
            const scale = Math.hypot(probe.x - areaP.x, probe.y - areaP.y) / 100;
            areaR = effect.r * scale;
            areaAlpha = Math.max(0, 0.35 * (1 - (t - effect.t0) / (effect.t1 - effect.t0)));
          }
        }
      }
      if (areaP && areaR > 0 && areaAlpha > 0) {
        g.save();
        g.globalAlpha = areaAlpha;
        g.fillStyle = color;
        g.beginPath(); g.arc(areaP.x, areaP.y, areaR, 0, Math.PI * 2); g.fill();
        g.restore();
        stats.areas += 1;
      }
      if (landP && (!item.land || t >= item.land.t)) {
        utilityIcon(g, landP.x, landP.y, color, item);
        stats.landings += 1;
        hot = landP;
      } else if (!hot && areaP) {
        hot = areaP;
      }
    }
    if (hot) st.utilHits.push({ item, x: hot.x, y: hot.y });
  }
  stats.lastDrawMs = performance.now() - started;
}

function drawSpike(g, t) {
  const spike = st.round && st.round.spike;
  if (!spike || !Number.isFinite(spike.plantT) || t < spike.plantT) return;
  if (Number.isFinite(spike.defuseT) && t >= spike.defuseT) return;
  const p = project(spike.plantX, spike.plantY);
  if (!p) return;
  g.strokeStyle = '#ffcf6b';
  g.lineWidth = 3;
  g.beginPath(); g.arc(p.x, p.y, 13 + 3 * Math.sin(t / 180), 0, Math.PI * 2); g.stroke();
  g.fillStyle = '#ffcf6b';
  g.font = '20px sans-serif';
  g.textAlign = 'center';
  g.fillText('◆', p.x, p.y + 7);
}

// ---------------------------------------------------------------------------------------------
// Pointer tooltips
// ---------------------------------------------------------------------------------------------

function onPointerMove(event) {
  if (!st || !st.round) return;
  const rect = st.els.stage.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  const nearby = st.visibleMarkers.filter(({ p }) => Math.hypot(p.x - x, p.y - y) <= 16);
  st.els.actorTip.hidden = nearby.length === 0;
  if (!nearby.length) {
    let best = null;
    let bestD = 14 * 14;
    for (const hit of st.utilHits) {
      const d = (hit.x - x) * (hit.x - x) + (hit.y - y) * (hit.y - y);
      if (d <= bestD) { bestD = d; best = hit; }
    }
    st.els.utilTip.hidden = !best;
    if (best) {
      st.els.utilTip.textContent = utilityLabel(best.item);
      st.els.utilTip.style.left = `${clamp(x + 16, 8, Math.max(8, rect.width - 240))}px`;
      st.els.utilTip.style.top = `${clamp(y - 12, 8, Math.max(8, rect.height - 40))}px`;
    }
    return;
  }
  st.els.utilTip.hidden = true;
  st.els.actorTip.replaceChildren(...nearby.map(({ name, color, hp }) =>
    h('span', { class: 'replay__tip-line', style: `color:${color}` }, `${name}${hp === null ? '' : ` · ${hp} HP`}`)));
  st.els.actorTip.style.left = `${clamp(x + 16, 8, Math.max(8, rect.width - 140))}px`;
  st.els.actorTip.style.top = `${clamp(y - 12, 8, Math.max(8, rect.height - 40))}px`;
}

function onStageClick(event) {
  if (!st || !st.round) return;
  const rect = st.els.stage.getBoundingClientRect();
  const x = event.clientX - rect.left;
  const y = event.clientY - rect.top;
  let best = null;
  let bestD = 14 * 14;
  for (const hit of st.utilHits) {
    const d = (hit.x - x) * (hit.x - x) + (hit.y - y) * (hit.y - y);
    if (d <= bestD) { bestD = d; best = hit; }
  }
  if (best && Number.isFinite(best.item.t)) seek(best.item.t);
}

// ---------------------------------------------------------------------------------------------
// QA hooks (cheap versions of v1 window.replayPerf / window.UtilityLayer.debugState)
// ---------------------------------------------------------------------------------------------

function installQaHooks() {
  const state = st;
  state.qaInstalled = true;
  window.replayPerf = {
    debug() {
      if (st !== state || !state.round) return null;
      return {
        round: state.round.n, tMs: Math.round(state.elapsed), dpr: state.dpr,
        sprites: state.sprites.size, artReady: state.artReady, markers: state.visibleMarkers.length,
        counts: JSON.parse(JSON.stringify(state.counts)),
        labels: JSON.parse(JSON.stringify(state.labels)),
        utility: { ...state.utilStats },
      };
    },
    // Redraws the marker layer `frames` times and returns percentiles (mutates elapsed, restores).
    measureMarkers(frames = 120) {
      if (st !== state || !state.round) return null;
      const keep = state.elapsed;
      const times = [];
      const span = Math.max(1000, Math.min(duration() - 1000, 25000));
      for (let i = 0; i < frames; i += 1) {
        if (st !== state) return null;
        state.elapsed = 2000 + (i * 137) % span;
        const started = performance.now();
        drawActors();
        state.els.actors.getContext('2d').getImageData(0, 0, 1, 1);
        times.push(performance.now() - started);
      }
      state.elapsed = keep;
      renderTime();
      drawActors();
      times.sort((a, b) => a - b);
      const rank = (fraction) => times[Math.min(times.length - 1, Math.floor(fraction * times.length))];
      return { frames, dpr: state.dpr, p50: rank(0.5), p95: rank(0.95), max: times[times.length - 1] };
    },
  };
  window.UtilityLayer = {
    debugState: () => (st === state ? { ...state.utilStats } : null),
  };
}

// ---------------------------------------------------------------------------------------------
// Frame loop
// ---------------------------------------------------------------------------------------------

function frame(now) {
  if (!st || st.destroyed) return;
  st.raf = requestAnimationFrame(frame);
  if (st.playing && st.round) {
    const dt = st.lastFrame ? Math.min(now - st.lastFrame, 150) : 0;
    st.lastFrame = now;
    if (dt > 0) {
      st.elapsed = Math.min(duration(), st.elapsed + dt * st.speed);
      renderTime();
      drawActors();
      renderFeed();
      scheduleUrl();
      if (st.elapsed >= duration()) setPlaying(false);
    }
    return;
  }
  st.lastFrame = now;
  if (st.dirty) {
    st.dirty = false;
    drawActors();
  }
}

// ---------------------------------------------------------------------------------------------
// Space module
// ---------------------------------------------------------------------------------------------

async function mount(root, ctx) {
  if (st) unmount();
  st = createState(root, ctx);
  buildDom();
  wire();
  installQaHooks();
  st.raf = requestAnimationFrame(frame);
  await load();
}

function unmount() {
  if (!st) return;
  const state = st;
  st = null;
  state.destroyed = true;
  cancelAnimationFrame(state.raf);
  cancelAnimationFrame(state.resizeRaf);
  if (state.urlTimer) clearTimeout(state.urlTimer);
  if (state.ro) state.ro.disconnect();
  if (state.resizeRafCleanup) state.resizeRafCleanup();
  try { state.listeners.abort(); } catch { /* ignore */ }
  state.roundCache.clear();
  state.loadingRounds.clear();
  state.sprites.clear();
  state.portraits.clear();
  state.abilityIcons.clear();
  if (state.qaInstalled) {
    delete window.replayPerf;
    delete window.UtilityLayer;
  }
  if (state.root) state.root.replaceChildren();
}

function onKey(event) {
  if (!st || !event) return false;
  if (event.key === 'Escape') { onEscape(); return true; }
  return false;
}

export default {
  id: 'replay',
  title: 'Replay',
  accent: 'replay',
  mount,
  unmount,
  onKey,
};
