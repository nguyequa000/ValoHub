// ValoHub UI v2 — Library space (`/replays`), DESIGN-V2 §4.1.
//
// Archive grid (search, filter chips derived from loaded data, sort, grid/list), selected-card
// preview pane (summary, top duelists, last coach verdict, Open replay / Analyze), import flow
// (drag-drop anywhere, file picker, Import button, SSE stage progress, cancel, per-file errors),
// hero empty state, skeleton loading, error card with Retry, demo fallback.
//
// Contract notes (SHELL-CONTRACT.md + the frozen-seams comment on the task):
//   - No innerHTML anywhere; every API-derived string goes through el() (textContent).
//   - Render only fields the API returns. GET /api/replays has no score/result/agents: those
//     render only when present; a score is never invented. `stale` is a boolean.
//   - Import: the backend takes a RAW body on `PUT /api/replays/upload?name=` (not multipart) and
//     shell.js has no `upload` yet, so the upload is a local XHR with the .vrf as the body.
//     It reports progress and is abortable.
//   - Progress: `/api/jobs/stream` SSE (`progress` events, matched by jobId). `/api/jobs` is
//     re-read after an upload and on stream reconnect so a finished job whose event was missed
//     still resolves.
//   - Ctrl+O: registered with ctx.shortcuts. In Electron the main process owns that accelerator
//     (desktop/electron/src/main.js before-input-event) and the renderer never receives it.
//   - Async work captures its own session (`me`) so a late response after unmount never touches
//     a newer mount.

import { el, ApiError } from '../shell.js';

const MAP_ASSETS = '/static/replays_app/assets/maps/';
const MAP_RE = /^https:\/\/media\.valorant-api\.com\/maps\/([\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12})\/displayicon\.png$/i;
const USABLE = new Set(['ready', 'timeline', 'suspect', 'stale']);
// GET /api/replays returns no score field, so there is no Score sort to offer: a control that
// silently cannot order anything is worse than no control.
const SORTS = [['recent', 'Recent'], ['duration', 'Duration']];
const FACETS = [['map', 'Map'], ['result', 'Result'], ['agent', 'Agent'], ['patch', 'Patch']];
const STAGES = [['upload', 'Upload'], ['parse', 'Parse'], ['normalize', 'Normalize'], ['ready', 'Ready']];
const DUELIST_ROUND_CAP = 40;
const DUELIST_CONCURRENCY = 4;
const RECONNECT_MS = 3000;

let s = null; // current mount session; null when unmounted

const enc = (v) => encodeURIComponent(String(v));

function errText(err) {
  if (err instanceof ApiError) {
    if (err.status >= 500 || err.status === 0) {
      return err.requestId ? `Server error (request ${err.requestId})` : 'Backend unreachable';
    }
    return err.message || 'Request failed';
  }
  return 'Something went wrong';
}

function stageIndex(stage) {
  const v = String(stage || '').toLowerCase();
  if (['receive', 'upload', 'queued'].includes(v)) return 0;
  if (['parse', 'decode', 'export', 'parsing', 'decoding'].includes(v)) return 1;
  if (['normalize', 'normalizing'].includes(v)) return 2;
  if (['done', 'ready', 'complete'].includes(v)) return 3;
  return -1;
}

