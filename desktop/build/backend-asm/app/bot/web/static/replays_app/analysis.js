(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const matchPath = location.pathname.match(/\/replays\/([^/]+)\/analysis\/?$/);
  const query = new URLSearchParams(location.search);
  const id = query.get('id') || (matchPath && decodeURIComponent(matchPath[1])) || (query.get('demo') === '1' ? 'demo' : null);
  const direct = location.pathname.startsWith('/bot/web/static/replays_app/');
  const base = `/api/replays/${encodeURIComponent(id || '')}`;
  const mapCanvas = $('analysis-map'), heatCanvas = $('analysis-heat');
  const bg = mapCanvas.getContext('2d'), ctx = heatCanvas.getContext('2d');
  const mapImage = new Image();
  let match, analysis, fit = {x: 0, y: 0, side: 1};
  const safeUUID = value => typeof value === 'string' && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(value) ? value : '';
  const mediaUrl = value => typeof value === 'string' && /^https:\/\/media\.valorant-api\.com\/[\w/.-]+$/i.test(value) ? value : '';
  const node = (tag, className, text) => { const el = document.createElement(tag); el.className = className; el.textContent = text; return el; };
  const label = value => value == null || value === '' ? 'Unknown' : String(value);
  async function json(url, options) {
    const response = await fetch(url, options);
    let body; try { body = await response.json(); } catch { body = {}; }
    if (!response.ok) { const error = new Error(body.error || `HTTP ${response.status}`); error.status = response.status; throw error; }
    return body;
  }
  function draw() {
    if (!match || !analysis) return;
    const bounds = $('analysis-stage').getBoundingClientRect();
    const w = Math.max(1, bounds.width), h = Math.max(1, bounds.height);
    const ratio = Math.min(devicePixelRatio || 1, 2);
    for (const canvas of [mapCanvas, heatCanvas]) {
      canvas.width = Math.round(w * ratio); canvas.height = Math.round(h * ratio);
      canvas.style.width = `${w}px`; canvas.style.height = `${h}px`;
    }
    bg.setTransform(ratio, 0, 0, ratio, 0, 0); ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    fit.side = Math.max(1, Math.min(w - 32, h - 32));
    fit.x = (w - fit.side) / 2; fit.y = (h - fit.side) / 2;
    bg.fillStyle = '#101820'; bg.fillRect(0, 0, w, h);
    bg.fillStyle = '#1c2b34'; bg.fillRect(fit.x, fit.y, fit.side, fit.side);
    if (mapImage.complete && mapImage.naturalWidth) {
      bg.globalAlpha = .82; bg.drawImage(mapImage, fit.x, fit.y, fit.side, fit.side); bg.globalAlpha = 1;
    }
    bg.strokeStyle = '#56646b'; bg.strokeRect(fit.x, fit.y, fit.side, fit.side);
    const kind = $('heat-kind').value, selected = $('heat-round').value;
    const counts = new Map();
    for (const layer of analysis.heat || []) {
      if (layer.kind !== kind || (selected !== 'all' && String(layer.round) !== selected)) continue;
      for (const bin of layer.bins || []) {
        if (!Array.isArray(bin) || bin.length !== 3) continue;
        const [x, y, count] = bin;
        const key = `${x},${y}`;
        counts.set(key, (counts.get(key) || 0) + count);
      }
    }
    const maximum = Math.max(1, ...counts.values());
    for (const [key, count] of counts) {
      const [x, y] = key.split(',').map(Number);
      const px = fit.x + x / 1024 * fit.side, py = fit.y + y / 1024 * fit.side;
      const size = 32 / 1024 * fit.side;
      const intensity = Math.sqrt(count / maximum);
      ctx.fillStyle = `rgba(${kind === 'deaths' ? '255,70,85' : kind === 'util' ? '212,142,255' : '255,194,89'},${(.18 + .66 * intensity).toFixed(3)})`;
      ctx.fillRect(px, py, size, size);
    }
    renderLegend(kind, counts, maximum);
    $('heat-count').textContent = `${counts.size} cells`;
    $('map-warning').hidden = counts.size !== 0;
    $('map-warning').textContent = `No ${kind} coordinates for this selection.`;
  }
  const LAYER_NAMES = {positions: 'Player positions', deaths: 'Deaths', util: 'Utility / ultimates'};
  const LAYER_RGB = {positions: '255,194,89', deaths: '255,70,85', util: '212,142,255'};
  // Legend only reads the already-computed cell counts; same intensity curve as draw().
  function renderLegend(kind, counts, maximum) {
    $('legend-layer').textContent = LAYER_NAMES[kind] || kind;
    $('legend-cell').textContent = '32 × 32 minimap px';
    const key = $('legend-key'); key.replaceChildren();
    if (!counts.size) { $('legend-peak').textContent = 'none'; key.append(node('li', 'legend-empty', 'No cells for this selection')); return; }
    $('legend-peak').textContent = `${maximum} sample${maximum === 1 ? '' : 's'}`;
    const bands = 5, tally = new Array(bands).fill(0), low = new Array(bands).fill(Infinity), high = new Array(bands).fill(0);
    for (const count of counts.values()) {
      const band = Math.min(bands - 1, Math.floor(Math.sqrt(count / maximum) * bands));
      tally[band] += 1; low[band] = Math.min(low[band], count); high[band] = Math.max(high[band], count);
    }
    for (let band = 0; band < bands; band += 1) {
      const item = node('li', 'legend-band', '');
      const swatch = node('span', 'legend-swatch', '');
      const rgb = LAYER_RGB[kind] || LAYER_RGB.positions, a0 = (.18 + .66 * band / bands).toFixed(3), a1 = (.18 + .66 * (band + 1) / bands).toFixed(3);
      swatch.style.background = `linear-gradient(90deg, rgba(${rgb},${a0}), rgba(${rgb},${a1}))`;
      const range = tally[band] ? (low[band] === high[band] ? `${low[band]}` : `${low[band]}–${high[band]}`) : '–';
      item.append(swatch, node('span', 'legend-range', range), node('span', 'legend-n', `${tally[band]} cell${tally[band] === 1 ? '' : 's'}`));
      item.title = `${tally[band]} cells with ${range} samples`;
      key.append(item);
    }
  }
  function renderFindings() {
    const panel = $('findings'); panel.replaceChildren();
    $('count-findings').textContent = analysis.findings?.length ? String(analysis.findings.length) : '';
    if (!analysis.findings?.length) { panel.textContent = 'No key moments recorded for this replay.'; panel.classList.add('is-empty'); return; }
    for (const finding of analysis.findings) {
      const anchor = node('a', `finding ${finding.severity || 'info'}`, '');
      anchor.replaceChildren(node('strong', '', `R${finding.round} · ${finding.title}`), node('span', 'muted', finding.detail));
      const t = Number.isFinite(finding.tMs) ? finding.tMs / 1000 : 0;
      anchor.href = direct ? `${window.replayAssetBase}view.html?id=${encodeURIComponent(id)}#r=${finding.round}&t=${t}` :
        `/replays/${encodeURIComponent(id)}/view#r=${finding.round}&t=${t}`;
      panel.append(anchor);
    }
  }
  function renderUtilityUsage() {
    const section = $('utility-usage-section'), panel = $('utility-usage');
    if (!match.utilityCounts) { panel.replaceChildren(node('p', 'unavailable', 'Utility usage unavailable: this replay recorded no ability data.')); return; }
    panel.replaceChildren();
    for (const player of match.players || []) {
      const counts = match.utilityCounts[player.id] || {};
      const abilities = Object.entries(counts.abilities || {}).sort((a, b) => b[1] - a[1]);
      const full = abilities.map(([kind, n]) => `${kind} ×${n}`).join(', ') || 'No utility recorded';
      const row = node('div', 'util-row', ''); row.title = full;
      row.append(node('span', 'util-name', label(player.name || player.id)),
        node('span', 'util-agent', label(player.agent)),
        node('span', 'util-total', String(counts.total || 0)),
        node('span', 'util-break', abilities.slice(0, 4).map(([kind, n]) => `${kind} ×${n}`).join(' · ') || 'No utility recorded'));
      panel.append(row);
    }
  }
  function renderRounds() {
    const table = $('analysis-rounds'); table.replaceChildren();
    for (const meta of match.rounds || []) {
      const row = document.createElement('tr');
      const attacker = meta.attackerTeam;
      const sides = attacker === 0 || attacker === 1 ? `Team ${attacker === 0 ? 'A' : 'B'} ATK / Team ${attacker === 0 ? 'B' : 'A'} DEF` : 'Unknown';
      const winner = meta.winner === 0 ? 'Team A' : meta.winner === 1 ? 'Team B' : 'Unknown';
      for (const value of [meta.n, sides, winner, meta.endType || 'Unknown', meta.confidence || 'unknown'])
        row.append(node('td', '', String(value)));
      table.append(row);
    }
  }
  function renderReview(review) {
    const panel = $('review-result'); panel.replaceChildren();
    panel.append(node('p', '', review.summary || 'Review generated.'));
    for (const finding of review.findings || []) {
      const item = node('div', 'finding', '');
      item.replaceChildren(node('strong', '', `${finding.round ? `R${finding.round} · ` : ''}${finding.title || 'Finding'}`),
        node('span', 'muted', finding.detail || ''));
      panel.append(item);
    }
    $('review-status').textContent = `${review.backend || 'Local model'} · ${review.model || 'default model'}`;
  }
  async function loadReview() {
    try { renderReview(await json(`${base}/review`)); }
    catch (error) { $('review-status').textContent = error.status === 404 ? 'No saved review. Optional: generate one with a local model.' : `Review unavailable: ${error.message}`; }
  }
  async function generateReview() {
    const btn = $('generate-review'); btn.disabled = true;
    $('review-status').textContent = 'Waiting for local model…';
    try { renderReview(await json(`${base}/review`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({backend: 'auto'})})); }
    catch (error) { $('review-status').textContent = error.status === 503 ? 'No local LLM reachable. Start LM Studio or Ollama and try again.' : `Review unavailable: ${error.message}`; }
    finally { btn.disabled = false; }
  }
  async function loadIdentity() {
    try {
      const identity = await json(`${base}/identity`);
      $('identity-status').textContent = identity.available ? `Updated ${label(identity.refreshedAt)}` :
        `Identity unavailable: ${identity.reason || 'No verified match found'}. Replay slots remain visible.`;
      const list = $('identity-players'); list.replaceChildren();
      for (const player of identity.players || []) {
        const display = player.riotId || player.displayName || player.slot;
        list.append(node('div', 'roster-row', String(display || player.slot)));
      }
      if (!list.children.length) list.append(node('p', 'unavailable', 'No player names to show. Replay slots only.'));
    } catch (error) { $('identity-status').textContent = `Identity unavailable (${error.message}).`; }
  }
  function showFailure(error) {
    const reason = error.status === 404 ? 'not found' : error.status >= 500 ? `server error ${error.status}` : error.status ? error.message : 'offline';
    const advice = error.status === 404 ? 'This replay has no saved analysis. Re-import it from the library.'
      : error.status >= 500 ? 'The replay could not be decoded. Re-import it, or check the app log.'
      : 'Cannot reach the ValoHub server. Check that the app is running, then reload.';
    $('match-title').textContent = `Analysis unavailable · ${reason}`;
    $('match-subtitle').textContent = id ? `REPLAY ${id}` : 'TACTICAL ANALYSIS';
    $('heat-count').textContent = '—';
    for (const key of ['legend-layer', 'legend-peak', 'legend-cell']) $(key).textContent = '—';
    $('legend-key').replaceChildren(node('li', 'legend-empty', 'No data'));
    $('map-warning').hidden = false; $('map-warning').textContent = 'No heatmap: analysis unavailable.';
    const panel = $('findings'); panel.replaceChildren(); panel.classList.add('is-empty'); panel.textContent = advice;
  }
  async function init() {
    if (!id) { showFailure(new Error('missing replay ID')); $('findings').textContent = 'Missing replay ID. Open analysis from your library.'; $('match-title').textContent = 'Analysis unavailable · no replay selected'; return; }
    try {
      match = await json(direct && id === 'demo' ? '/data/replays/demo/match.json' : base);
      analysis = await json(`${base}/analysis`);
      $('match-title').textContent = `${label(match.map?.name)} · ${analysis.rounds} rounds`;
      $('match-subtitle').textContent = `REPLAY ${id}`;
      $('viewer-link').href = direct ? `${window.replayAssetBase}view.html?id=${encodeURIComponent(id)}` : `/replays/${encodeURIComponent(id)}/view`;
      for (const meta of match.rounds || []) {
        const option = node('option', '', `Round ${meta.n}`); option.value = String(meta.n); $('heat-round').append(option);
      }
      renderFindings(); renderRounds(); renderUtilityUsage(); draw();
      const local = safeUUID(match.map?.uuid);
      const remote = mediaUrl(match.map?.image);
      if (local) {
        mapImage.onerror = () => { mapImage.onerror = draw; if (remote) mapImage.src = remote; else draw(); };
        mapImage.src = `${window.replayAssetBase}assets/maps/${local}.png`;
      } else if (remote) { mapImage.onerror = draw; mapImage.src = remote; }
      mapImage.onload = draw;
    } catch (error) { showFailure(error); }
    loadReview(); loadIdentity();
  }
  const tabs = [...document.querySelectorAll('.inspector-tabs [role=tab]')];
  function selectTab(tab, focus) {
    for (const other of tabs) {
      const on = other === tab;
      other.setAttribute('aria-selected', String(on)); other.tabIndex = on ? 0 : -1;
      $(`pane-${other.dataset.pane}`).hidden = !on;
    }
    if (focus) tab.focus();
  }
  for (const tab of tabs) {
    tab.addEventListener('click', () => selectTab(tab, false));
    tab.addEventListener('keydown', event => {
      const at = tabs.indexOf(tab);
      const next = event.key === 'ArrowRight' ? tabs[(at + 1) % tabs.length] : event.key === 'ArrowLeft' ? tabs[(at + tabs.length - 1) % tabs.length] :
        event.key === 'Home' ? tabs[0] : event.key === 'End' ? tabs[tabs.length - 1] : null;
      if (next) { event.preventDefault(); selectTab(next, true); }
    });
  }
  $('heat-kind').addEventListener('change', draw);
  $('heat-round').addEventListener('change', draw);
  $('generate-review').addEventListener('click', generateReview);
  addEventListener('resize', draw);
  init();
})();
