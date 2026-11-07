// Shared personal coach panel for the viewer and analysis pages.
(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const route = location.pathname.match(/\/replays\/([^/]+)\/(?:view|analysis)\/?$/);
  const query = new URLSearchParams(location.search);
  const id = query.get('id') || (route && decodeURIComponent(route[1])) || (query.get('demo') === '1' ? 'demo' : null);
  if (!id || !$('coach-player')) return;
  const base = `/api/replays/${encodeURIComponent(id)}`;
  const picker = $('coach-player'), button = $('coach-review'), status = $('coach-status'), result = $('coach-result');
  let pending = false;
  const element = (tag, className, text) => {
    const node = document.createElement(tag);
    node.className = className;
    node.textContent = text;
    return node;
  };
  async function json(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw Error(data.error || `HTTP ${response.status}`);
    return data;
  }
  const viewerUrl = (round, t) => {
    const path = location.pathname.startsWith('/bot/web/static/replays_app/')
      ? `${window.replayAssetBase}view.html?id=${encodeURIComponent(id)}`
      : `/replays/${encodeURIComponent(id)}/view`;
    return `${path}#r=${round}&t=${t}`;
  };
  function refs(parent, items) {
    const list = element('div', 'coach-refs', '');
    for (const ref of items || []) {
      if (!Number.isInteger(ref.round) || !Number.isFinite(ref.t) || ref.t < 0) continue;
      const seconds = Number.isFinite(ref.tSec) ? ref.tSec : ref.t / 1000;
      const link = element('a', 'coach-ref', `R${ref.round} · ${seconds.toFixed(1)}s ↗`);
      link.href = viewerUrl(ref.round, seconds);
      list.append(link);
    }
    parent.append(list);
  }
  function render(data) {
    if (data.schema !== 'valoplant_coach_api/v1') throw Error('Invalid coach response');
    result.replaceChildren();
    const narration = data.narration;
    const rules = data.rules?.findings || [];
    result.append(element('h3', '', `${data.player?.agent || data.player?.slot || 'Player'} · ${data.roundsAnalyzed || 0} rounds`));
    result.append(element('p', 'coach-summary', narration?.summary || `Rules-only review: ${rules.length} replay-backed findings. Select any moment to inspect the round.`));
    if (!data.llm?.available) result.append(element('p', 'coach-note', `Local LLM unavailable — showing rules-only findings. ${data.llm?.reason || 'Narration is optional.'}`));
    else result.append(element('p', 'muted', `Narration: ${data.llm.backend} · ${data.llm.model}`));
    const section = (title, items, limit, ruleMode = false) => {
      result.append(element('h4', '', title));
      if (!items.length) result.append(element('p', 'muted', 'No cited items for this review.'));
      for (const item of items.slice(0, limit)) {
        const card = element('article', 'coach-finding', '');
        const head = element('div', 'coach-finding-head', '');
        const severity = ['major', 'minor', 'info'].includes(item.severity) ? item.severity : 'info';
        head.append(element('strong', '', ruleMode ? item.kind?.replaceAll('_', ' ') || 'Finding' : item.title || 'Finding'),
          element('span', `severity severity-${severity}`, severity));
        card.append(head, element('p', '', ruleMode ? item.text || '' : item.detail || ''));
        refs(card, item.refs);
        result.append(card);
      }
    };
    section('Top mistakes · replay rules', rules, 3, true);
    if (narration?.mistakes?.length) section('Coach perspective', narration.mistakes, 3);
    if (narration?.drills?.length) section('Practice drills', narration.drills, 6);
    else result.append(element('p', 'muted', 'Practice drills require local narration; replay findings remain available above.'));
    status.textContent = data.llm?.available ? 'Review ready.' : 'Rules-only review ready.';
  }
  async function load() {
    try {
      const [match, me] = await Promise.all([json(base), json(`${base}/me`)]);
      for (const player of match.players || []) {
        const option = document.createElement('option');
        option.value = player.id;
        option.textContent = `${player.id} · ${player.name || player.agent || 'Unknown'} · Team ${player.team === 0 ? 'A' : player.team === 1 ? 'B' : '?'}`;
        picker.append(option);
      }
      if (me.player && [...picker.options].some(option => option.value === me.player)) picker.value = me.player;
      button.disabled = !picker.value;
      status.textContent = picker.value ? `Selected ${picker.selectedOptions[0].textContent}.` : 'Choose your player to begin.';
      if (picker.value) {
        try {render(await json(`${base}/coach?player=${encodeURIComponent(picker.value)}`));}
        catch (error) {if (!error.message.includes('no cached coach result')) status.textContent = `Saved review unavailable: ${error.message}`;}
      }
    } catch (error) {status.textContent = `Coach unavailable: ${error.message}`;}
  }
  picker.addEventListener('change', async () => {
    const choice = picker.value;
    result.replaceChildren();
    button.disabled = true;
    if (!choice) {status.textContent = 'Choose your player to begin.'; return;}
    status.textContent = 'Saving your player…';
    try {
      const me = await json(`${base}/me`, {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({player: choice})});
      if (picker.value !== choice) return;
      status.textContent = `This is me: ${me.player}. Ready to review.`;
      button.disabled = pending;
    } catch (error) {status.textContent = `Could not save player: ${error.message}`;}
  });
  button.addEventListener('click', async () => {
    if (pending || !picker.value) return;
    pending = true; button.disabled = true; picker.disabled = true;
    result.replaceChildren(); status.textContent = 'Reviewing replay… local narration may take a minute.';
    try {
      render(await json(`${base}/coach`, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({player: picker.value})}));
    } catch (error) {status.textContent = `Review unavailable: ${error.message}`;}
    finally {pending = false; picker.disabled = false; button.disabled = !picker.value;}
  });
  load();
})();
