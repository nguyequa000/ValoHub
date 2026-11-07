(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const pathMatch = location.pathname.match(/\/replays\/([^/]+)\/view\/?$/);
  const demo = params.get('demo') === '1' || params.get('id') === 'demo';
  const id = demo ? 'demo' : params.get('id') || (pathMatch && decodeURIComponent(pathMatch[1]));
  // Ship the synthetic fixture with the UI so ?demo=1 works on a fresh clone.
  const demoRoot = `${window.replayAssetBase}demo/`;
  const root = demo ? demoRoot : `/api/replays/${encodeURIComponent(id || '')}`;
  const mapCanvas = $('map-canvas'), actorsCanvas = $('actors-canvas');
  const bg = mapCanvas.getContext('2d'), ctx = actorsCanvas.getContext('2d');
  const mapImage = new Image();
  let match, round, roundIndex = 0, elapsed = 0, playing = false, lastFrame = 0;
  let remoteGeneration = 0;
  let mapFit = {x: 0, y: 0, side: 1}, projection = true, generation = 0;
  let visibleMarkers = [], dpr = 1;
  const cache = new Map(), loading = new Map();
  let assetIndex = {};
  const abilityIcons = new Map();
  const colors = ['#65c9e8', '#ff6874'];
  // Team A/B are stable team IDs, not attacking/defending sides (which swap at halftime).
  const teamLabel = team => team === 0 ? 'Team A' : team === 1 ? 'Team B' : '';
  const sideLabel = (meta, team) => teamLabel(team) + (meta?.attackerTeam === team ? ' · ATK' : meta?.attackerTeam === 1 - team ? ' · DEF' : '');
  const outcome = meta => `${teamLabel(meta.winner) || 'Unknown winner'} · ${display(meta.endType, 'Unknown end')} · ${display(meta.confidence, 'unknown')} confidence`;
  const abilityPalette = ['#6de5c3', '#f4c46e', '#67bfff', '#f389ac', '#b59bff'];
  const validMedia = url => typeof url === 'string' && /^https:\/\/media\.valorant-api\.com\/[\w/.-]+$/i.test(url) ? url : '';
  const display = (value, fallback = 'Unknown') => value === null || value === undefined || value === '' ? fallback : String(value);
  const time = ms => { const s = Math.max(0, ms / 1000); return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}.${Math.floor(s * 10) % 10}`; };
  const duration = () => round ? Math.max(0, round.endMs - round.startMs) : 0;
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const clamp = (v, min, max) => Math.min(max, Math.max(min, v));
  const matchUrl = demo ? `${root}match.json` : root;
  const roundUrl = n => demo ? `${root}rounds/r${String(n).padStart(2, '0')}.json` : `${root}/rounds/${n}`;


  async function getJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`${url}: HTTP ${response.status}`);
    return response.json();
  }
  function fail(message) { $('loading').hidden = false; $('loading').textContent = message; playing = false; $('play').textContent = '▶'; }
  function updateHash() {
    if (!round) return;
    const hash = `r=${round.n}&t=${(elapsed / 1000).toFixed(1)}`;
    if (location.hash.slice(1) !== hash) history.replaceState(null, '', `${location.pathname}${location.search}#${hash}`);
  }
  function hashMoment() {
    const p = new URLSearchParams(location.hash.slice(1));
    return { n: Number(p.get('r')), t: Number(p.get('t')) };
  }
  function resize() {
    const bounds = $('map-stage').getBoundingClientRect();
    const scale = Math.min(devicePixelRatio || 1, 2);
    dpr = scale; sprites.clear(); // sprites are baked at device resolution: rebake on any scale change
    for (const canvas of [mapCanvas, actorsCanvas]) {
      canvas.width = Math.max(1, Math.round(bounds.width * scale));
      canvas.height = Math.max(1, Math.round(bounds.height * scale));
      canvas.style.width = `${bounds.width}px`; canvas.style.height = `${bounds.height}px`;
    }
    bg.setTransform(scale, 0, 0, scale, 0, 0); ctx.setTransform(scale, 0, 0, scale, 0, 0);
    mapFit = {side: Math.max(1, Math.min(bounds.width - 32, bounds.height - 32)), x: 0, y: 0};
    mapFit.x = (bounds.width - mapFit.side) / 2; mapFit.y = (bounds.height - mapFit.side) / 2;
    drawMap(); drawActors();
  }
  function drawMap() {
    if (!match) return;
    const w = mapCanvas.clientWidth, h = mapCanvas.clientHeight;
    bg.fillStyle = '#101820'; bg.fillRect(0, 0, w, h);
    if (mapImage.complete && mapImage.naturalWidth) {
      bg.globalAlpha = .8; bg.drawImage(mapImage, mapFit.x, mapFit.y, mapFit.side, mapFit.side); bg.globalAlpha = 1;
    } else { bg.fillStyle = '#1c2b34'; bg.fillRect(mapFit.x, mapFit.y, mapFit.side, mapFit.side); }
    bg.strokeStyle = '#56646b'; bg.lineWidth = 1; bg.strokeRect(mapFit.x, mapFit.y, mapFit.side, mapFit.side);
  }
  function project(x, y) {
    if (!Number.isFinite(x) || !Number.isFinite(y)) return null;
    // Contract §2: the world axes are swapped; normalized image coordinates scale to the fitted map.
    if (!projection) return null;
    return { x: mapFit.x + (match.map.xScalarToAdd + y * match.map.xMultiplier) * mapFit.side,
      y: mapFit.y + (match.map.yScalarToAdd + x * match.map.yMultiplier) * mapFit.side };
  }
  function sample(track, t) {
    if (!track || !Array.isArray(track.x) || !Array.isArray(track.y)) return null;
    const ms = Number(round.tickMs);
    if (!Number.isFinite(ms) || ms <= 0) return null;
    const index = clamp(t / ms, 0, Math.max(0, Number(round.frames) - 1));
    const lo = Math.floor(index), hi = Math.ceil(index), alpha = index - lo;
    if (!Number.isFinite(track.x[lo]) || !Number.isFinite(track.y[lo])) return null;
    // Missing samples are not interpolated across a gap. Show the last known point for <=0.6s only.
    let next = hi;
    if (!Number.isFinite(track.x[hi]) || !Number.isFinite(track.y[hi])) next = lo;
    if (next === lo && t - lo * ms > 600) return null;
    const alive = track.alive?.[lo];
    if (alive === 0 || track.alive?.[next] === 0) return null;
    const x = track.x[lo] + (track.x[next] - track.x[lo]) * (next === lo ? 0 : alpha);
    const y = track.y[lo] + (track.y[next] - track.y[lo]) * (next === lo ? 0 : alpha);
    const yaw0 = track.yaw?.[lo], yaw1 = track.yaw?.[next];
    let yaw = Number.isFinite(yaw0) ? yaw0 : null;
    if (yaw !== null && Number.isFinite(yaw1)) {
      const diff = ((yaw1 - yaw0 + 540) % 360) - 180;
      yaw += diff * (next === lo ? 0 : alpha);
    }
    const health = track.hp?.[lo];
    return {x, y, yaw, hp: Number.isFinite(health) ? clamp(health, 0, 100) : null};
  }
  function teamColor(player) { return player?.team === 0 ? colors[0] : player?.team === 1 ? colors[1] : '#9aa5ab'; } // unknown team stays neutral
  const iconFor = (agent, ability) => {
    const path = assetIndex[agent]?.abilities?.[ability];
    return typeof path === 'string' && /^abilities\/[a-z0-9-]+\.png$/i.test(path) ? `${window.replayAssetBase}assets/${path}` : null;
  };
  const utilityLabel = item => {
    const parts = [display(item.agent, 'Unknown'), display(item.ability, item.kind || 'Utility')];
    const bounces = Array.isArray(item.bounces) ? item.bounces.length : 0;
    if (bounces > 0) parts.push(`${bounces} bounce${bounces === 1 ? '' : 's'}`);
    const landMs = item.land && Number.isFinite(item.land.t) ? item.land.t - item.t : NaN;
    if (Number.isFinite(landMs)) parts.push(`landed ${(landMs / 1000).toFixed(1)} s after throw`);
    parts.push(item.confidence === 'unowned' || item.actor == null ? 'unowned' : 'recorded');
    return parts.join(' · ');
  };
  function drawAbilities(t) {
    if (!$('abilities').checked) return;
    for (const event of round.events || []) {
      if (!['ability', 'ult'].includes(event.type) || !Number.isFinite(event.t)) continue;
      const lifetime = event.type === 'ult' ? 5500 : 3500;
      const age = t - event.t;
      if (age < 0 || age > lifetime) continue;
      const track = round.players?.[event.actor];
      const point = Number.isFinite(event.x) && Number.isFinite(event.y) ? {x: event.x, y: event.y} :
        track && sample({...track, alive: undefined}, event.t);
      const p = point && project(point.x, point.y);
      if (!p) continue; // never guess a utility location
      const label = typeof event.ability === 'string' ? event.ability : '';
      const bucket = [...label].reduce((sum, char) => sum + char.charCodeAt(0), 0) % abilityPalette.length;
      const color = event.type === 'ult' ? '#d48eff' : abilityPalette[bucket];
      ctx.globalAlpha = .3 + .7 * (1 - age / lifetime);
      ctx.strokeStyle = color; ctx.fillStyle = '#19252e'; ctx.lineWidth = 2;
      const radius = event.type === 'ult' ? 13 : 10;
      ctx.beginPath(); ctx.arc(p.x, p.y, radius, 0, Math.PI * 2); ctx.fill(); ctx.stroke();
      const player = match.players?.find(item => item.id === event.actor);
      const iconPath = player?.agent && event.ability && assetIndex[player.agent]?.abilities?.[event.ability];
      if (typeof iconPath === 'string' && /^abilities\/[a-z0-9-]+\.png$/i.test(iconPath)) {
        if (!abilityIcons.has(iconPath)) {
          const image = new Image();
          image.onload = drawActors; image.onerror = () => { abilityIcons.set(iconPath, null); drawActors(); };
          abilityIcons.set(iconPath, image);
          image.src = `${window.replayAssetBase}assets/${iconPath}`;
        }
        const image = abilityIcons.get(iconPath);
        if (image?.complete && image.naturalWidth) ctx.drawImage(image, p.x - 8, p.y - 8, 16, 16);
      } else {
        ctx.fillStyle = color; ctx.font = 'bold 13px sans-serif'; ctx.textAlign = 'center';
        ctx.fillText(event.type === 'ult' ? '✦' : '●', p.x, p.y + 5);
      }
      ctx.globalAlpha = 1;
    }
  }
  // ---- P8-C: circular agent-portrait markers -----------------------------------------------
  // Geometry (CSS px): ring outer edge r=13 (26 px marker), dark keyline +1.5 outside it, portrait r=10,
  // 3 px team ring between r=10 and r=13, 1 px light hairline on the portrait edge. Sprite box 44 px (Team B reach 18.5 + 3.5 margin). Heading chevron (r+7..r+15 = 20..28) is drawn separately, outside the largest ring.
  const MARKER_R = 13, PORTRAIT_R = 10, SPRITE_BOX = 44, NEUTRAL = '#9aa5ab', INK = '#09121b';
  const ringReach = team => team === 1 ? MARKER_R + 5.5 : MARKER_R + 1.5, NUDGE_CAP = 16, BADGE_DIST = MARKER_R * 2; // footprint radius incl. keyline
  const ART_RE = /^agents\/(?:map\/)?[a-z0-9-]+\.png$/i;
  const markerMode = params.get('markers'), skipMarkers = markerMode === 'off', skipLabels = markerMode === 'nolabels', nudgeOff = markerMode === 'nudge-off'; // QA perf A/B flags
  const sprites = new Map(), portraitCache = new Map();
  let labelLog = {placed: [], omitted: [], badges: [], states: []}, rotateMarkers = false, artReady = false, deaths = new Map(), debugMarkers = [], markerMsLog = [];
  let drawQueued = false, ariaKey = '', ariaForce = false;
  const scheduleDraw = () => { if (drawQueued) return; drawQueued = true; requestAnimationFrame(() => { drawQueued = false; drawActors(); }); };
  const teamTint = team => team === 0 ? colors[0] : team === 1 ? colors[1] : NEUTRAL;
  const ringKind = team => team === 0 ? 'solid' : team === 1 ? 'double' : 'neutral';
  const initialsOf = player => {
    const letters = String(player?.agent || player?.name || player?.id || '?').replace(/[^A-Za-z0-9]/g, '').toUpperCase();
    return letters.slice(0, 2) || '?';
  };
  // assetIndex v2 carries a `_meta` key next to the agents; only plain objects under non-underscore names are agents.
  const agentEntry = name => typeof name === 'string' && !name.startsWith('_') && Object.hasOwn(assetIndex, name) &&
    assetIndex[name] && typeof assetIndex[name] === 'object' ? assetIndex[name] : null;
  const artCandidates = name => {
    const entry = agentEntry(name);
    if (!entry) return [];
    return [entry.map, entry.portrait].filter(path => typeof path === 'string' && ART_RE.test(path)).map(path => `${window.replayAssetBase}assets/${path}`);
  };
  function loadPortrait(rec) {
    if (rec.i >= rec.urls.length) { rec.state = 'error'; rec.img = null; rec.settle(); scheduleDraw(); return; }
    const image = new Image(); rec.img = image; rec.state = 'loading';
    image.onload = () => { rec.state = 'ok'; rec.settle(); scheduleDraw(); };
    image.onerror = () => { rec.i += 1; loadPortrait(rec); }; // map/ 64px art missing -> try the full portrait
    image.src = rec.urls[rec.i];
  }
  function portraitFor(name) {
    const urls = artCandidates(name);
    if (!urls.length) return null;
    const key = urls.join('|');
    let rec = portraitCache.get(key);
    if (!rec) {
      rec = {key, urls, i: 0, img: null, state: 'loading', settle: null};
      rec.settled = new Promise(resolve => { rec.settle = resolve; });
      portraitCache.set(key, rec); loadPortrait(rec);
    }
    return rec;
  }
  function drawCross(g, color, alpha) {
    g.save(); g.globalAlpha = alpha; g.lineCap = 'round';
    g.strokeStyle = INK; g.lineWidth = 5.5; g.beginPath(); g.moveTo(-6, -6); g.lineTo(6, 6); g.moveTo(6, -6); g.lineTo(-6, 6); g.stroke();
    g.strokeStyle = color; g.lineWidth = 3; g.beginPath(); g.moveTo(-6, -6); g.lineTo(6, 6); g.moveTo(6, -6); g.lineTo(-6, 6); g.stroke();
    g.restore();
  }
  // One offscreen canvas per (art, team, state, scale): disc + clipped portrait + ring + keylines (+ dead overlay).
  function bakeSprite(rec, initials, team, kind) {
    const size = Math.ceil(SPRITE_BOX * dpr), k = size / SPRITE_BOX;
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = size;
    const g = canvas.getContext('2d', {willReadFrequently: true}); g.imageSmoothingEnabled = true; g.imageSmoothingQuality = 'high';
    g.scale(k, k); g.translate(SPRITE_BOX / 2, SPRITE_BOX / 2);
    const tint = teamTint(team), art = rec?.state === 'ok' ? rec.img : null;
    if (kind === 'x') { drawCross(g, tint, .6); return finishSprite({canvas, fallback: null}); }
    const body = kind === 'dead' ? .55 : 1;
    g.globalAlpha = body;
    if (art) {
      g.globalAlpha = body * .35; g.fillStyle = tint; g.beginPath(); g.arc(0, 0, PORTRAIT_R, 0, Math.PI * 2); g.fill(); // tint the disc, never the portrait
      g.globalAlpha = body; g.save(); g.beginPath(); g.arc(0, 0, PORTRAIT_R, 0, Math.PI * 2); g.clip();
      g.drawImage(art, -PORTRAIT_R, -PORTRAIT_R, PORTRAIT_R * 2, PORTRAIT_R * 2); g.restore();
    } else { // missing art is a real state: same footprint, team fill + initials, never a dark disc
      g.fillStyle = tint; g.beginPath(); g.arc(0, 0, PORTRAIT_R, 0, Math.PI * 2); g.fill();
      g.font = 'bold 12px sans-serif'; g.textAlign = 'center'; g.textBaseline = 'middle'; g.lineJoin = 'round';
      g.strokeStyle = INK; g.lineWidth = 2.3; g.strokeText(initials, 0, .5); // 1 px dark outline outside the glyph
      g.fillStyle = '#fff'; g.fillText(initials, 0, .5);
    }
    g.globalAlpha = 1;
    g.strokeStyle = 'rgba(255,255,255,.85)'; g.lineWidth = 1; g.beginPath(); g.arc(0, 0, PORTRAIT_R - .5, 0, Math.PI * 2); g.stroke(); // light inner hairline
    const mid = (PORTRAIT_R + MARKER_R) / 2;
    if (kind === 'dead') g.globalAlpha = .6; // rings at 60 % so a dead marker never reads as a full-strength team marker
    g.strokeStyle = tint; g.lineWidth = 3; g.beginPath(); g.arc(0, 0, mid, 0, Math.PI * 2); g.stroke(); // solid 3 px band (both teams)
    if (team === 1) { // DEF: same solid band + outer 2 px band at r+2..r+4 after a 2 px transparent gap, full opacity
      g.lineWidth = 2; g.beginPath(); g.arc(0, 0, MARKER_R + 3, 0, Math.PI * 2); g.stroke();
      g.strokeStyle = INK; g.lineWidth = 1.5; g.beginPath(); g.arc(0, 0, MARKER_R + 4.75, 0, Math.PI * 2); g.stroke(); // keyline outside band 2
    } else { g.strokeStyle = INK; g.lineWidth = 1.5; g.beginPath(); g.arc(0, 0, MARKER_R + .75, 0, Math.PI * 2); g.stroke(); } // dark outer keyline
    g.globalAlpha = 1;
    if (kind === 'dead') drawCross(g, tint, 1);
    return finishSprite({canvas, fallback: art ? null : 'initials'});
  }
  // Promote the baked canvas to an ImageBitmap (immutable, cheap to blit on both CPU- and GPU-backed targets).
  function finishSprite(sprite) {
    if (typeof createImageBitmap === 'function') createImageBitmap(sprite.canvas).then(bitmap => { sprite.bitmap = bitmap; }).catch(() => {});
    return sprite;
  }
  function spriteFor(player, kind) {
    const rec = portraitFor(player.agent), initials = initialsOf(player);
    const art = rec ? `${rec.key}@${rec.state}` : `none:${initials}`;
    const key = `${art}|${player.team ?? 'u'}|${kind}|${dpr}`;
    let sprite = sprites.get(key);
    if (!sprite) { sprite = bakeSprite(rec, initials, player.team, kind); sprites.set(key, sprite); }
    return sprite;
  }
  function blitSprite(sprite, x, y, angle) {
    const css = sprite.canvas.width / dpr, source = sprite.bitmap || sprite.canvas;
    if (angle !== null && rotateMarkers) {
      // Opt-in rotation: image-space angle from project()+atan2 (never raw world yaw); sprite art is drawn "up".
      ctx.save(); ctx.translate(x, y); ctx.rotate(angle + Math.PI / 2); ctx.drawImage(source, -css / 2, -css / 2, css, css); ctx.restore();
    } else ctx.drawImage(source, Math.round((x - css / 2) * dpr) / dpr, Math.round((y - css / 2) * dpr) / dpr, css, css);
  }
  function drawChevron(p, angle, color) {
    // Separate heading glyph outside the largest (Team B double) ring: base at r+7, tip at r+15 (20..28 px), 2.55 rad half-angle.
    const tipD = MARKER_R + 15, baseD = MARKER_R + 7, wing = (tipD - baseD) / -Math.cos(2.55), notch = MARKER_R + 10.5;
    const tip = [p.x + Math.cos(angle) * tipD, p.y + Math.sin(angle) * tipD];
    ctx.beginPath(); ctx.moveTo(tip[0], tip[1]);
    ctx.lineTo(tip[0] + Math.cos(angle + 2.55) * wing, tip[1] + Math.sin(angle + 2.55) * wing);
    ctx.lineTo(p.x + Math.cos(angle) * notch, p.y + Math.sin(angle) * notch);
    ctx.lineTo(tip[0] + Math.cos(angle - 2.55) * wing, tip[1] + Math.sin(angle - 2.55) * wing);
    ctx.closePath(); ctx.lineJoin = 'round'; ctx.strokeStyle = INK; ctx.lineWidth = 2; ctx.stroke(); ctx.fillStyle = color; ctx.fill();
  }
  // Marker pass: bodies + HP bars at the drawn centre q, then displacement ticks (true position), then ONE final chevron pass.
  function paintMarkers(list) {
    for (const m of list) {
      blitSprite(spriteFor(m.player, 'alive'), m.q.x, m.q.y, m.angle);
      if (m.hp !== null) { // y+33..37: >= 3 px clear of the chevron's south reach (r+15 = 28, +1 outline)
        ctx.fillStyle = INK; ctx.fillRect(m.q.x - 13, m.q.y + 33, 26, 4);
        ctx.fillStyle = m.hp > 30 ? '#6de5a4' : '#ff6874';
        if (m.hp > 0) ctx.fillRect(m.q.x - 12, m.q.y + 34, Math.max(1.5, 24 * m.hp / 100), 2);
      }
    }
    for (const m of list) if (m.angle !== null) drawChevron(m.q, m.angle, m.color); // final pass: never occluded
  }
  // True-position annotation, drawn AFTER bodies + labels so nothing covers it: >2 px -> 1.5 px line (dark casing) from the true
  // position to the drawn ring edge; >4 px -> 3 px dot at the true position. Logged for the harness.
  let tickLog = [];
  function paintTicks(list) {
    tickLog = [];
    for (const m of list) {
      const dx = m.q.x - m.p.x, dy = m.q.y - m.p.y, d = Math.hypot(dx, dy), edge = ringReach(m.player.team);
      if (!(d > 2)) continue;
      let lineLen = 0;
      ctx.globalAlpha = 1; ctx.lineCap = 'butt';
      if (d > edge) { // true position lies outside the drawn ring: line runs to the ring edge
        const ex = m.q.x - dx / d * edge, ey = m.q.y - dy / d * edge; lineLen = d - edge;
        ctx.strokeStyle = INK; ctx.lineWidth = 3.5; ctx.beginPath(); ctx.moveTo(m.p.x, m.p.y); ctx.lineTo(ex, ey); ctx.stroke();
        ctx.strokeStyle = m.color; ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(m.p.x, m.p.y); ctx.lineTo(ex, ey); ctx.stroke();
      }
      const dot = d > 4;
      if (dot) {
        ctx.fillStyle = INK; ctx.beginPath(); ctx.arc(m.p.x, m.p.y, 2.5, 0, Math.PI * 2); ctx.fill();
        ctx.fillStyle = m.color; ctx.beginPath(); ctx.arc(m.p.x, m.p.y, 1.5, 0, Math.PI * 2); ctx.fill();
      }
      tickLog.push({id: m.player.id, disp: d, ringReach: edge, lineLen, dot, trueX: m.p.x, trueY: m.p.y});
    }
  }
  // Adaptive nudge around the TRUE position. Pair target = max(30, (footprintA + footprintB)/2 + 6) (footprint = drawn ring diameter incl. keyline).
  // Each marker moves <= NUDGE_CAP (16 px) from p; a pair that cannot reach its target within both caps is skipped and logged as unresolved.
  let nudgeLog = {unresolved: [], pairs: 0};
  function nudgeMarkers(list) {
    for (const m of list) { m.q = {x: m.p.x, y: m.p.y}; m.displaced = false; }
    nudgeLog = {unresolved: [], pairs: 0};
    if (nudgeOff || list.length < 2) return;
    const target = (a, b) => Math.max(30, ringReach(a.player.team) + ringReach(b.player.team) + 6);
    const active = [];
    for (let i = 0; i < list.length; i++) for (let j = i + 1; j < list.length; j++) {
      const a = list[i], b = list[j], t = target(a, b), d0 = Math.hypot(a.p.x - b.p.x, a.p.y - b.p.y);
      if (d0 >= t) continue;
      nudgeLog.pairs++;
      if (d0 + 2 * NUDGE_CAP < t) { nudgeLog.unresolved.push({a: a.player.id, b: b.player.id, dist: d0, target: t, reason: 'needs more than 2x16 px displacement'}); continue; }
      active.push([i, j, t]);
    }
    for (let iter = 0; iter < 60 && active.length; iter++) {
      let moved = false;
      for (const [i, j, t] of active) {
        const a = list[i], b = list[j];
        let dx = b.q.x - a.q.x, dy = b.q.y - a.q.y, d = Math.hypot(dx, dy);
        if (d >= t - .05) continue;
        if (d < .01) { const ang = (i * 2.399963) % (Math.PI * 2); dx = Math.cos(ang); dy = Math.sin(ang); d = 1; }
        const push = (t - d) / 2 + .01, ux = dx / d, uy = dy / d;
        const move = (m, sx, sy) => {
          let x = m.q.x + sx, y = m.q.y + sy; const ox = x - m.p.x, oy = y - m.p.y, od = Math.hypot(ox, oy);
          if (od > NUDGE_CAP) { x = m.p.x + ox / od * NUDGE_CAP; y = m.p.y + oy / od * NUDGE_CAP; }
          m.q = {x, y};
        };
        move(a, -ux * push, -uy * push); move(b, ux * push, uy * push); moved = true;
      }
      if (!moved) break;
    }
    for (const [i, j, t] of active) {
      const d = Math.hypot(list[i].q.x - list[j].q.x, list[i].q.y - list[j].q.y);
      if (d < t - .5) nudgeLog.unresolved.push({a: list[i].player.id, b: list[j].player.id, dist: d, target: t, reason: 'cap (16 px) or neighbouring markers prevented full separation'});
    }
    for (const m of list) m.displaced = Math.hypot(m.q.x - m.p.x, m.q.y - m.p.y) > 1;
  }
  // Death records per round: latest kill event per victim, else first alive==0 sample. Position = last sample before death.
  function computeDeaths() {
    deaths = new Map();
    if (!round) return;
    const where = (track, at) => { const pt = sample({...track, alive: undefined}, Math.max(0, clamp(at, 0, duration()) - 1)); return pt ? {x: pt.x, y: pt.y} : {}; };
    const kills = (round.events || []).filter(e => e.type === 'kill' && Number.isFinite(e.t) && e.target != null).sort((a, b) => a.t - b.t);
    for (const event of kills) { const track = round.players?.[event.target]; if (track) deaths.set(String(event.target), {t: event.t, ...where(track, event.t)}); }
    for (const [pid, track] of Object.entries(round.players || {})) {
      if (deaths.has(pid) || !Array.isArray(track.alive)) continue;
      const first = track.alive.findIndex(v => v === 0);
      if (first > 0) deaths.set(pid, {t: first * round.tickMs, ...where(track, first * round.tickMs)});
    }
  }
  function updateAria(counts) {
    const meta = match.rounds[roundIndex], side = team => sideLabel(meta, team);
    const key = [counts.alive[0], counts.alive[1], counts.alive.u, round.n].join('/');
    if (key === ariaKey && !ariaForce) return; // only on state change; never per animation frame
    ariaKey = key; ariaForce = false;
    const parts = [`${side(0)}: ${counts.alive[0]} of ${counts.total[0]} alive`, `${side(1)}: ${counts.alive[1]} of ${counts.total[1]} alive`];
    if (counts.total.u) parts.push(`${counts.alive.u} of ${counts.total.u} unassigned alive`);
    actorsCanvas.setAttribute('aria-label', `Round ${round.n}, ${time(elapsed)}. ${parts.join('. ')}.`);
  }
  function drawActors() {
    if (Math.min(devicePixelRatio || 1, 2) !== dpr) { resize(); return; } // DPR changed (zoom / monitor move): rebake
    const w = actorsCanvas.clientWidth, h = actorsCanvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    visibleMarkers = []; debugMarkers = []; labelLog = {placed: [], omitted: [], badges: [], states: []};
    if (!round || !match || !projection) { actorsCanvas.setAttribute('aria-label', projection ? 'Player positions and spike on map' : 'Player positions unavailable: map transform missing'); return; }
    const t = elapsed;
    const live = [], gone = [], counts = {alive: {0: 0, 1: 0, u: 0}, total: {0: 0, 1: 0, u: 0}};
    const frameIndex = clamp(Math.round(t / (round.tickMs || 100)), 0, Math.max(0, Number(round.frames) - 1));
    for (const player of match.players || []) {
      const track = round.players?.[player.id];
      if (!track) continue;
      const color = teamColor(player), group = player.team === 0 || player.team === 1 ? player.team : 'u';
      counts.total[group] += 1;
      if ($('trails').checked) {
        ctx.strokeStyle = color; ctx.globalAlpha = .42; ctx.lineWidth = 2; ctx.beginPath();
        let started = false;
        for (let trailT = Math.max(0, t - 2500); trailT <= t; trailT += Math.max(50, round.tickMs || 100)) {
          const point = sample(track, trailT), p = point && project(point.x, point.y);
          if (!p) { started = false; continue; }
          if (!started) { ctx.moveTo(p.x, p.y); started = true; } else ctx.lineTo(p.x, p.y);
        }
        ctx.stroke(); ctx.globalAlpha = 1;
      }
      const death = deaths.get(String(player.id));
      const dead = death && death.t <= t && !(Array.isArray(track.alive) && track.alive[frameIndex] === 1 && t - death.t > 2000); // >2 s alive again = revived
      if (dead) {
        const dp = Number.isFinite(death.x) && Number.isFinite(death.y) ? project(death.x, death.y) : null;
        if (dp) gone.push({p: dp, player, since: t - death.t});
        continue;
      }
      const point = sample(track, t), p = point && project(point.x, point.y);
      if (!p) continue;
      counts.alive[group] += 1;
      let angle = null;
      if (point.yaw !== null) {
        // World yaw -> image direction through the same swapped projection axes.
        const rad = point.yaw * Math.PI / 180;
        const tip = project(point.x + Math.cos(rad) * 260, point.y + Math.sin(rad) * 260);
        if (tip) angle = Math.atan2(tip.y - p.y, tip.x - p.x);
      }
      live.push({p, q: p, name: player.agent || player.id, color, hp: point.hp, player, angle});
    }
    visibleMarkers = live.map(({p, name, color, hp}) => ({p, name, color, hp}));
    nudgeLog = {unresolved: [], pairs: 0};
    const started = performance.now();
    if (!skipMarkers && artReady) {
      // Dead markers: 55 % portrait + team X for 1.5 s, then a dimmed X at the death spot for the rest of the round.
      for (const {p, player, since} of gone) {
        const sprite = spriteFor(player, since <= 1500 ? 'dead' : 'x'); blitSprite(sprite, p.x, p.y, null);
        debugMarkers.push({id: player.id, agent: player.agent ?? null, team: player.team ?? null, state: since <= 1500 ? 'dead' : 'x', x: p.x, y: p.y, ring: ringKind(player.team), hasSprite: true, fallback: sprite.fallback});
      }
      live.sort((a, b) => a.p.y - b.p.y); // isometric depth: lower on screen draws on top
      nudgeMarkers(live);
      paintMarkers(live);
      for (const m of live) {
        const sprite = spriteFor(m.player, 'alive');
        debugMarkers.push({id: m.player.id, agent: m.player.agent ?? null, team: m.player.team ?? null, state: 'alive', x: m.q.x, y: m.q.y, trueX: m.p.x, trueY: m.p.y, displaced: m.displaced, ring: ringKind(m.player.team), hasSprite: true, fallback: sprite.fallback});
      }
      if (!skipLabels) drawLabels(live, w, h, gone);
      paintTicks(live);
    }
    markerMsLog.push(performance.now() - started); if (markerMsLog.length > 600) markerMsLog.shift();
    drawAbilities(t);
    if (window.UtilityLayer && typeof window.UtilityLayer.draw === 'function') window.UtilityLayer.draw(ctx, t,
      {round, match, project, colors, players: match.players,
        showPaths: $('utility-paths').checked, showAreas: $('utility-areas').checked,
        playerFilter: $('utility-player').value, teamFilter: $('utility-team').value, iconFor, redraw: drawActors});
    const spike = round.spike || {};
    if (Number.isFinite(spike.plantT) && t >= spike.plantT && (!Number.isFinite(spike.defuseT) || t < spike.defuseT)) {
      const p = project(spike.plantX, spike.plantY);
      if (p) { ctx.strokeStyle = '#ffcf6b'; ctx.lineWidth = 3; ctx.beginPath(); ctx.arc(p.x, p.y, 13 + 3 * Math.sin(t / 180), 0, Math.PI * 2); ctx.stroke();
        ctx.fillStyle = '#ffcf6b'; ctx.font = '20px sans-serif'; ctx.textAlign = 'center'; ctx.fillText('◆', p.x, p.y + 7); }
    }
    updateAria(counts);
  }
  // Label placement. Every marker ends in exactly one state: labelled | in-badge(id) | omitted(reason). A label is only omitted when no
  // candidate box fits on the canvas at all; when every clean slot is taken it falls back to the least-overlapping on-screen slot.
  const widthCache = new Map();
  const labelWidth = name => { let v = widthCache.get(name); if (v === undefined) { v = ctx.measureText(name).width; widthCache.set(name, v); } return v; };
  function drawLabels(markers, w, h, gone = []) {
    labelLog = {placed: [], omitted: [], badges: [], states: [], crowded: [], rects: [], deadRects: []};
    ctx.font = '600 11px sans-serif'; ctx.textAlign = 'center'; ctx.textBaseline = 'alphabetic';
    const overlapArea = (a, b) => Math.max(0, Math.min(a.right, b.right) - Math.max(a.left, b.left)) * Math.max(0, Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top));
    const bodies = markers.map(({q: p, player}) => { const r = ringReach(player.team) + .5; return {left: p.x - r, right: p.x + r, top: p.y - r, bottom: p.y + r}; });
    // Dead markers repel labels exactly like live ones: full sprite footprint + 2 px, kept 8 px clear of label boxes.
    const deadBodies = gone.map(({p, player}) => { const r = ringReach(player.team) + 2; labelLog.deadRects.push({id: player.id, x: p.x, y: p.y, r}); return {left: p.x - r - 8, right: p.x + r + 8, top: p.y - r - 8, bottom: p.y + r + 8}; });
    const occupied = [];
    // Badge: >= 3 markers chained within ONE marker diameter (26 px) after nudging. ?markers=nudge-off restores the legacy 1.5-diameter grouping.
    const near = nudgeOff ? 1.5 * BADGE_DIST : BADGE_DIST, parent = markers.map((_, i) => i);
    const find = i => { while (parent[i] !== i) i = parent[i] = parent[parent[i]]; return i; };
    for (let i = 0; i < markers.length; i++) for (let j = i + 1; j < markers.length; j++)
      if (Math.hypot(markers[i].q.x - markers[j].q.x, markers[i].q.y - markers[j].q.y) <= near) parent[find(i)] = find(j);
    const groups = new Map();
    markers.forEach((m, i) => { const root = find(i); if (!groups.has(root)) groups.set(root, []); groups.get(root).push(i); });
    const clustered = new Map();
    for (const members of groups.values()) {
      if (members.length < 3) continue;
      const badgeId = `badge${labelLog.badges.length}`;
      members.forEach(i => clustered.set(i, badgeId));
      const cx = members.reduce((s, i) => s + markers[i].q.x, 0) / members.length;
      const top = Math.min(...members.map(i => markers[i].q.y));
      const bx = clamp(cx, 18, w - 18), by = clamp(top - 40, 12, h - 12); // above the pile (and its chevrons) so no identity is covered
      ctx.fillStyle = '#101820ee'; ctx.fillRect(bx - 15, by - 9, 30, 17);
      ctx.strokeStyle = '#e8eef2'; ctx.lineWidth = 1; ctx.strokeRect(bx - 14.5, by - 8.5, 29, 16);
      ctx.fillStyle = '#fff'; ctx.fillText(`+${members.length}`, bx, by + 4);
      occupied.push({left: bx - 15, right: bx + 15, top: by - 9, bottom: by + 8});
      labelLog.badges.push({id: badgeId, members: members.map(i => markers[i].player.id), x: bx, y: by});
    }
    markers.forEach(({player}, index) => { if (clustered.has(index)) labelLog.states.push({id: player.id, state: 'in-badge', badge: clustered.get(index)}); });
    markers.forEach(({q: p, name, color, player}, index) => {
      if (clustered.has(index)) return;
      const width = labelWidth(name) + 12, side = width / 2 + 31;
      // chevron reach is 28 (+1 outline), HP bar to 34: labels start outside both
      const base = [[0, -39], [0, 50], [-side, 0], [side, 0], [-side, -34], [side, -34], [-side, 34], [side, 34], [0, -57], [0, 64], [-side - 16, 0], [side + 16, 0], [-side, -52], [side, -52], [-side, 52], [side, 52], [0, -75], [0, 82]];
      let best = null;
      for (const [dx, dy] of base) {
        const x = p.x + dx, y = p.y + dy, box = {left: x - width / 2, right: x + width / 2, top: y - 9, bottom: y + 7};
        if (box.left < 4 || box.right > w - 4 || box.top < 4 || box.bottom > h - 4) continue; // off-canvas: next candidate
        let cost = 0;
        for (const other of occupied) cost += overlapArea(box, other) * 20; // another label / badge: heavily penalised
        for (const body of bodies) cost += overlapArea(box, body);
        for (const body of deadBodies) cost += overlapArea(box, body) * 20;
        if (!best || cost < best.cost) best = {cost, dx, dy, x, y, box};
        if (cost === 0) break;
      }
      if (!best) { // nothing fits on the canvas at all (canvas smaller than the label)
        const reason = 'no candidate label box fits inside the canvas';
        labelLog.omitted.push({id: player.id, reason}); labelLog.states.push({id: player.id, state: 'omitted', reason}); return;
      }
      if (best.cost > 0) labelLog.crowded.push({id: player.id, overlapPx2: best.cost});
      const {dx, dy, x, y, box} = best, len = Math.hypot(dx, dy) || 1, start = dy > 0 && Math.abs(dx) < 14 ? 38 : 30; // leader starts outside chevron + ring (and below the HP bar when heading south)
      ctx.strokeStyle = color; ctx.globalAlpha = .6; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(p.x + dx / len * start, p.y + dy / len * start); ctx.lineTo(x, y); ctx.stroke(); ctx.globalAlpha = 1;
      ctx.fillStyle = '#101820e8'; ctx.fillRect(box.left, box.top, width, 16);
      ctx.fillStyle = color; ctx.fillRect(box.left, box.top, 2, 16);
      ctx.fillStyle = '#fff'; ctx.fillText(name, x, y + 3);
      occupied.push(box); labelLog.rects.push({id: player.id, left: box.left, right: box.right, top: box.top, bottom: box.bottom}); labelLog.placed.push({id: player.id, team: player.team ?? null, name});
      labelLog.states.push({id: player.id, state: 'labelled'});
    });
  }
  window.replayPerf = {
    // Redraws the marker layer `frames` times (seeking across the round, with a 1px readback to flush the GPU queue).
    measureMarkers(frames = 200) {
      if (!round) return null;
      const keep = elapsed, times = [], span = Math.max(1000, Math.min(duration() - 1000, 25000));
      for (let i = 0; i < frames; i++) {
        elapsed = 2000 + (i * 137) % span; const t0 = performance.now();
        drawActors(); ctx.getImageData(0, 0, 1, 1); times.push(performance.now() - t0);
      }
      elapsed = keep; drawActors(); times.sort((a, b) => a - b);
      const q = f => times[Math.min(times.length - 1, Math.floor(f * times.length))];
      return {frames, dpr, skipMarkers, p50: q(.5), p95: q(.95), max: times[times.length - 1]};
    },
    // QA pixel probe: paints one marker (real sprite/bar/chevron code) at a fixed spot on the actors canvas, returns RGBA, then restores.
    probeMarker({team = 0, agent = null, angle = null, hp = 100, name = 'Zzyzx', dead = false} = {}) {
      const w = actorsCanvas.clientWidth, h = actorsCanvas.clientHeight, cx = Math.round(w / 2), cy = Math.round(h / 2), half = 40;
      const player = {id: '__probe', team, agent: agent || name};
      ctx.clearRect(0, 0, w, h);
      const m = {p: {x: cx, y: cy}, q: {x: cx, y: cy}, name: player.agent, color: teamTint(team), hp, player, angle, displaced: false};
      if (dead) blitSprite(spriteFor(player, 'dead'), cx, cy, null); else paintMarkers([m]);
      const img = ctx.getImageData(Math.round((cx - half) * dpr), Math.round((cy - half) * dpr), Math.round(2 * half * dpr), Math.round(2 * half * dpr));
      ctx.clearRect(0, 0, w, h); scheduleDraw();
      return {dpr, half, w: img.width, h: img.height, color: teamTint(team), data: Array.from(img.data)};
    },
    spriteStats() {
      const scales = [...new Set([...sprites.values()].map(s => Math.round(s.canvas.width / SPRITE_BOX * 1000) / 1000))];
      return {count: sprites.size, scale: dpr, spriteScales: scales, bytes: [...sprites.values()].reduce((n, s) => n + s.canvas.width * s.canvas.height * 4, 0)};
    }
  };
  window.replayDebug = () => ({ticks: tickLog.map(t => ({...t})), markers: debugMarkers.map(m => ({...m})), rotation: rotateMarkers, sprites: sprites.size, skipMarkers, artReady, dpr,
    markerMs: markerMsLog.slice(), ariaLabel: actorsCanvas.getAttribute('aria-label'), labels: JSON.parse(JSON.stringify(labelLog)), nudge: JSON.parse(JSON.stringify(nudgeLog))});
  function moment() {
    $('clock').textContent = time(elapsed); $('duration').textContent = ` / ${time(duration())}`;
    $('scrub').max = String(Math.max(1, duration())); $('scrub').value = String(Math.round(elapsed));
    updateHash(); drawActors();
    document.dispatchEvent(new Event('replay:state'));
    const feed = $('feed'); feed.replaceChildren();
    const hasUtility = Array.isArray(round?.utility) && round.utility.length > 0;
    // Tracked utility replaces legacy ability events in the feed (ult casts stay); no duplicates.
    const events = (round?.events || [])
      .filter(event => Number.isFinite(event.t) && event.t <= elapsed && !(hasUtility && event.type === 'ability'))
      .map(event => ({t: event.t, event}));
    const utilities = hasUtility ? round.utility
      .filter(item => item && item.kind !== 'state' && Number.isFinite(item.t) && item.t <= elapsed)
      .map(item => ({t: item.t, util: item})) : [];
    const entries = [...events, ...utilities].sort((a, b) => b.t - a.t).slice(0, 8);
    for (const entry of entries) {
      if (entry.util) {
        const item = entry.util;
        const player = match.players.find(p => p.id === item.actor);
        const ability = display(item.ability, item.kind || 'Utility');
        const btn = document.createElement('button');
        btn.type = 'button'; btn.className = 'feed-item feed-utility';
        const iconUrl = iconFor(item.agent, item.ability);
        if (iconUrl) {
          const img = document.createElement('img');
          img.src = iconUrl; img.alt = ''; img.loading = 'lazy';
          img.addEventListener('error', () => img.remove());
          btn.append(img);
        } else {
          const diamond = document.createElement('span'); diamond.textContent = '◆';
          btn.append(diamond);
        }
        const who = display(player?.name || player?.agent, 'Unowned');
        const label = document.createElement('span');
        label.textContent = `${who} · ${ability}`;
        btn.append(label);
        btn.title = `R${display(round?.n, '?')} · ${time(item.t)} · ${ability} · click to seek`;
        btn.setAttribute('aria-label', `R${display(round?.n, '?')} · ${time(item.t)} · ${who} · ${ability} · press Enter to seek`);
        btn.addEventListener('click', () => seek(item.t));
        feed.append(btn);
        continue;
      }
      const event = entry.event;
      const item = document.createElement('div'); item.className = 'feed-item';
      const actor = match.players.find(p => p.id === event.actor);
      const victim = match.players.find(p => p.id === event.target);
      const localArt = event.type === 'kill' && actor ? artCandidates(actor.agent)[0] : null; // offline: local portrait only
      if (localArt) {
        const img = document.createElement('img'); img.src = localArt;
        img.alt = ''; img.loading = 'lazy'; img.addEventListener('error', () => img.remove()); item.append(img);
      }
      const colored = (player, fallback) => {
        const span = document.createElement('span');
        span.className = `feed-player ${player?.team === 1 ? 'defender' : player?.team === 0 ? 'attacker' : ''}`;
        span.textContent = display(player?.name || player?.agent || fallback, 'Spike');
        if (teamLabel(player?.team)) span.title = teamLabel(player.team);
        return span;
      };
      const text = document.createElement('span');
      text.append(colored(actor, event.actor), event.type === 'kill' ? ' ✕ ' : ` ${event.ability || event.type}`);
      if (event.type === 'kill') text.append(colored(victim, event.target));
      item.append(text);
      feed.append(item);
    }
    if (!entries.length) feed.textContent = 'No recorded events yet.';
  }
  function buildTicks() {
    const container = $('ticks'); container.replaceChildren();
    const entries = [...(round.events || []),
      ...['plantT', 'defuseT'].filter(k => Number.isFinite(round.spike?.[k])).map(k => ({t: round.spike[k], type: k === 'plantT' ? 'plant' : 'defuse'}))];
    const seen = new Set();
    for (const event of entries) {
      if (!['kill', 'plant', 'defuse'].includes(event.type) || !Number.isFinite(event.t)) continue;
      const key = `${event.type}-${event.t}`; if (seen.has(key)) continue; seen.add(key);
      const x = event.t / (duration() || 1) * 100;
      if (x < 0 || x > 100) continue;
      const tick = document.createElement('span'); tick.className = `tick ${event.type}`;
      tick.textContent = {kill: '✕', plant: '◆', defuse: '✓'}[event.type];
      tick.setAttribute('aria-hidden', 'true'); tick.style.left = `${x}%`; tick.title = `${event.type} at ${time(event.t)}`;
      container.append(tick);
    }
    const wrap = container.parentElement; const width = container.clientWidth || 225;
    const items = (Array.isArray(round?.utility) ? round.utility : [])
      .filter(i => i && i.kind !== 'state' && Number.isFinite(i.t) && i.t >= 0 && i.t <= (duration() || 1))
      .sort((p, q) => p.t - q.t);
    const label = item => { const pl = match.players.find(p => p.id === item.actor);
      return `${display(pl?.name || pl?.agent, 'Unowned')} · ${display(item.ability, item.kind || 'Utility')} · ${time(item.t)}`; };
    // Cluster marks closer than the 24px hit size; each cluster is ONE button at its first throw's exact x.
    const clusters = [];
    for (const item of items) {
      const px = item.t / (duration() || 1) * width, last = clusters[clusters.length - 1];
      if (last && px - last.px < 24) last.items.push(item); else clusters.push({px, items: [item]});
    }
    const closePop = () => { wrap.querySelectorAll('.tick-pop').forEach(n => n.remove()); };
    for (const c of clusters) {
      const tick = document.createElement('button'); tick.type = 'button'; tick.tabIndex = -1;
      tick.className = ['tick', 'util', c.items.length === 1 ? c.items[0].kind : 'cluster'].filter(Boolean).join(' ');
      tick.style.left = `${c.items[0].t / (duration() || 1) * 100}%`;
      tick.dataset.t = String(c.items[0].t); tick.dataset.count = String(c.items.length);
      if (c.items.length === 1) {
        tick.textContent = '◆'; tick.title = label(c.items[0]);
        tick.setAttribute('aria-label', `Seek to ${label(c.items[0])}`);
        tick.addEventListener('click', () => { closePop(); seek(c.items[0].t); });
      } else {
        tick.textContent = String(c.items.length); tick.title = `${c.items.length} throws near ${time(c.items[0].t)} — click to choose`;
        tick.setAttribute('aria-label', `${c.items.length} throws near ${time(c.items[0].t)}; choose one`);
        tick.setAttribute('aria-haspopup', 'true');
        tick.addEventListener('click', ev => {
          ev.stopPropagation(); const had = tick.getAttribute('aria-expanded') === 'true'; closePop();
          container.querySelectorAll('[aria-expanded]').forEach(n => n.setAttribute('aria-expanded', 'false'));
          if (had) return;
          tick.setAttribute('aria-expanded', 'true');
          const pop = document.createElement('div'); pop.className = 'tick-pop'; pop.setAttribute('role', 'group');
          pop.setAttribute('aria-label', `Throws near ${time(c.items[0].t)}`);
          for (const item of c.items) {
            const b = document.createElement('button'); b.type = 'button'; b.textContent = label(item); b.dataset.t = String(item.t);
            b.addEventListener('click', () => { closePop(); tick.setAttribute('aria-expanded', 'false'); seek(item.t); });
            pop.append(b);
          }
          wrap.append(pop);
          const tr = tick.getBoundingClientRect(), wr = wrap.getBoundingClientRect(), pw = pop.offsetWidth;
          pop.style.left = `${Math.max(0, Math.min(tr.left - wr.left, wr.width - pw))}px`;
        });
      }
      container.append(tick);
    }
  }
  document.addEventListener('click', e => { if (!e.target.closest?.('.tick-pop')) document.querySelectorAll('.tick-pop').forEach(n => n.remove()); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.querySelectorAll('.tick-pop').forEach(n => n.remove()); });
  if (window.ResizeObserver) { let rw = 0; new ResizeObserver(() => { const w = $('ticks').clientWidth; if (w && w !== rw) { rw = w; if (typeof round !== 'undefined' && round) buildTicks(); } }).observe($('ticks')); }
  function buildStrip() {
    const strip = $('round-strip'); strip.replaceChildren();
    $('round-count').textContent = String(match.rounds.length);
    match.rounds.forEach((meta, index) => {
      if (index % 12 === 0) {
        const label = document.createElement('div'); label.className = 'half-label';
        label.textContent = index < 24 ? `HALF ${Math.floor(index / 12) + 1}` : `OVERTIME · SET ${Math.floor((index - 24) / 2) + 1}`;
        strip.append(label);
      }
      const btn = document.createElement('button'); btn.type = 'button'; btn.textContent = String(meta.n);
      btn.className = `round-button ${index === roundIndex ? 'active' : ''}`;
      btn.title = `Round ${meta.n} · ${sideLabel(meta, 0)} / ${sideLabel(meta, 1)} · ${outcome(meta)}`;
      if (meta.winner === 0 || meta.winner === 1) {
        btn.classList.add(meta.winner === 0 ? 'won-a' : 'won-b');
        btn.setAttribute('aria-label', btn.title);
      }
      btn.addEventListener('click', () => chooseRound(index)); strip.append(btn);
    });
  }
  function buildRoster() {
    const roster = $('roster'); roster.replaceChildren();
    for (const player of match.players || []) {
      const row = document.createElement('div'); row.className = 'roster-row';
      const portrait = player.agent && assetIndex[player.agent]?.portrait;
      if (typeof portrait === 'string' && /^agents\/[a-z0-9-]+\.png$/i.test(portrait)) {
        const image = document.createElement('img'); image.className = 'agent-icon'; image.alt = '';
        image.src = `${window.replayAssetBase}assets/${portrait}`;
        image.addEventListener('error', () => image.remove()); row.append(image);
      }
      const dot = document.createElement('i'); dot.className = `legend-dot ${player.team === 1 ? 'defender' : 'attacker'}`;
      const label = document.createElement('span'); label.textContent = display(player.name || player.agent || player.id);
      // Side as text, not color-only: team 0 = ATK, 1 = DEF, unknown = —.
      const tag = document.createElement('span'); tag.className = 'role-tag';
      tag.textContent = player.team === 0 ? 'ATK' : player.team === 1 ? 'DEF' : '—';
      row.title = teamLabel(player.team);
      row.append(dot, label, tag); roster.append(row);
    }
  }
  function buildUtilityPlayers() {
    const select = $('utility-player'), keep = select.value;
    const all = document.createElement('option'); all.value = ''; all.textContent = 'All players';
    select.replaceChildren(all);
    for (const player of match.players || []) {
      const option = document.createElement('option');
      option.value = String(player.id); option.textContent = display(player.name || player.agent || player.id);
      select.append(option);
    }
    if ([...select.options].some(option => option.value === keep)) select.value = keep;
  }
  function buildSpike() {
    const spike = round.spike || {};
    const rows = [];
    if (Number.isFinite(spike.plantT)) rows.push(`◆ Plant ${time(spike.plantT)}${spike.site ? ` · ${spike.site}` : ''}`);
    if (Number.isFinite(spike.defuseT)) rows.push(`✓ Defuse ${time(spike.defuseT)}`);
    $('spike-info').textContent = rows.length ? rows.join('  /  ') : 'No plant or defuse recorded.';
  }
  async function fetchRound(index) {
    if (index < 0 || index >= match.rounds.length) return null;
    const n = match.rounds[index].n;
    if (cache.has(n)) return cache.get(n);
    if (loading.has(n)) return loading.get(n);
    const promise = getJSON(roundUrl(n)).then(data => {
      if (!['valoplant_round/v1', 'valoplant_round/v2'].includes(data.schema) || data.n !== n) throw new Error(`Invalid round ${n}`);
      cache.set(n, data);
      while (cache.size > 4) cache.delete(cache.keys().next().value);
      return data;
    }).finally(() => loading.delete(n));
    loading.set(n, promise); return promise;
  }
  async function chooseRound(index, offset = 0) {
    if (index < 0 || index >= match.rounds.length) return;
    playing = false; $('play').textContent = '▶'; $('play').setAttribute('aria-label', 'Play');
    const token = ++generation; $('loading').hidden = false; $('loading').textContent = 'Loading round…';
    try {
      const data = await fetchRound(index); if (token !== generation) return;
      round = data; roundIndex = index; elapsed = clamp(offset, 0, duration());
      $('loading').hidden = true; $('stage-round').textContent = `ROUND ${data.n}`;
      const meta = match.rounds[index];
      $('legend-a').textContent = sideLabel(meta, 0);
      $('legend-b').textContent = sideLabel(meta, 1);
      $('round-outcome').textContent = `R${data.n}: ${outcome(meta)}`;
      computeDeaths(); ariaForce = true; buildStrip(); buildTicks(); buildSpike(); moment();
      for (const near of [index - 1, index + 1]) fetchRound(near).catch(() => {});
    } catch (error) { if (token === generation) fail(`Unable to load round: ${error.message}`); }
  }
  function seek(value) { if (!round) return; ariaForce = true; elapsed = clamp(value, 0, duration()); moment(); }
  function animate(now) {
    if (playing && round && lastFrame) {
      elapsed = Math.min(duration(), elapsed + Math.min(now - lastFrame, 150) * Number($('speed').value));
      moment();
      if (elapsed >= duration()) { playing = false; $('play').textContent = '▶'; $('play').setAttribute('aria-label', 'Play'); }
    }
    lastFrame = now; requestAnimationFrame(animate);
  }
  async function init() {
    if (!id) { fail('Missing replay id. Open a library replay or use ?demo=1.'); return; }
    try {
      match = await getJSON(matchUrl);
      if (!['valoplant_match/v1', 'valoplant_match/v2'].includes(match.schema) || !Array.isArray(match.rounds) || !match.rounds.length) throw new Error('Invalid match metadata');
      $('match-title').textContent = `${display(match.map?.name)} · ${match.rounds.length} rounds`;
      $('match-subtitle').textContent = `REPLAY ${match.id}`; $('map-name').textContent = display(match.map?.name);
      $('patch').textContent = `PATCH ${display(match.patch, '—')}`;
      if (!demo) getJSON(`/api/replays/${encodeURIComponent(id)}/quality`).then(quality => {
        if (quality.status !== 'suspect') return;
        const badge = $('quality-badge'); badge.hidden = false;
        badge.title = quality.suspectReason || 'Decoder output has suspiciously low sample density';
        badge.setAttribute('aria-label', `Suspect: ${badge.title}`);
      }).catch(() => {});
      const aHref = demo ? `${window.replayAssetBase}analysis.html?id=demo` :
        location.pathname.startsWith('/bot/web/static/replays_app/') ? `${window.replayAssetBase}analysis.html?id=${encodeURIComponent(id)}` :
          `/replays/${encodeURIComponent(id)}/analysis`;
      $('analysis-link').href = aHref; $('analysis-link-header').href = aHref;
      if (location.pathname.startsWith('/bot/web/static/replays_app/'))
        document.querySelector('.header-actions a[href="/replays"]').href = `${window.replayAssetBase}index.html`;
      projection = ['xMultiplier', 'yMultiplier', 'xScalarToAdd', 'yScalarToAdd'].every(key => Number.isFinite(match.map?.[key])) &&
        match.map.xMultiplier !== 0 && match.map.yMultiplier !== 0;
      if (!projection) {
        $('map-warning').hidden = false;
        $('map-warning').textContent = 'Map transform unavailable; map shown fit-to-image. Player positions cannot be safely projected.';
      }
      const imageUrl = validMedia(match.map?.image);
      const localMap = typeof match.map?.uuid === 'string' && /^[\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12}$/i.test(match.map.uuid)
        ? `${window.replayAssetBase}assets/maps/${match.map.uuid}.png` : '';
      mapImage.onload = drawMap;
      if (localMap) {
        mapImage.onerror = () => { mapImage.onerror = drawMap; if (imageUrl) mapImage.src = imageUrl; else drawMap(); };
        mapImage.src = localMap;
      } else if (imageUrl) { mapImage.onerror = drawMap; mapImage.src = imageUrl; }
      fetch(`${window.replayAssetBase}assets/agents/index.json`).then(r => r.ok ? r.json() : {}).then(data => { assetIndex = data && typeof data === 'object' ? data : {}; }).catch(() => {})
        .then(() => Promise.race([Promise.allSettled((match.players || []).map(pl => portraitFor(pl.agent)?.settled)), sleep(2500)]))
        .then(() => { artReady = true; buildRoster(); if (round) moment(); else drawActors(); });
      setTimeout(() => { if (!artReady) { artReady = true; scheduleDraw(); } }, 3500);
      buildRoster(); buildUtilityPlayers(); resize();
      const saved = hashMoment();
      const index = match.rounds.findIndex(r => r.n === saved.n);
      await chooseRound(index < 0 ? 0 : index, Number.isFinite(saved.t) ? saved.t * 1000 : 0);
    } catch (error) { fail(`Replay unavailable: ${error.message}`); }
  }
  // Small playback adapter used by the optional lobby; the viewer remains the sole owner of state.
  window.replayPlayback = {
    get: () => round ? {replayId: id, round: round.n, tMs: Math.round(elapsed),
      speed: Number($('speed').value), paused: !playing} : null,
    async apply(state) {
      if (!match || !Array.isArray(match.rounds)) return;
      const serial = ++remoteGeneration;
      const index = match.rounds.findIndex(item => item.n === state.round);
      if (index < 0 || !Number.isFinite(state.tMs)) return;
      if (roundIndex !== index) await chooseRound(index, state.tMs);
      if (serial !== remoteGeneration || !round || roundIndex !== index) return;
      seek(state.tMs);
      const speed = String(state.speed);
      if ([...$('speed').options].some(option => option.value === speed)) $('speed').value = speed;
      playing = !state.paused;
      $('play').textContent = playing ? 'Ⅱ' : '▶';
      $('play').setAttribute('aria-label', playing ? 'Pause' : 'Play');
      lastFrame = 0;
    }
  };
  document.dispatchEvent(new Event('replay:adapter'));
  $('prev').addEventListener('click', () => chooseRound(roundIndex - 1));
  $('next').addEventListener('click', () => chooseRound(roundIndex + 1));
  $('play').addEventListener('click', () => { if (!round) return; if (elapsed >= duration()) seek(0); playing = !playing; $('play').textContent = playing ? 'Ⅱ' : '▶'; $('play').setAttribute('aria-label', playing ? 'Pause' : 'Play'); lastFrame = 0; document.dispatchEvent(new Event('replay:state')); });
  $('speed').addEventListener('change', () => document.dispatchEvent(new Event('replay:state')));
  $('scrub').addEventListener('input', event => seek(Number(event.target.value)));
  $('trails').addEventListener('change', drawActors);
  $('abilities').addEventListener('change', drawActors);
  rotateMarkers = $('marker-rotate').checked = false;
  $('marker-rotate').addEventListener('change', () => { rotateMarkers = $('marker-rotate').checked; drawActors(); });
  for (const control of ['utility-paths', 'utility-areas', 'utility-player', 'utility-team'])
    $(control).addEventListener('change', drawActors);
  $('map-stage').addEventListener('mousemove', event => {
    const stage = $('map-stage'), tooltip = $('actor-tooltip'), utilTip = $('utility-tooltip'), rect = stage.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    const nearby = visibleMarkers.filter(({p}) => Math.hypot(p.x-x,p.y-y) <= 16);
    tooltip.hidden = nearby.length === 0;
    if (!nearby.length) {
      // No player marker nearby: surface utility under the cursor instead.
      const hit = window.UtilityLayer?.hitTest?.(x, y);
      utilTip.hidden = !hit?.item;
      if (hit?.item) {
        utilTip.textContent = utilityLabel(hit.item);
        utilTip.style.left = `${clamp(x+16,8,Math.max(8,rect.width-240))}px`;
        utilTip.style.top = `${clamp(y-12,8,Math.max(8,rect.height-40))}px`;
      }
      return;
    }
    utilTip.hidden = true;
    tooltip.replaceChildren();
    for (const {name,color,hp} of nearby) {
      const label = document.createElement('span'); label.textContent = `${name}${hp === null ? '' : ` · ${hp} HP`}`; label.style.color = color;
      tooltip.append(label);
    }
    tooltip.style.left = `${clamp(x+16,8,Math.max(8,rect.width-140))}px`;
    tooltip.style.top = `${clamp(y-12,8,Math.max(8,rect.height-40))}px`;
  });
  $('map-stage').addEventListener('mouseleave', () => { $('actor-tooltip').hidden = true; $('utility-tooltip').hidden = true; });
  $('map-stage').addEventListener('click', event => {
    const rect = $('map-stage').getBoundingClientRect();
    const hit = window.UtilityLayer?.hitTest?.(event.clientX - rect.left, event.clientY - rect.top);
    if (hit?.item && Number.isFinite(hit.item.t)) seek(hit.item.t);
  });
  $('share').addEventListener('click', async () => { try { await navigator.clipboard.writeText(location.href); $('share').textContent = 'Link copied ✓'; setTimeout(() => $('share').textContent = 'Share this moment ↗', 1800); } catch { $('share').textContent = 'Copy URL from address bar'; } });
  document.addEventListener('keydown', event => {
    if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey) return;
    const ae = document.activeElement, tag = ae?.tagName;
    // Never hijack text/select/range/radio edits, contenteditable, or composite widgets that own arrows (tabs, listbox, menu, slider...).
    const editing = tag === 'TEXTAREA' || tag === 'SELECT' || ae?.isContentEditable
      || (tag === 'INPUT' && !['button', 'checkbox', 'submit', 'reset'].includes(ae.type))
      || !!ae?.closest?.('[role=tab],[role=tablist],[role=listbox],[role=menu],[role=menubar],[role=slider],[role=radiogroup],[role=grid],[role=tree]');
    if (editing) return;
    // Space on a focused button/link/checkbox is native activation; do not also toggle play.
    const nativeActivator = tag === 'BUTTON' || tag === 'A' || tag === 'INPUT' || tag === 'SUMMARY';
    if (event.code === 'Space' && !nativeActivator) { event.preventDefault(); $('play').click(); }
    if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') { event.preventDefault(); seek(elapsed + (event.key === 'ArrowRight' ? 5000 : -5000)); }
    if (event.key === '[') chooseRound(roundIndex - 1);
    if (event.key === ']') chooseRound(roundIndex + 1);
  });
  addEventListener('hashchange', () => { const saved = hashMoment(); const index = match?.rounds.findIndex(r => r.n === saved.n); if (index >= 0) chooseRound(index, (saved.t || 0) * 1000); });
  addEventListener('resize', resize);
  // devicePixelRatio changes (browser zoom, moving between monitors) without a window resize: rebake at the new scale.
  (function watchDpr() {
    const query = matchMedia(`(resolution: ${devicePixelRatio}dppx)`);
    query.addEventListener('change', () => { resize(); watchDpr(); }, {once: true});
  })();
  requestAnimationFrame(animate);
  init();
})();