function fmtDuration(ms) {
  const n = Number(ms);
  if (!Number.isFinite(n) || n <= 0) return 'Duration unknown';
  const total = Math.round(n / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`;
}

function fmtDate(iso) {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function isUsable(r) {
  return USABLE.has(String(r.status || '').toLowerCase());
}

function gateReason(r) {
  const st = String(r.status || '').toLowerCase();
  if (st === 'failed') return r.error || r.failureReason || r.qualityReason || 'Decoder could not read this file. Re-import to try again.';
  if (!st || ['processing', 'queued', 'running'].includes(st)) return 'Still processing.';
  return 'Not ready yet.';
}

function scoreText(r) {
  if (typeof r.score === 'string' && r.score.trim()) return r.score.trim();
  if (typeof r.score === 'number' && Number.isFinite(r.score)) return String(r.score);
  return null;
}

function resultText(r) {
  return typeof r.result === 'string' && r.result.trim() ? r.result.trim().toUpperCase() : null;
}

function agentsOf(r) {
  return Array.isArray(r.agents) ? r.agents.filter((a) => typeof a === 'string' && a) : [];
}

function facetValues(r, facet) {
  if (facet === 'map') return [r.mapName || 'Unknown map'];
  if (facet === 'patch') return [r.patch || '—'];
  if (facet === 'result') return resultText(r) ? [resultText(r)] : [];
  if (facet === 'agent') return agentsOf(r);
  return [];
}

function facetOptions(facet) {
  const counts = new Map();
  for (const r of s.replays) for (const v of facetValues(r, facet)) counts.set(v, (counts.get(v) || 0) + 1);
  return [...counts.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0])).slice(0, 10).map(([v]) => v);
}

function activeFilterCount() {
  return Object.values(s.filters).reduce((n, set) => n + set.size, 0) + (s.query.trim() ? 1 : 0);
}

function visibleReplays() {
  const q = s.query.trim().toLowerCase();
  let list = s.replays.filter((r) => {
    if (q) {
      const hay = [r.mapName, r.patch, r.id, agentsOf(r).join(' ')].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    for (const [facet, values] of Object.entries(s.filters)) {
      if (values.size && !facetValues(r, facet).some((v) => values.has(v))) return false;
    }
    return true;
  });
  if (s.sort === 'duration') {
    list = [...list].sort((a, b) => (Number(b.durationMs) || 0) - (Number(a.durationMs) || 0));
  }
  return list;
}

// ---------------------------------------------------------------------------------------------
// Rendering (sync; only ever called for the live session)
// ---------------------------------------------------------------------------------------------

function buildShell(root) {
  const search = el('input', {
    type: 'search', class: 'field lib-search', 'data-testid': 'library-search',
    placeholder: 'Search map, patch, agent or id  ( / )', 'aria-label': 'Search replays', autocomplete: 'off',
  });
  const filters = el('div', { class: 'lib-filters', role: 'group', 'aria-label': 'Filters', 'data-testid': 'library-filters' });
  const sort = el('select', { class: 'select lib-sort', 'data-testid': 'library-sort', 'aria-label': 'Sort replays' },
    SORTS.map(([v, label]) => el('option', { value: v }, label)));
  const gridBtn = el('button', { type: 'button', class: 'chip', 'data-view': 'grid', 'aria-pressed': 'false' }, 'Grid');
  const listBtn = el('button', { type: 'button', class: 'chip', 'data-view': 'list', 'aria-pressed': 'false' }, 'List');
  const viewToggle = el('div', { class: 'segmented lib-view', role: 'group', 'aria-label': 'View', 'data-testid': 'library-view-toggle' }, gridBtn, listBtn);
  const importBtn = el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'library-import', 'aria-keyshortcuts': 'Control+O' }, 'Import');
  const refreshBtn = el('button', { type: 'button', class: 'icon-btn', 'data-testid': 'library-refresh', 'aria-label': 'Refresh library', title: 'Refresh' }, '↻');
  const fileInput = el('input', { type: 'file', accept: '.vrf', multiple: true, class: 'lib-file-input', 'aria-hidden': 'true', tabindex: '-1' });
  const count = el('span', { class: 'lib-count', 'aria-live': 'polite' });
  const toolbar = el('header', { class: 'lib-toolbar' },
    el('div', { class: 'lib-toolbar__row' }, search, sort, viewToggle, importBtn, refreshBtn, fileInput),
    el('div', { class: 'lib-toolbar__row lib-toolbar__row--sub' }, filters, count));
  const grid = el('div', { class: 'lib-grid', role: 'listbox', 'aria-label': 'Replays', 'data-testid': 'library-grid' });
  const imports = el('section', { class: 'lib-imports', 'aria-label': 'Imports', 'aria-live': 'polite', 'data-testid': 'library-imports' });
  imports.hidden = true;
  const main = el('section', { class: 'lib-main', 'aria-label': 'Archive' }, grid, imports);
  const preview = el('aside', { class: 'lib-preview', 'aria-label': 'Replay preview', 'data-testid': 'library-preview' });
  const veil = el('div', { class: 'lib-veil', 'aria-hidden': 'true', 'data-testid': 'library-drop-overlay' }, 'Drop .vrf files to import');
  veil.hidden = true;
  const body = el('div', { class: 'lib-body' }, main, preview);
  const wrap = el('div', { class: 'lib', 'data-testid': 'library' }, toolbar, body, veil);
  root.replaceChildren(wrap);
  return { wrap, search, filters, sort, gridBtn, listBtn, viewToggle, importBtn, refreshBtn, fileInput, count, grid, imports, preview, veil };
}

function renderToolbar() {
  const list = s.view === 'list';
  s.ui.gridBtn.setAttribute('aria-pressed', String(!list));
  s.ui.listBtn.setAttribute('aria-pressed', String(list));
  s.ui.grid.classList.toggle('lib-grid--list', list);
  s.ui.sort.value = s.sort;
}

function renderFilters(focusKey) {
  const nodes = [];
  for (const [facet, label] of FACETS) {
    const opts = facetOptions(facet);
    if (!opts.length) continue;
    nodes.push(el('span', { class: 'lib-facet-label' }, label));
    for (const value of opts) {
      const on = s.filters[facet].has(value);
      nodes.push(el('button', {
        type: 'button', class: on ? 'chip chip--active' : 'chip', 'aria-pressed': String(on),
        'data-facet': facet, 'data-value': value,
        onclick: () => toggleFilter(facet, value),
      }, value));
    }
  }
  if (activeFilterCount()) {
    nodes.push(el('button', { type: 'button', class: 'btn btn--ghost lib-clear', 'data-testid': 'library-clear-filters', onclick: clearFilters }, 'Clear filters'));
  }
  s.ui.filters.replaceChildren(...nodes);
  if (focusKey) {
    const again = [...s.ui.filters.querySelectorAll('button[data-facet]')]
      .find((b) => `${b.dataset.facet}\u0000${b.dataset.value}` === focusKey);
    if (again) again.focus();
  }
}

function toggleFilter(facet, value) {
  const set = s.filters[facet];
  if (set.has(value)) set.delete(value); else set.add(value);
  renderArchive(`${facet}\u0000${value}`);
}

function clearFilters() {
  for (const set of Object.values(s.filters)) set.clear();
  s.query = '';
  s.ui.search.value = '';
  renderArchive();
  s.ui.search.focus();
}

function renderArchive(focusKey) {
  renderToolbar();
  renderFilters(focusKey);
  const grid = s.ui.grid;
  s.cards = new Map();
  if (s.loading && !s.replays.length) {
    s.ui.count.textContent = 'Loading library…';
    grid.replaceChildren(...Array.from({ length: 6 }, () => el('div', {
      class: 'lib-skel', 'data-testid': 'library-skeleton', 'aria-busy': 'true', 'aria-hidden': 'true',
    }, el('div', { class: 'lib-skel__art' }), el('div', { class: 'lib-skel__line' }), el('div', { class: 'lib-skel__line lib-skel__line--short' }))));
  } else if (s.listError) {
    s.ui.count.textContent = 'Library unavailable';
    grid.replaceChildren(el('div', { class: 'lib-state card', role: 'alert', 'data-testid': 'library-error' },
      el('h2', { class: 'lib-state__title' }, 'The library could not be loaded'),
      el('p', { class: 'lib-state__body' }, s.listError),
      el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'library-retry', onclick: () => loadLibrary() }, 'Retry'),
      s.demos.length ? el('p', { class: 'lib-state__body' }, `${s.demos.length} demo replay${s.demos.length === 1 ? '' : 's'} can still be opened.`) : null,
      s.demos.length ? el('button', { type: 'button', class: 'btn', 'data-testid': 'library-open-demo', onclick: openDemo }, 'Open demo') : null));
  } else if (!s.replays.length) {
    s.ui.count.textContent = '0 replays';
    grid.replaceChildren(hero());
  } else {
    const list = visibleReplays();
    s.ui.count.textContent = activeFilterCount()
      ? `${list.length} of ${s.replays.length} replays`
      : `${s.replays.length} ${s.replays.length === 1 ? 'replay' : 'replays'}`;
    if (!list.length) {
      grid.replaceChildren(el('div', { class: 'lib-state card', 'data-testid': 'library-no-match' },
        el('h2', { class: 'lib-state__title' }, 'No replays match'),
        el('p', { class: 'lib-state__body' }, 'Try a different search or clear the filters.'),
        el('button', { type: 'button', class: 'btn', onclick: clearFilters }, 'Clear filters')));
    } else {
      const nodes = list.map((r) => {
        const node = cardFor(r);
        s.cards.set(r.id, node);
        return node;
      });
      grid.replaceChildren(...nodes);
    }
  }
  renderPreview();
}

function hero() {
  return el('div', { class: 'lib-hero card', 'data-testid': 'library-empty' },
    el('div', { class: 'lib-hero__art', 'aria-hidden': 'true' }, '▦'),
    el('h2', { class: 'lib-state__title' }, 'Import your first replay'),
    el('p', { class: 'lib-state__body' }, 'Drop a .vrf file anywhere in this space, or choose one. Imports are copied, never moved.'),
    el('div', { class: 'lib-hero__actions' },
      el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'library-empty-import', onclick: () => s.ui.fileInput.click() }, 'Import .vrf'),
      el('button', {
        type: 'button', class: 'btn', 'data-testid': 'library-open-demo',
        disabled: s.demos.length ? null : true, onclick: openDemo,
      }, s.demos.length ? 'Open demo' : 'Open demo (none installed)')));
}

function cardFor(r) {
  const sel = r.id === s.selectedId;
  const name = r.mapName || 'Unknown map';
  const thumb = el('div', { class: 'lib-thumb', 'aria-hidden': 'true' },
    el('span', { class: 'lib-thumb__ph' }, name.slice(0, 2).toUpperCase()));
  const mapId = typeof r.mapImage === 'string' ? r.mapImage.match(MAP_RE) : null;
  if (mapId) {
    const img = el('img', { src: `${MAP_ASSETS}${mapId[1]}.png`, alt: '', loading: 'lazy', decoding: 'async' });
    img.addEventListener('error', () => img.remove());
    thumb.append(img);
  }
  const badges = el('div', { class: 'lib-badges' });
  const result = resultText(r);
  if (result) badges.append(el('span', { class: `lib-badge lib-badge--${result === 'WIN' ? 'win' : result === 'LOSS' ? 'loss' : 'neutral'}` }, result));
  if (r.stale === true) badges.append(el('span', { class: 'lib-badge lib-badge--warn', title: 'Parsed with an older decoder; re-import to refresh' }, 'Stale'));
  if (!isUsable(r)) badges.append(el('span', { class: 'lib-badge lib-badge--neutral' }, String(r.status || 'processing')));
  const facts = el('dl', { class: 'lib-facts' });
  const addFact = (k, v) => { if (v) facts.append(el('dt', null, k), el('dd', null, v)); };
  addFact('Score', scoreText(r));
  addFact('Duration', r.durationMs ? fmtDuration(r.durationMs) : null);
  addFact('Patch', r.patch || '—');
  addFact('Date', fmtDate(r.createdAt));
  const agents = agentsOf(r);
  const body = el('div', { class: 'lib-card__body' },
    el('h3', { class: 'lib-card__title' }, name),
    badges, facts,
    agents.length ? el('p', { class: 'lib-agents' }, agents.join(' · ')) : null,
    !isUsable(r) ? el('p', { class: 'lib-note' }, gateReason(r)) : null);
  return el('div', {
    class: sel ? 'lib-card is-selected' : 'lib-card', role: 'option', 'aria-selected': sel ? 'true' : 'false',
    tabindex: sel ? '0' : '-1', 'data-testid': `library-card-${r.id}`, 'data-id': r.id,
    'aria-label': `${name}${result ? `, ${result}` : ''}${r.durationMs ? `, ${fmtDuration(r.durationMs)}` : ''}`,
    onclick: () => selectReplay(r.id),
    ondblclick: () => openReplay(r.id),
  }, thumb, body);
}

function renderSelection() {
  for (const [id, node] of s.cards) {
    const on = id === s.selectedId;
    node.classList.toggle('is-selected', on);
    node.setAttribute('aria-selected', String(on));
    node.setAttribute('tabindex', on ? '0' : '-1');
  }
}

function renderImports() {
  const jobs = [...s.jobs];
  s.ui.imports.hidden = jobs.length === 0;
  if (!jobs.length) { s.ui.imports.replaceChildren(); return; }
  const rows = jobs.map((job) => {
    const idx = stageIndex(job.stage);
    const steps = el('ol', { class: 'lib-stages', 'aria-label': 'Import stages' }, STAGES.map(([key, label], i) => {
      const done = job.status === 'done' || idx > i;
      const current = job.status !== 'done' && idx === i;
      const attrs = { class: `lib-stage${done ? ' is-done' : ''}${current ? ' is-current' : ''}`, 'data-stage': key };
      if (current) attrs['aria-current'] = 'step';
      return el('li', attrs, label);
    }));
    const pct = Math.max(0, Math.min(100, Math.round(job.percent || 0)));
    const bar = el('progress', { class: 'lib-progress', max: '100', value: String(pct), 'aria-label': `${job.name} progress` });
    const terminal = ['done', 'error', 'cancelled'].includes(job.status);
    const statusText = job.status === 'error' ? `Failed: ${job.error || job.message || 'import failed'}`
      : job.status === 'cancelled' ? 'Cancelled'
        : job.status === 'done' ? 'Ready in the archive'
          : (job.message || (job.status === 'uploading' ? 'Uploading…' : 'Queued'));
    const actions = el('div', { class: 'lib-job__actions' });
    if (job.status === 'uploading' && job.controller) {
      actions.append(el('button', { type: 'button', class: 'btn btn--ghost', 'aria-label': `Cancel import of ${job.name}`, onclick: () => cancelJob(job) }, 'Cancel'));
    }
    if (job.status === 'done' && job.replayId) {
      actions.append(el('button', { type: 'button', class: 'btn', onclick: () => selectReplay(job.replayId) }, 'Show'));
    }
    if (terminal) {
      actions.append(el('button', { type: 'button', class: 'btn btn--ghost', 'aria-label': `Dismiss ${job.name}`, onclick: () => dismissJob(job) }, 'Dismiss'));
    }
    return el('div', { class: `lib-job lib-job--${job.status}`, 'data-job': job.key },
      el('div', { class: 'lib-job__head' }, el('strong', { class: 'lib-job__name' }, job.name), actions),
      steps, bar,
      el('p', { class: job.status === 'error' ? 'lib-note lib-note--error' : 'lib-note', role: job.status === 'error' ? 'alert' : null }, statusText));
  });
  s.ui.imports.replaceChildren(el('h2', { class: 'lib-section-title' }, 'Imports'), ...rows);
}

function renderPreview() {
  const pane = s.ui.preview;
  if (!s.selectedId) {
    pane.replaceChildren(dropPanel());
    return;
  }
  const r = s.replays.find((x) => x.id === s.selectedId);
  const d = s.detail;
  const usable = r ? isUsable(r) : !!(d && d.status === 'ok');
  const head = el('header', { class: 'lib-preview__head' },
    el('h2', { class: 'lib-preview__title' }, r ? (r.mapName || 'Unknown map') : 'Replay'),
    el('span', { class: 'lib-mono' }, s.selectedId),
    el('button', { type: 'button', class: 'btn btn--ghost', 'aria-label': 'Clear selection', onclick: clearSelection }, 'Clear'));
  const actions = el('div', { class: 'lib-preview__actions' },
    el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'library-open-replay', disabled: usable ? null : true, onclick: () => openReplay(s.selectedId) }, 'Open replay'),
    el('button', { type: 'button', class: 'btn', 'data-testid': 'library-analyze', disabled: usable ? null : true, onclick: () => analyzeReplay(s.selectedId) }, 'Analyze'));
  const gate = r && !usable ? el('p', { class: 'lib-note' }, gateReason(r)) : null;

  let summary;
  if (!d || d.status === 'loading') {
    summary = el('p', { class: 'lib-note', role: 'status' }, 'Loading summary…');
  } else if (d.status === 'notfound') {
    summary = el('p', { class: 'lib-note lib-note--error', role: 'alert' }, 'This replay is no longer in the archive.');
  } else if (d.status === 'error') {
    summary = el('div', null, el('p', { class: 'lib-note lib-note--error', role: 'alert' }, d.message),
      el('button', { type: 'button', class: 'btn', onclick: () => loadDetail(s.selectedId) }, 'Retry'));
  } else {
    summary = summaryBlock(d.match);
  }
  const gateNodes = gate ? [gate] : [];
  pane.replaceChildren(head, ...gateNodes, actions,
    el('section', { class: 'lib-block', 'aria-label': 'Summary' }, summary),
    el('section', { class: 'lib-block', 'aria-label': 'Top duelists' },
      el('h3', { class: 'lib-section-title' }, 'Top duelists'), duelistBlock()),
    el('section', { class: 'lib-block', 'aria-label': 'Coach verdict' },
      el('h3', { class: 'lib-section-title' }, 'Last coach verdict'), coachBlock()));
}

function dropPanel() {
  const zone = el('div', { class: 'lib-drop', 'data-testid': 'library-drop', 'aria-label': 'Import drop zone' },
    el('div', { class: 'lib-drop__art', 'aria-hidden': 'true' }, '⤓'),
    el('p', { class: 'lib-state__body' }, 'Drop .vrf files anywhere in the space, or'),
    el('button', { type: 'button', class: 'btn btn--primary', 'data-testid': 'library-choose', onclick: () => s.ui.fileInput.click() }, 'Choose .vrf files'));
  const seen = new Set();
  const recent = [
    ...[...s.jobs].filter((j) => j.status === 'done' && j.replayId).map((j) => ({ id: j.replayId, name: j.name })),
    ...s.replays.slice(0, 5).map((r) => ({ id: r.id, name: r.mapName || 'Unknown map' })),
  ].filter((x) => (seen.has(x.id) ? false : seen.add(x.id)));
  const recentBlock = recent.length
    ? el('section', { class: 'lib-block', 'aria-label': 'Recent imports' },
      el('h3', { class: 'lib-section-title' }, 'Recent imports'),
      el('ul', { class: 'lib-recent' }, recent.slice(0, 6).map((x) => el('li', null,
        el('button', { type: 'button', class: 'btn btn--ghost', onclick: () => selectReplay(x.id) }, x.name)))))
    : null;
  return el('div', { class: 'lib-drop-panel' }, zone, recentBlock);
}

function summaryBlock(match) {
  if (!match || typeof match !== 'object') return el('p', { class: 'lib-note' }, 'No summary available.');
  const rounds = Array.isArray(match.rounds) ? match.rounds : [];
  let a = 0; let b = 0;
  for (const rd of rounds) { if (rd && rd.winner === 0) a += 1; else if (rd && rd.winner === 1) b += 1; }
  const rows = [
    ['Map', match.map && match.map.name ? match.map.name : 'Unknown map'],
    ['Rounds won', rounds.length ? `${a} – ${b}` : '—'],
    ['Rounds', rounds.length ? String(rounds.length) : '—'],
    ['Duration', fmtDuration(match.durationMs)],
    ['Patch', match.patch || '—'],
  ];
  return el('dl', { class: 'lib-facts lib-facts--wide' }, rows.flatMap(([k, v]) => [el('dt', null, k), el('dd', null, v)]));
}

function duelistBlock() {
  const d = s.duelists;
  if (!d) return el('p', { class: 'lib-note' }, 'Available once the summary loads.');
  if (d.status === 'loading') return el('p', { class: 'lib-note', role: 'status' }, 'Counting kills from round events…');
  if (d.status === 'error') return el('p', { class: 'lib-note lib-note--error' }, d.message);
  if (!d.rows.length) return el('p', { class: 'lib-note' }, 'No kill events found in this replay.');
  return el('div', null,
    el('ol', { class: 'lib-duelists' }, d.rows.map((row) => el('li', null,
      el('span', null, row.label),
      el('span', { class: 'lib-mono' }, `${row.kills}K / ${row.deaths}D`)))),
    d.partial ? el('p', { class: 'lib-note' }, 'Some rounds could not be read; counts may be low.') : null);
}

function coachBlock() {
  const c = s.coach;
  if (!c || c.status === 'loading') return el('p', { class: 'lib-note', role: 'status' }, 'Loading verdict…');
  if (c.status === 'none') return el('p', { class: 'lib-note' }, 'No coach verdict yet for this replay.');
  if (c.status === 'error') return el('p', { class: 'lib-note lib-note--error' }, c.message);
  const data = c.data || {};
  const mistakes = Array.isArray(data.mistakes) ? data.mistakes.slice(0, 3) : [];
  return el('div', null,
    data.summary ? el('p', { class: 'lib-verdict' }, String(data.summary)) : el('p', { class: 'lib-note' }, 'Verdict has no summary.'),
    mistakes.length ? el('ul', { class: 'lib-mistakes' }, mistakes.map((m) => el('li', null, m && m.title ? String(m.title) : 'Mistake'))) : null);
}

// ---------------------------------------------------------------------------------------------
// Data loading (each async path captures `me` and bails if that mount has ended)
// ---------------------------------------------------------------------------------------------

async function loadLibrary({ silent = false } = {}) {
  const me = s;
  if (!me) return;
  const token = ++me.token;
  if (!silent) { me.loading = true; me.listError = null; renderArchive(); }
  let list = null; let error = null;
  try {
    const data = await me.ctx.api.get('/api/replays');
    list = (data && Array.isArray(data.replays) ? data.replays : []).filter((r) => r && typeof r.id === 'string');
  } catch (err) {
    error = errText(err);
  }
  if (!me.alive || token !== me.token) return;
  me.loading = false;
  if (list) {
    me.replays = list;
    me.listError = null;
    if (me.selectedId && !list.some((r) => r.id === me.selectedId) && !(me.detail && me.detail.status === 'ok')) {
      me.selectedId = null; me.detail = null;
    }
  } else if (!silent) {
    me.listError = error;
    me.replays = [];
  }
  await loadDemos(me, token);
  if (!me.alive || token !== me.token) return;
  renderArchive();
}

async function loadDemos(me, token) {
  try {
    const data = await me.ctx.api.get('/api/demos');
    if (me.alive && token === me.token) {
      me.demos = Array.isArray(data && data.demos) ? data.demos.filter((d) => d && typeof d.name === 'string') : [];
    }
  } catch {
    if (me.alive && token === me.token) me.demos = [];
  }
}

async function selectReplay(id) {
  if (!s) return;
  s.selectedId = id;
  renderSelection();
  if (s.detail && s.detail.id === id && s.detail.status !== 'loading') { renderPreview(); return; }
  await loadDetail(id);
}

function clearSelection() {
  if (!s) return;
  s.selectedId = null; s.detail = null; s.duelists = null; s.coach = null;
  s.detailToken += 1;
  renderSelection();
  renderPreview();
}

async function loadDetail(id) {
  const me = s;
  if (!me) return;
  const token = ++me.detailToken;
  me.detail = { id, status: 'loading' };
  me.duelists = null; me.coach = null;
  renderPreview();
  const [m, c] = await Promise.allSettled([
    me.ctx.api.get(`/api/replays/${enc(id)}`),
    me.ctx.api.get(`/api/replays/${enc(id)}/coach`),
  ]);
  if (!me.alive || token !== me.detailToken || me.selectedId !== id) return;
  if (m.status === 'fulfilled') {
    me.detail = { id, status: 'ok', match: m.value };
  } else if (m.reason instanceof ApiError && m.reason.status === 404) {
    me.detail = { id, status: 'notfound' };
  } else {
    me.detail = { id, status: 'error', message: errText(m.reason) };
  }
  if (c.status === 'fulfilled') me.coach = { status: 'ok', data: c.value };
  else if (c.reason instanceof ApiError && c.reason.status === 404) me.coach = { status: 'none' };
  else me.coach = { status: 'error', message: `Coach verdict unavailable (${errText(c.reason)})` };
  renderPreview();
  if (me.detail.status === 'ok') loadDuelists(me, id, token, me.detail.match);
}

async function loadDuelists(me, id, token, match) {
  const rounds = Array.isArray(match.rounds) ? match.rounds.slice(0, DUELIST_ROUND_CAP) : [];
  const players = new Map((Array.isArray(match.players) ? match.players : []).map((p) => [String(p.id), p]));
  if (!rounds.length) { me.duelists = { status: 'ok', rows: [] }; renderPreview(); return; }
  me.duelists = { status: 'loading' };
  renderPreview();
  const kills = new Map(); const deaths = new Map();
  let next = 0; let failed = 0; let read = 0;
  const worker = async () => {
    while (next < rounds.length) {
      const meta = rounds[next++];
      const n = meta && Number.isInteger(meta.n) ? meta.n : null;
      if (n === null) { failed += 1; continue; }
      try {
        const rd = await me.ctx.api.get(`/api/replays/${enc(id)}/rounds/${n}`);
        if (!me.alive || token !== me.detailToken) return;
        read += 1;
        for (const ev of Array.isArray(rd && rd.events) ? rd.events : []) {
          if (!ev || ev.type !== 'kill') continue;
          if (ev.actor) kills.set(String(ev.actor), (kills.get(String(ev.actor)) || 0) + 1);
          if (ev.target) deaths.set(String(ev.target), (deaths.get(String(ev.target)) || 0) + 1);
        }
      } catch { failed += 1; }
    }
  };
  await Promise.all(Array.from({ length: DUELIST_CONCURRENCY }, worker));
  if (!me.alive || token !== me.detailToken) return;
  if (!read) { me.duelists = { status: 'error', message: 'Round events could not be loaded.' }; renderPreview(); return; }
  const ids = new Set([...kills.keys(), ...deaths.keys()]);
  const rows = [...ids].map((pid) => {
    const p = players.get(pid);
    const label = p && (p.name || p.agent) ? String(p.name || p.agent) : pid;
    return { label, kills: kills.get(pid) || 0, deaths: deaths.get(pid) || 0 };
  }).sort((x, y) => y.kills - x.kills || x.deaths - y.deaths).slice(0, 3);
  me.duelists = { status: 'ok', rows, partial: failed > 0 };
  renderPreview();
}

// ---------------------------------------------------------------------------------------------
// Actions: open / analyze / demo
// ---------------------------------------------------------------------------------------------

function openReplay(id) {
  if (!s) return;
  const r = s.replays.find((x) => x.id === id);
  if (r && !isUsable(r)) { s.ctx.ui.toast(gateReason(r), 'info'); return; }
  s.ctx.navigate(`/replay/${enc(id)}`);
}

function analyzeReplay(id) {
  if (!s) return;
  const r = s.replays.find((x) => x.id === id);
  if (r && !isUsable(r)) { s.ctx.ui.toast(gateReason(r), 'info'); return; }
  s.ctx.navigate(`/analysis/${enc(id)}`);
}

async function openDemo() {
  const me = s;
  if (!me) return;
  const demo = me.demos[0];
  if (!demo) { me.ctx.ui.toast('No demo replay is installed.', 'info'); return; }
  if (typeof demo.path !== 'string' || !demo.path) {
    me.ctx.ui.toast('Demo path is not available from this client.', 'error');
    return;
  }
  try {
    const job = await me.ctx.api.post('/api/replays/import-path', { path: demo.path });
    if (!me.alive) return;
    trackJob({ key: `demo-${demo.name}-${Date.now()}`, name: demo.name, jobId: job.jobId, replayId: job.id || null, status: 'queued', stage: 'receive', percent: 0, message: 'Queued demo' });
  } catch (err) {
    if (me.alive) me.ctx.ui.toast(errText(err), 'error');
  }
}

// ---------------------------------------------------------------------------------------------
// Import
// ---------------------------------------------------------------------------------------------

function enqueueFiles(files) {
  if (!s) return;
  const me = s;
  for (const file of Array.from(files || [])) {
    if (!/\.vrf$/i.test(file.name)) {
      trackJob({ key: `bad-${file.name}-${Date.now()}`, name: file.name, status: 'error', stage: '', percent: 0, error: 'Only .vrf files can be imported.' });
      continue;
    }
    const job = {
      key: `up-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`, name: file.name, file,
      status: 'uploading', stage: 'receive', percent: 0, message: 'Uploading…', controller: new AbortController(),
    };
    trackJob(job);
    me.chain = me.chain.then(() => runUpload(me, job));
  }
}

function trackJob(job) {
  if (!s) return;
  s.jobs.unshift(job);
  if (job.jobId) s.byJobId.set(job.jobId, job);
  renderImports();
  renderPreview();
  if (job.jobId) ensureStream();
}

function uploadFile(me, job, signal) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open('PUT', `/api/replays/upload?name=${enc(job.file.name)}`);
    xhr.setRequestHeader('Accept', 'application/json');
    xhr.setRequestHeader('Content-Type', 'application/octet-stream');
    xhr.upload.addEventListener('progress', (e) => {
      if (!me.alive || !e.lengthComputable || !e.total) return;
      job.stage = 'receive';
      job.percent = Math.round((e.loaded / e.total) * 9);
      job.message = `Uploading ${job.name}…`;
      renderImports();
    });
    xhr.addEventListener('load', () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText); } catch { data = null; }
      if (xhr.status >= 200 && xhr.status < 300 && data && data.jobId) {
        resolve(data);
        return;
      }
      const err = data && data.error;
      const code = (err && typeof err === 'object' && err.code) || `http_${xhr.status}`;
      const message = (typeof err === 'string' && err) || (err && err.message) || `HTTP ${xhr.status}`;
      reject(new ApiError(xhr.status, code, message, xhr.getResponseHeader('X-Request-Id')));
    });
    xhr.addEventListener('error', () => reject(new ApiError(0, 'network', 'Backend unreachable')));
    xhr.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
    const onAbort = () => xhr.abort();
    signal.addEventListener('abort', onAbort, { once: true });
    xhr.send(job.file);
  });
}

async function runUpload(me, job) {
  if (!me.alive || job.status === 'cancelled') return;
  job.status = 'uploading';
  renderImports();
  try {
    const data = await uploadFile(me, job, job.controller.signal);
    if (!me.alive) return;
    job.jobId = data.jobId;
    job.replayId = data.id || null;
    job.status = 'queued';
    job.stage = 'receive';
    job.message = 'Queued';
    job.controller = null;
    job.file = null;
    me.byJobId.set(job.jobId, job);
    ensureStream();
    await syncJobs(me);
  } catch (err) {
    if (!me.alive) return;
    job.controller = null;
    job.file = null;
    if (err && err.name === 'AbortError') {
      job.status = 'cancelled';
      job.message = 'Cancelled';
    } else {
      job.status = 'error';
      job.error = errText(err);
    }
  }
  if (me.alive) { renderImports(); renderPreview(); }
}

function cancelJob(job) {
  if (job.controller) job.controller.abort();
}

function dismissJob(job) {
  if (!s) return;
  s.jobs = s.jobs.filter((j) => j !== job);
  if (job.jobId) s.byJobId.delete(job.jobId);
  renderImports();
  renderPreview();
}

function applyJob(update) {
  if (!s) return;
  const job = s.byJobId.get(update.jobId || update.id);
  if (!job) return;
  const status = String(update.status || '');
  job.stage = update.stage || job.stage;
  job.percent = Number.isFinite(Number(update.pct)) ? Number(update.pct) : job.percent;
  if (update.message) job.message = String(update.message);
  if (update.replayId) job.replayId = update.replayId;
  if (status === 'error') {
    job.status = 'error';
    job.error = String(update.error || update.message || 'Import failed');
  } else if (status === 'done') {
    const wasDone = job.status === 'done';
    job.status = 'done';
    job.percent = 100;
    if (!wasDone) onJobDone(job);
  } else if (job.status !== 'done') {
    job.status = 'running';
  }
  renderImports();
  renderPreview();
}

function onJobDone(job) {
  if (!s) return;
  s.ctx.ui.toast(`Imported ${job.name}`, 'success');
  if (!s.selectedId && job.replayId) selectReplay(job.replayId);
  loadLibrary({ silent: true });
}

async function syncJobs(me) {
  if (!me.alive || !me.byJobId.size) return;
  try {
    const data = await me.ctx.api.get('/api/jobs');
    if (!me.alive) return;
    for (const j of Array.isArray(data && data.jobs) ? data.jobs : []) {
      if (me.byJobId.has(j.id)) {
        applyJob({ jobId: j.id, replayId: j.replayId, stage: j.stage, pct: j.pct, message: j.message, status: j.status, error: j.error });
      }
    }
  } catch { /* the stream or the next refresh catches up */ }
}

function hasPending(me) {
  return [...me.byJobId.values()].some((j) => j.status === 'queued' || j.status === 'running');
}

function ensureStream() {
  const me = s;
  if (!me || !me.alive || me.streamCtrl) return;
  const ctrl = new AbortController();
  me.streamCtrl = ctrl;
  const retry = () => {
    if (me.streamCtrl === ctrl) me.streamCtrl = null;
    if (!me.alive || !hasPending(me)) return;
    clearTimeout(me.reconnectTimer);
    me.reconnectTimer = setTimeout(() => {
      if (me.alive) { ensureStream(); syncJobs(me); }
    }, RECONNECT_MS);
  };
  me.ctx.api.stream('/api/jobs/stream', undefined, {
    signal: ctrl.signal,
    onEvent: ({ event, data }) => {
      if (me.alive && event === 'progress' && data && typeof data === 'object') applyJob(data);
    },
  }).then(retry, (err) => { if (!(err && err.name === 'AbortError')) retry(); });
}

// ---------------------------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------------------------

function wire(me, root) {
  const ui = me.ui;
  const on = (node, type, fn) => {
    node.addEventListener(type, fn);
    me.teardown.push(() => node.removeEventListener(type, fn));
  };

  on(ui.search, 'input', () => { me.query = ui.search.value; renderArchive(); });
  on(ui.search, 'keydown', (e) => {
    if (e.key === 'Enter') {
      const list = visibleReplays();
      if (list.length) { e.preventDefault(); openReplay(list[0].id); }
    } else if (e.key === 'ArrowDown') {
      const target = (me.selectedId && me.cards.get(me.selectedId)) || ui.grid.querySelector('.lib-card');
      if (target) { e.preventDefault(); focusCard(target); }
    } else if (e.key === 'Escape' && ui.search.value) {
      ui.search.value = ''; me.query = ''; renderArchive();
    }
  });
  on(ui.sort, 'change', () => { me.sort = ui.sort.value; me.ctx.state.set('sort', me.sort); renderArchive(); });
  on(ui.viewToggle, 'click', (e) => {
    const btn = e.target.closest && e.target.closest('[data-view]');
    if (!btn) return;
    me.view = btn.dataset.view === 'list' ? 'list' : 'grid';
    me.ctx.state.set('view', me.view);
    renderToolbar();
  });
  on(ui.importBtn, 'click', () => ui.fileInput.click());
  on(ui.refreshBtn, 'click', () => loadLibrary());
  on(ui.fileInput, 'change', () => {
    enqueueFiles(ui.fileInput.files);
    ui.fileInput.value = '';
  });

  // Drag and drop anywhere in the space (listeners only on the space root, never the shell).
  let depth = 0;
  const hasFiles = (e) => !!(e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files'));
  on(root, 'dragenter', (e) => { if (!hasFiles(e)) return; e.preventDefault(); depth += 1; ui.veil.hidden = false; });
  on(root, 'dragover', (e) => { if (!hasFiles(e)) return; e.preventDefault(); e.dataTransfer.dropEffect = 'copy'; });
  on(root, 'dragleave', (e) => { if (!hasFiles(e)) return; depth = Math.max(0, depth - 1); if (!depth) ui.veil.hidden = true; });
  on(root, 'drop', (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    depth = 0; ui.veil.hidden = true;
    enqueueFiles(e.dataTransfer.files);
  });
}

function focusCard(node) {
  if (!node || !s) return;
  node.focus();
  const id = node.dataset.id;
  if (id && id !== s.selectedId) selectReplay(id);
}

function columnCount() {
  if (!s || s.view === 'list') return 1;
  const cards = [...s.ui.grid.querySelectorAll('.lib-card')];
  if (!cards.length) return 1;
  const top = cards[0].offsetTop;
  let n = 0;
  for (const c of cards) { if (c.offsetTop === top) n += 1; else break; }
  return Math.max(1, n);
}

// ---------------------------------------------------------------------------------------------
// Module export (SHELL-CONTRACT space shape)
// ---------------------------------------------------------------------------------------------

export default {
  id: 'library',
  title: 'Library',
  accent: 'library',

  async mount(root, ctx) {
    if (s) this.unmount();
    const me = {
      alive: true, token: 0, detailToken: 0, ctx, root,
      replays: [], demos: [], jobs: [], byJobId: new Map(), chain: Promise.resolve(),
      loading: true, listError: null, query: '', sort: 'recent', view: 'grid',
      filters: { map: new Set(), result: new Set(), agent: new Set(), patch: new Set() },
      selectedId: null, detail: null, duelists: null, coach: null,
      cards: new Map(), streamCtrl: null, reconnectTimer: null, teardown: [],
    };
    const savedSort = ctx.state.get('sort', 'recent');
    const savedView = ctx.state.get('view', 'grid');
    if (SORTS.some(([v]) => v === savedSort)) me.sort = savedSort;
    if (savedView === 'list' || savedView === 'grid') me.view = savedView;
    s = me;
    me.ui = buildShell(root);
    me.ui.sort.value = me.sort;
    wire(me, root);
    ctx.shortcuts.register({
      '/': () => { if (!s) return false; s.ui.search.focus(); s.ui.search.select(); return true; },
      'Ctrl+O': () => { if (!s) return false; s.ui.fileInput.click(); return true; },
    });
    renderArchive();
    renderImports();
    renderPreview();
    await loadLibrary();
    if (me.alive) {
      ensureStream();
      await syncJobs(me);
    }
  },

  unmount() {
    if (!s) return;
    const cur = s;
    s = null;
    cur.alive = false;
    cur.token += 1;
    cur.detailToken += 1;
    for (const release of cur.teardown) { try { release(); } catch { /* already detached */ } }
    cur.teardown = [];
    clearTimeout(cur.reconnectTimer);
    if (cur.streamCtrl) cur.streamCtrl.abort();
    for (const job of cur.jobs) if (job.controller) job.controller.abort();
  },

  onKey(e) {
    if (!s || !s.alive) return false;
    const active = document.activeElement;
    const onCard = !!(active && active.classList && active.classList.contains('lib-card'));
    const inArchive = s.ui.grid.contains(active) || active === s.root;
    if (!inArchive) return false;
    if (e.key === 'Enter' && onCard) {
      const id = active.dataset.id;
      if (!id) return false;
      openReplay(id);
      return true;
    }
    const cols = columnCount();
    const moves = { ArrowRight: 1, ArrowLeft: -1, ArrowDown: cols, ArrowUp: -cols };
    if (!(e.key in moves)) return false;
    const ids = visibleReplays().map((r) => r.id);
    if (!ids.length) return false;
    const idx = ids.indexOf(s.selectedId);
    const next = Math.max(0, Math.min(ids.length - 1, idx < 0 ? 0 : idx + moves[e.key]));
    focusCard(s.cards.get(ids[next]));
    return true;
  },
};
