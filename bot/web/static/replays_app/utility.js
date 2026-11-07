// Utility throws layer: dashed flight paths, bounce ×, landing icons, effect areas.
// Loaded before viewer.js; the viewer calls window.UtilityLayer.draw(ctx, tMs, state) each frame.
(() => {
  'use strict';
  const NEUTRAL = '#9aa7b0';
  const HIT_RADIUS = 14;
  const icons = new Map(); // url -> Image
  let candidates = [];     // {item, x, y} screen-space hotspots from the last draw
  let lastState = null;    // newest state, so icon onload can trigger the viewer's redraw
  let lastProject = null;
  let last = { paths: 0, landings: 0, areas: 0, bounces: 0, unowned: 0, tMs: 0, lastDrawMs: 0, maxDrawMs: 0 };

  function findPlayer(players, id) {
    if (!players) return null;
    if (Array.isArray(players)) return players.find(p => p && p.id === id) || null;
    return players[id] || null;
  }

  // End of life = the latest finite bound among effect.t1, endT and land.t, so a
  // landing that outlives its effect (or vice versa) never vanishes 1–2 frames early.
  function visible(item, tMs) {
    let end = item.t;
    for (const bound of [item.effect && item.effect.t1, item.endT, item.land && item.land.t]) {
      if (typeof bound === 'number' && Number.isFinite(bound)) end = Math.max(end, bound);
    }
    return item.t <= tMs && tMs <= end;
  }

  function iconImage(url) {
    if (!icons.has(url)) {
      const image = new Image();
      image.onload = () => {
        if (image._notified) return;
        image._notified = true;
        const state = lastState;
        if (state && typeof state.redraw === 'function') state.redraw();
      };
      image.src = url;
      icons.set(url, image);
    }
    return icons.get(url);
  }

  function drawIcon(ctx, cx, cy, color, iconFor, item) {
    const url = typeof iconFor === 'function' ? iconFor(item.agent, item.ability) : null;
    if (typeof url === 'string' && url) {
      const image = iconImage(url);
      if (image.complete && image.naturalWidth > 0) {
        ctx.drawImage(image, cx - 8, cy - 8, 16, 16);
        return;
      }
    }
    // While loading or missing: colored diamond placeholder.
    ctx.save();
    ctx.translate(cx, cy);
    ctx.rotate(Math.PI / 4);
    ctx.fillStyle = color;
    ctx.fillRect(-5, -5, 10, 10);
    ctx.restore();
  }

  function draw(ctx, tMs, state) {
    const started = performance.now();
    const items = (state && state.round && Array.isArray(state.round.utility)) ? state.round.utility : [];
    candidates = [];
    last.paths = 0; last.landings = 0; last.areas = 0; last.bounces = 0; last.unowned = 0; last.tMs = tMs;
    const project = state && typeof state.project === 'function' ? state.project : null;
    lastState = state;
    lastProject = project;
    if (!project || !items.length) { finish(started); return; }
    const colors = state.colors || ['#65c9e8', '#ff6874'];
    const rawTeam = String(state.teamFilter || 'all');
    const teamFilter = rawTeam === 'attackers' ? '0' : rawTeam === 'defenders' ? '1' : rawTeam;
    const playerFilter = state.playerFilter || '';
    const iconFor = state.iconFor;
    for (const item of items) {
      // State actors (e.g. caster heals) are evidence only; never drawn, counted or hit-tested.
      if (!item || item.kind === 'state' || !visible(item, tMs)) continue;
      const player = findPlayer(state.players, item.actor);
      const unowned = item.actor == null;
      const color = player && player.team === 1 ? (colors[1] || NEUTRAL) : player && player.team === 0 ? (colors[0] || NEUTRAL) : NEUTRAL;
      if (unowned) last.unowned++;
      // Filters hide attribution targets, never unowned items (no team/actor to match).
      if (teamFilter !== 'all' && !unowned && (!player || String(player.team) !== String(teamFilter))) continue;
      if (playerFilter && !unowned && item.actor !== playerFilter) continue;

      let hot = null; // screen point this item is found at right now

      // Flight path: dashed (or solid for steered walls), only points already flown.
      if (state.showPaths !== false && item.route === 'path' && Array.isArray(item.path) && item.path.length) {
        const pts = [];
        for (const entry of item.path) {
          if (!Array.isArray(entry) || entry.length < 3) continue;
          if (item.t + entry[0] > tMs) break;
          const p = project(entry[1], entry[2]);
          if (p) pts.push(p);
        }
        if (pts.length > 1) {
          ctx.save();
          ctx.strokeStyle = color;
          ctx.lineWidth = 2;
          ctx.globalAlpha = item.kind === 'wall' ? 0.55 : 0.9;
          if (item.kind !== 'wall') ctx.setLineDash([5, 4]);
          ctx.lineJoin = 'round';
          ctx.lineCap = 'round';
          ctx.beginPath();
          ctx.moveTo(pts[0].x, pts[0].y);
          for (let i = 1; i < pts.length; i++) ctx.lineTo(pts[i].x, pts[i].y);
          ctx.stroke();
          ctx.restore();
          last.paths++;
        }
        let head = pts.length ? pts[pts.length - 1] : null;
        if (head && (!item.land || tMs < item.land.t)) {
          // Ability icon rides the path head while in flight; the landing icon
          // takes over at land.t. Diamond fallback while loading/missing.
          drawIcon(ctx, head.x, head.y, color, iconFor, item);
        }
        // Bounces are drawn after the head so they are never covered by it.
        if (Array.isArray(item.bounces)) {
          for (const b of item.bounces) {
            if (!Array.isArray(b) || b.length < 3 || item.t + b[0] > tMs) continue;
            const p = project(b[1], b[2]);
            if (!p) continue;
            ctx.save();
            ctx.lineJoin = 'miter';
            // Dark underlay + light stroke keeps the × readable over any fill.
            for (const pass of [['#0d151c', 5], ['#fff', 2.5]]) {
              ctx.strokeStyle = pass[0];
              ctx.lineWidth = pass[1];
              ctx.beginPath();
              ctx.moveTo(p.x - 4.5, p.y - 4.5); ctx.lineTo(p.x + 4.5, p.y + 4.5);
              ctx.moveTo(p.x + 4.5, p.y - 4.5); ctx.lineTo(p.x - 4.5, p.y + 4.5);
              ctx.stroke();
            }
            ctx.restore();
            last.bounces++;
          }
        }
        if (head) hot = head;
      }

      // Landing icon + effect area (both under the areas toggle).
      if (state.showAreas !== false) {
        const land = item.land;
        const landP = land && Number.isFinite(land.x) && Number.isFinite(land.y) ? project(land.x, land.y) : null;
        const effect = item.effect;
        let areaP = null, areaR = 0, areaAlpha = 0;
        if (effect && Number.isFinite(effect.t0) && Number.isFinite(effect.t1) && tMs >= effect.t0) {
          areaP = Number.isFinite(effect.x) && Number.isFinite(effect.y) ? project(effect.x, effect.y) : null;
          if (areaP && Number.isFinite(effect.r) && effect.r > 0) {
            // r is already world units; scale it to pixels through the projection itself.
            const probe = project(effect.x + 100, effect.y);
            if (probe) {
              const scale = Math.hypot(probe.x - areaP.x, probe.y - areaP.y) / 100;
              areaR = effect.r * scale;
              areaAlpha = Math.max(0, 0.35 * (1 - (tMs - effect.t0) / (effect.t1 - effect.t0)));
            }
          }
        }
        if (areaP && areaR > 0 && areaAlpha > 0) {
          ctx.save();
          ctx.globalAlpha = areaAlpha;
          ctx.fillStyle = color;
          ctx.beginPath(); ctx.arc(areaP.x, areaP.y, areaR, 0, Math.PI * 2); ctx.fill();
          ctx.restore();
          last.areas++;
        }
        if (landP && (!item.land || tMs >= item.land.t)) {
          drawIcon(ctx, landP.x, landP.y, color, iconFor, item);
          last.landings++;
          hot = landP;
        } else if (!hot && areaP) {
          hot = areaP;
        }
      }
      if (hot) candidates.push({ item, x: hot.x, y: hot.y });
    }
    finish(started);
  }

  function finish(started) {
    const ms = performance.now() - started;
    last.lastDrawMs = ms;
    last.maxDrawMs = Math.max(last.maxDrawMs || 0, ms);
  }

  function hitTest(px, py) {
    let best = null;
    let bestD = HIT_RADIUS * HIT_RADIUS;
    for (const c of candidates) {
      const d = (c.x - px) * (c.x - px) + (c.y - py) * (c.y - py);
      if (d <= bestD) { bestD = d; best = { item: c.item, x: c.x, y: c.y }; }
    }
    return best;
  }

  function debugState() {
    return { ...last, paths: last.paths, landings: last.landings, areas: last.areas, bounces: last.bounces, unowned: last.unowned };
  }

  window.UtilityLayer = { draw, hitTest, debugState };
})();