// U2: tabbed, collapsible inspector (layout only; no playback state touched).
(function inspectorTabs() {
  const tabs = [...document.querySelectorAll('.inspector-tabs [role=tab]')];
  const layout = document.querySelector('.viewer-layout');
  const toggle = document.getElementById('inspector-toggle');
  if (!tabs.length || !layout || !toggle) return;
  function select(tab, focus) {
    for (const t of tabs) {
      const on = t === tab;
      t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1;
      document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
    }
    if (focus) tab.focus();
  }
  tabs.forEach((t, i) => {
    t.addEventListener('click', () => { if (layout.classList.contains('inspector-collapsed')) setCollapsed(false); select(t); });
    t.addEventListener('keydown', e => {
      const d = {ArrowRight: 1, ArrowLeft: -1}[e.key];
      if (d) { e.preventDefault(); select(tabs[(i + d + tabs.length) % tabs.length], true); }
      else if (e.key === 'Home') { e.preventDefault(); select(tabs[0], true); }
      else if (e.key === 'End') { e.preventDefault(); select(tabs[tabs.length - 1], true); }
    });
  });
  function setCollapsed(c) {
    layout.classList.toggle('inspector-collapsed', c);
    toggle.setAttribute('aria-expanded', String(!c));
    toggle.textContent = c ? '«' : '»';
    const label = c ? 'Expand inspector' : 'Collapse inspector';
    toggle.title = label; toggle.setAttribute('aria-label', label);
    dispatchEvent(new Event('resize'));
  }
  toggle.addEventListener('click', () => setCollapsed(!layout.classList.contains('inspector-collapsed')));
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !layout.classList.contains('inspector-collapsed') && document.querySelector('#inspector').contains(document.activeElement)) { setCollapsed(true); toggle.focus(); }
  });
  const title = document.getElementById('match-title'), h1 = document.getElementById('viewer-h1');
  if (title && h1) new MutationObserver(() => { h1.textContent = `2D replay · ${title.textContent}`; }).observe(title, {childList: true, characterData: true, subtree: true});
})();

// U2: keep canvas bitmaps in step with the flexible map stage (layout reflow without a window resize, e.g. inspector collapse).
(function stageObserver() {
  const stage = document.getElementById('map-stage');
  if (!stage || typeof ResizeObserver === 'undefined') return;
  let w = 0, h = 0, raf = 0;
  new ResizeObserver(entries => {
    const r = entries[0].contentRect;
    if (Math.abs(r.width - w) < 1 && Math.abs(r.height - h) < 1) return;
    w = r.width; h = r.height;
    cancelAnimationFrame(raf); raf = requestAnimationFrame(() => dispatchEvent(new Event('resize')));
  }).observe(stage);
})();
