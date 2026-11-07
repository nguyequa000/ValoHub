// In-page probe for tools/desktop_qa/ui_gate.mjs (dependency-free, no build step).
//
// `probeFn` is serialised with Function.prototype.toString() and evaluated inside the page, so it must stay
// self-contained: no imports, no references to module scope. The pure helpers below (luminance/contrast/
// parseColor/hitAreaClass) are exported separately so `node --test ui_gate_selftest.mjs` can unit-test the
// WCAG maths without a browser.

export const INTERACTIVE_SELECTOR = [
  'a[href]', 'button', 'input:not([type="hidden"])', 'select', 'textarea', 'summary',
  '[role="button"]', '[role="link"]', '[role="tab"]', '[role="checkbox"]', '[role="switch"]',
  '[role="slider"]', '[role="menuitem"]', '[role="option"]', '[role="menuitemcheckbox"]',
  '[contenteditable="true"]', '[tabindex]:not([tabindex="-1"])',
].join(',');

/** WCAG 2.x relative luminance of an sRGB triple (0-255). */
export function luminance([r, g, b]) {
  const f = (v) => {
    const c = v / 255;
    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

/** WCAG contrast ratio between two 0-255 sRGB triples. */
export function contrastRatio(fg, bg) {
  const a = luminance(fg) + 0.05;
  const b = luminance(bg) + 0.05;
  return a > b ? a / b : b / a;
}

/** Parse a getComputedStyle colour (`rgb()`/`rgba()`, comma or space syntax) into {r,g,b,a}. */
export function parseColor(value) {
  const m = /rgba?\(([^)]+)\)/.exec(String(value || ''));
  if (!m) return null;
  const parts = m[1].split(/[,/\s]+/).filter(Boolean);
  if (parts.length < 3) return null;
  const num = (s) => (s.endsWith('%') ? (parseFloat(s) / 100) * 255 : parseFloat(s));
  const alpha = parts.length > 3 ? (parts[3].endsWith('%') ? parseFloat(parts[3]) / 100 : parseFloat(parts[3])) : 1;
  const out = { r: num(parts[0]), g: num(parts[1]), b: num(parts[2]), a: Number.isFinite(alpha) ? alpha : 1 };
  if (![out.r, out.g, out.b, out.a].every(Number.isFinite)) return null;
  return out;
}

/**
 * Hit-area policy from DESIGN.md section 5 (>= 24x24) with the task's hard floor of 16x16.
 * 'ok' >= 24x24, 'warn' between 16 and 24 on either axis, 'hard' below 16 on either axis.
 */
export function hitAreaClass(w, h) {
  if (!Number.isFinite(w) || !Number.isFinite(h) || w <= 0 || h <= 0) return 'hidden';
  if (w >= 24 && h >= 24) return 'ok';
  if (w < 16 || h < 16) return 'hard';
  return 'warn';
}

/** Required contrast threshold for a computed font (WCAG "large text"). */
export function contrastThreshold(fontSizePx, fontWeight) {
  const px = Number(fontSizePx) || 0;
  const w = Number(fontWeight) || 400;
  return px >= 24 || (px >= 18.66 && w >= 700) ? 3 : 4.5;
}

/* eslint-disable-next-line no-unused-vars -- serialised into the page, not called here */
export const probeFn = function probe(cfg) {
  const TOL = 0.75;
  const MAX = cfg.maxList || 60;
  const SEL = cfg.interactiveSelector;
  const out = {
    viewport: {},
    h1: { count: 0, items: [] },
    cards: { count: 0, first: null },
    transport: [],
    interactive: { total: 0, visible: 0, noName: [], small: [], smallHard: [], clipped: [], nested: [], ariaHidden: [] },
    contrast: { checked: 0, failures: [], unverified: [] },
    notes: [],
  };

  const rectOf = (el) => {
    const r = el.getBoundingClientRect();
    return { x: +r.x.toFixed(1), y: +r.y.toFixed(1), w: +r.width.toFixed(1), h: +r.height.toFixed(1), top: +r.top.toFixed(1), bottom: +r.bottom.toFixed(1), left: +r.left.toFixed(1), right: +r.right.toFixed(1) };
  };

  // Effective pointer target: a labelable control wrapped by / associated with a <label> is clickable through
  // the whole label, so the target box is the union (WCAG 2.5.8 measures what the user can hit).
  const hitRect = (el) => {
    const r = el.getBoundingClientRect();
    let left = r.left, top = r.top, right = r.right, bottom = r.bottom;
    let viaLabel = false;
    if (el.labels && el.labels.length && /^(INPUT|SELECT|TEXTAREA)$/.test(el.tagName)) {
      for (const label of el.labels) {
        const lr = label.getBoundingClientRect();
        if (lr.width < 1 || lr.height < 1) continue;
        viaLabel = true;
        left = Math.min(left, lr.left); top = Math.min(top, lr.top);
        right = Math.max(right, lr.right); bottom = Math.max(bottom, lr.bottom);
      }
    }
    return {
      x: +left.toFixed(1), y: +top.toFixed(1), w: +(right - left).toFixed(1), h: +(bottom - top).toFixed(1),
      top: +top.toFixed(1), bottom: +bottom.toFixed(1), left: +left.toFixed(1), right: +right.toFixed(1), viaLabel,
    };
  };

  // Human-readable selector: #id when present, else up to 3 ancestor steps ending at an id.
  const desc = (el) => {
    if (!el || el.nodeType !== 1) return '';
    const parts = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 4) {
      if (node.id) { parts.unshift('#' + node.id); break; }
      let part = node.tagName.toLowerCase();
      const cls = (node.getAttribute('class') || '').trim().split(/\s+/).filter(Boolean).slice(0, 3);
      if (cls.length) part += '.' + cls.join('.');
      const parent = node.parentElement;
      if (parent) {
        const same = Array.from(parent.children).filter((c) => c.tagName === node.tagName);
        if (same.length > 1) part += ':nth-of-type(' + (same.indexOf(node) + 1) + ')';
      }
      parts.unshift(part);
      node = node.parentElement;
      depth += 1;
      if (parts.length >= 3 && node && !node.id) break;
    }
    if (node && node.id && parts[0] !== '#' + node.id) parts.unshift('#' + node.id);
    return parts.join(' > ');
  };

  const styleVisible = (el) => {
    try {
      if (typeof el.checkVisibility === 'function') {
        return el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true });
      }
    } catch (_) { /* fall through */ }
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return false;
    if (Number(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 && r.height > 0;
  };
  const inViewport = (r) => r.right > 0 && r.bottom > 0 && r.left < innerWidth && r.top < innerHeight;

  // ---- viewport / document geometry -------------------------------------------------------------
  const doc = document.documentElement;
  out.viewport = {
    innerWidth: innerWidth,
    innerHeight: innerHeight,
    dpr: devicePixelRatio,
    docScrollWidth: doc.scrollWidth,
    bodyScrollWidth: document.body ? document.body.scrollWidth : 0,
    docScrollHeight: doc.scrollHeight,
    bodyPaddingLeft: document.body ? getComputedStyle(document.body).paddingLeft : null,
    bodyMarginLeft: document.body ? getComputedStyle(document.body).marginLeft : null,
    fontsStatus: document.fonts ? document.fonts.status : 'unknown',
  };

  // ---- (e) headings -----------------------------------------------------------------------------
  const h1s = Array.from(document.querySelectorAll('h1'));
  out.h1 = {
    count: h1s.length,
    items: h1s.map((h) => ({ text: (h.innerText || h.textContent || '').trim().slice(0, 120), selector: desc(h), visible: styleVisible(h) })),
  };
  if (cfg.page === 'viewer' && out.h1.count === 0) out.notes.push('viewer has no h1 (DESIGN 4.2 requires a visible or visually-hidden match heading)');

  // ---- (b) library first card above the fold ----------------------------------------------------
  if (cfg.page === 'library') {
    const cards = Array.from(document.querySelectorAll('.replay-card'));
    out.cards.count = cards.length;
    if (cards.length) {
      const r = rectOf(cards[0]);
      out.cards.first = {
        selector: desc(cards[0]),
        ...r,
        fullyAboveFold: r.top >= -TOL && r.bottom <= innerHeight + TOL,
        intersectsFold: r.top < innerHeight && r.bottom > 0,
      };
    }
  }

  // ---- (c) transport / timeline controls containment --------------------------------------------
  for (const sel of cfg.transportSelectors || []) {
    const els = Array.from(document.querySelectorAll(sel));
    if (!els.length) { out.transport.push({ selector: sel, found: false, insideViewport: false, rect: null }); continue; }
    for (const el of els.slice(0, 8)) {
      if (!styleVisible(el)) { out.transport.push({ selector: sel, found: true, visible: false, insideViewport: null, rect: null, instance: desc(el) }); continue; }
      const r = rectOf(el);
      const overflow = [];
      if (r.left < -TOL) overflow.push('left:' + (-r.left).toFixed(1));
      if (r.right > innerWidth + TOL) overflow.push('right:' + (r.right - innerWidth).toFixed(1));
      if (r.top < -TOL) overflow.push('top:' + (-r.top).toFixed(1));
      if (r.bottom > innerHeight + TOL) overflow.push('bottom:' + (r.bottom - innerHeight).toFixed(1));
      out.transport.push({ selector: sel, found: true, visible: true, insideViewport: overflow.length === 0, overflow, rect: r, instance: desc(el) });
    }
  }

  // ---- (a)(f)(g) interactive inventory -----------------------------------------------------------
  const interactive = Array.from(document.querySelectorAll(SEL));
  out.interactive.total = interactive.length;

  const accName = (el) => {
    const al = (el.getAttribute('aria-label') || '').trim();
    if (al) return al;
    const lb = (el.getAttribute('aria-labelledby') || '').trim();
    if (lb) {
      const text = lb.split(/\s+/).map((id) => (document.getElementById(id)?.innerText || '')).join(' ').trim();
      if (text) return text;
    }
    if (el.labels && el.labels.length) {
      const text = Array.from(el.labels).map((l) => l.innerText || l.textContent || '').join(' ').trim();
      if (text) return text;
    }
    const tag = el.tagName;
    if (tag === 'IMG') { const alt = (el.getAttribute('alt') || '').trim(); if (alt) return alt; }
    if (tag === 'INPUT') {
      const type = (el.getAttribute('type') || 'text').toLowerCase();
      if (type === 'submit' || type === 'button' || type === 'reset') {
        const v = (el.value || '').trim();
        if (v) return v;
      }
      const ph = (el.getAttribute('placeholder') || '').trim();
      if (ph) return ph;
    }
    const title = (el.getAttribute('title') || '').trim();
    if (title) return title;
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (text) return text.slice(0, 120);
    return '';
  };

  const ariaHiddenAncestor = (el) => {
    let node = el;
    while (node && node.nodeType === 1) {
      const v = node.getAttribute && node.getAttribute('aria-hidden');
      if (v === 'true') return node;
      node = node.parentElement;
    }
    return null;
  };

  const nestedAncestor = (el) => {
    let node = el.parentElement;
    while (node) {
      if (node.matches && node.matches(SEL)) return node;
      node = node.parentElement;
    }
    return null;
  };

  for (const el of interactive) {
    if (!styleVisible(el)) continue;
    out.interactive.visible += 1;
    const r = rectOf(el);
    const name = accName(el);

    const hidden = ariaHiddenAncestor(el);
    if (hidden && out.interactive.ariaHidden.length < MAX) {
      out.interactive.ariaHidden.push({ selector: desc(el), role: el.tagName.toLowerCase(), hiddenBy: desc(hidden), name: name.slice(0, 60) });
    }

    const nested = nestedAncestor(el);
    if (nested && out.interactive.nested.length < MAX) {
      out.interactive.nested.push({ selector: desc(el), inside: desc(nested), name: name.slice(0, 60) });
    }

    // Only controls that actually paint inside the current viewport are judged for name/size.
    if (!inViewport(r)) continue;

    if (!name && out.interactive.noName.length < MAX) out.interactive.noName.push({ selector: desc(el), rect: r });

    let cls = 'ok';
    const targetBox = hitRect(el);
    if (targetBox.w >= 24 && targetBox.h >= 24) cls = 'ok';
    else if (targetBox.w < 16 || targetBox.h < 16) cls = 'hard';
    else cls = 'warn';
    if (cls !== 'ok') {
      const entry = { selector: desc(el), w: targetBox.w, h: targetBox.h, viaLabel: targetBox.viaLabel, name: name.slice(0, 60) };
      const cs2 = getComputedStyle(el);
      const inlineTextLink = el.tagName === 'A' && el.hasAttribute('href') && /^inline/.test(cs2.display)
        && !el.getAttribute('role');
      if (inlineTextLink) {
        // WCAG 2.5.8 exempts targets inside a sentence/block of text; report them, never hard-fail on them.
        entry.note = 'inline text link (WCAG 2.5.8 target-size exemption)';
        if (out.interactive.small.length < MAX) out.interactive.small.push(entry);
      } else if (cls === 'hard') {
        if (out.interactive.smallHard.length < MAX) out.interactive.smallHard.push(entry);
      } else if (out.interactive.small.length < MAX) out.interactive.small.push(entry);
    }

    // (a) clipped by an overflow:hidden/clip ancestor
    let node = el.parentElement;
    while (node && node !== doc) {
      const cs = getComputedStyle(node);
      if (/auto|scroll/.test(cs.overflowX + cs.overflowY)) break; // reachable by scrolling, not clipped
      const clipX = /hidden|clip/.test(cs.overflowX);
      const clipY = /hidden|clip/.test(cs.overflowY);
      if (clipX || clipY) {
        const pr = node.getBoundingClientRect();
        const cut = [];
        if (clipY && r.bottom > pr.bottom + TOL) cut.push('bottom:' + (r.bottom - pr.bottom).toFixed(1));
        if (clipY && r.top < pr.top - TOL) cut.push('top:' + (pr.top - r.top).toFixed(1));
        if (clipX && r.right > pr.right + TOL) cut.push('right:' + (r.right - pr.right).toFixed(1));
        if (clipX && r.left < pr.left - TOL) cut.push('left:' + (pr.left - r.left).toFixed(1));
        if (cut.length && out.interactive.clipped.length < MAX) {
          out.interactive.clipped.push({ selector: desc(el), by: desc(node), overflow: getComputedStyle(node).overflow, cut });
        }
      }
      node = node.parentElement;
    }
  }

  // ---- (h) text contrast -------------------------------------------------------------------------
  const parse = (value) => {
    const m = /rgba?\(([^)]+)\)/.exec(String(value || ''));
    if (!m) return null;
    const parts = m[1].split(/[,/\s]+/).filter(Boolean);
    if (parts.length < 3) return null;
    const num = (s) => (s.endsWith('%') ? (parseFloat(s) / 100) * 255 : parseFloat(s));
    const a = parts.length > 3 ? (parts[3].endsWith('%') ? parseFloat(parts[3]) / 100 : parseFloat(parts[3])) : 1;
    const c = { r: num(parts[0]), g: num(parts[1]), b: num(parts[2]), a: Number.isFinite(a) ? a : 1 };
    return [c.r, c.g, c.b, c.a].every(Number.isFinite) ? c : null;
  };
  const lum = ([r, g, b]) => {
    const f = (v) => { const c = v / 255; return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
  };
  const ratio = (fg, bg) => {
    const a = lum(fg) + 0.05;
    const b = lum(bg) + 0.05;
    return a > b ? a / b : b / a;
  };
  const over = (fg, bg) => [fg[0] * fg[3] + bg[0] * (1 - fg[3]), fg[1] * fg[3] + bg[1] * (1 - fg[3]), fg[2] * fg[3] + bg[2] * (1 - fg[3])];

  // Resolve the painted background behind `el`: composite translucent layers, stop at the first
  // opaque colour. A background-image (gradient/photo) is recorded as unverifiable, never guessed.
  const background = (el) => {
    const layers = [];
    let node = el;
    while (node && node.nodeType === 1) {
      const cs = getComputedStyle(node);
      const img = cs.backgroundImage;
      if (img && img !== 'none') {
        return { unverified: 'background-image', element: desc(node), image: img.slice(0, 90) };
      }
      const c = parse(cs.backgroundColor);
      if (c && c.a > 0) {
        if (c.a >= 1) {
          // layers are nearest-first; paint farthest-first, then the nearer ones on top
          let acc = [c.r, c.g, c.b];
          for (let i = layers.length - 1; i >= 0; i -= 1) acc = over(layers[i], acc);
          return { color: acc, from: desc(node) };
        }
        layers.push([c.r, c.g, c.b, c.a]);
      }
      node = node.parentElement;
    }
    let acc = [255, 255, 255];
    for (const layer of layers) acc = over(layer, acc);
    return { color: acc, from: 'canvas' };
  };

  const hasDirectText = (el) => Array.from(el.childNodes).some((n) => n.nodeType === 3 && n.textContent.trim().length > 0);
  const textEls = Array.from(document.body.querySelectorAll('*')).filter((el) => hasDirectText(el));
  for (const el of textEls) {
    if (!styleVisible(el)) continue;
    if (ariaHiddenAncestor(el)) continue;
    const cs = getComputedStyle(el);
    if (cs.webkitTextFillColor && /transparent/.test(cs.webkitTextFillColor)) {
      if (out.contrast.unverified.length < MAX) out.contrast.unverified.push({ selector: desc(el), reason: 'text-fill transparent (gradient text)' });
      continue;
    }
    const fg = parse(cs.color);
    if (!fg) continue;
    if ((parseFloat(cs.fontSize) || 0) < 1) continue; // font-size:0 glyph containers paint no readable copy
    const text = (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim();
    if (!text) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || !inViewport(r)) continue;

    const bg = background(el);
    if (bg.unverified) {
      if (out.contrast.unverified.length < MAX) out.contrast.unverified.push({ selector: desc(el), reason: bg.unverified, image: bg.image, text: text.slice(0, 50) });
      continue;
    }
    const fgRgb = fg.a >= 1 ? [fg.r, fg.g, fg.b] : over([fg.r, fg.g, fg.b, fg.a], bg.color);
    const px = parseFloat(cs.fontSize) || 0;
    const weight = Number(cs.fontWeight) || 400;
    const large = px >= 24 || (px >= 18.66 && weight >= 700);
    const need = large ? 3 : 4.5;
    const value = ratio(fgRgb, bg.color);
    out.contrast.checked += 1;
    if (value + 0.01 < need && out.contrast.failures.length < MAX) {
      out.contrast.failures.push({
        selector: desc(el),
        text: text.slice(0, 70),
        ratio: +value.toFixed(2),
        need,
        fontSize: px,
        fontWeight: weight,
        color: cs.color,
        background: 'rgb(' + bg.color.map((v) => Math.round(v)).join(', ') + ')',
        backgroundFrom: bg.from,
        opacity: +Number(cs.opacity).toFixed(2),
      });
    }
  }

  return out;
};
