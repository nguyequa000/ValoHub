(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const cards = $('cards'), empty = $('empty'), status = $('archive-status');
  const drop = $('drop-zone'), picker = $('file'), pick = $('pick');
  const uploadStatus = $('upload-status'), progress = $('progress-track'), bar = $('progress-bar');
  const direct = location.pathname.startsWith('/bot/web/static/replays_app/');
  let stream = null, decoderStream = null, decoderJob = null;
  const demoRoot = new URL('demo/', new URL(window.replayAssetBase, location.href));
  const mediaUrl = value => typeof value === 'string' && /^https:\/\/media\.valorant-api\.com\/[\w/.-]+$/i.test(value) ? value : '';
  const node = (tag, className, text) => {
    const el = document.createElement(tag); if (className) el.className = className;
    if (text !== undefined) el.textContent = String(text); return el;
  };
  async function json(url, options) {
    const response = await fetch(url, options);
    if (!response.ok) throw new Error(response.status === 404 ? '404 — endpoint unavailable' : `HTTP ${response.status}`);
    return response.json();
  }
  function disableImport(reason) {
    drop.classList.add('disabled'); drop.setAttribute('aria-disabled', 'true');
    drop.title = pick.title = reason; pick.disabled = true; $('empty-pick').disabled = true;
    uploadStatus.textContent = reason;
  }
  function card(replay, demo = false) {
    demo = demo || replay.id === 'demo' || replay.source === 'synthetic';
    const item = node('article', 'replay-card');
    const art = node('div', 'card-art');
    const src = mediaUrl(replay.mapImage);
    if (src) { const img = node('img');
      const mapId = src.match(/^https:\/\/media\.valorant-api\.com\/maps\/([\da-f]{8}(?:-[\da-f]{4}){3}-[\da-f]{12})\/displayicon\.png$/i);
      img.src = mapId ? `${window.replayAssetBase}assets/maps/${mapId[1]}.png` : src;
      img.alt = `${replay.mapName || 'Replay'} minimap`;
      img.loading = 'lazy'; img.addEventListener('error', () => {
        if (img.src !== src) { img.src = src; return; }
        img.remove(); art.classList.add('art-unavailable');
      }); art.append(img);
    } else art.classList.add('art-unavailable');
    art.append(node('span', 'card-art-label', demo ? 'DEMO REPLAY' : 'MATCH RECORD'));
    item.append(art);
    const body = node('div', 'card-body'); const meta = node('div', 'card-meta');
    meta.append(node('span', 'pill', `PATCH ${replay.patch || '—'}`));
    const state = String(replay.status || 'processing').toLowerCase();
    const badge = node('span', `status-pill ${['ready', 'timeline', 'processing', 'failed', 'suspect', 'stale'].includes(state) ? state : 'processing'}`, state);
    if (state === 'suspect' || state === 'stale') { badge.title = replay.suspectReason || (state === 'stale' ? 'Parsed with an older decoder; re-import to refresh' : 'Decoder output has suspiciously low sample density'); badge.setAttribute('aria-label', `${state === 'stale' ? 'Stale' : 'Suspect'}: ${badge.title}`); }
    meta.append(badge);
    body.append(meta, node('h2', '', replay.mapName || 'Unknown map'));
    const detail = node('p', 'muted', `${Number(replay.rounds) || 0} rounds · ${replay.durationMs ? Math.round(replay.durationMs / 60000) + ' min' : 'Duration unknown'}`);
    body.append(detail);
    const usable = demo || ['ready', 'timeline', 'suspect', 'stale'].includes(state);
    const actions = node('div', 'card-actions');
    if (usable) {
      const link = node('a', 'card-action', 'OPEN VIEWER →');
      link.href = demo ? `${window.replayAssetBase}view.html?demo=1` : direct
        ? `${window.replayAssetBase}view.html?id=${encodeURIComponent(replay.id)}`
        : `/replays/${encodeURIComponent(replay.id)}/view`;
      actions.append(link);
      const analysis = node('a', 'card-action analysis-action', 'VIEW ANALYSIS ↗');
      analysis.href = direct ? `${window.replayAssetBase}analysis.html?id=${encodeURIComponent(replay.id)}` :
        `/replays/${encodeURIComponent(replay.id)}/analysis`;
      actions.append(analysis);
    } else {
      const why = state === 'failed' ? (replay.error || replay.failureReason || 'Decoder could not read this file. Re-import it to try again.') : 'Decoding in progress…';
      actions.append(node('span', 'card-note', why));
    }
    body.append(actions); item.append(body); return item;
  }
  async function load() {
    status.textContent = 'Loading library…'; cards.replaceChildren(); $('demo-cards').replaceChildren(); $('demo-slot').hidden = true; empty.hidden = true;
    try {
      const result = await json('/api/replays');
      const list = Array.isArray(result.replays) ? result.replays : [];
      $('count').textContent = String(list.length);
      list.forEach(item => cards.append(card(item)));
      empty.hidden = list.length !== 0;
      status.textContent = `${list.length} ${list.length === 1 ? 'REPLAY' : 'REPLAYS'} IN ARCHIVE`;
      if (!list.length) await addDemo();
    } catch (error) {
      status.textContent = `Library API unavailable (${error.message})`;
      $('count').textContent = '—';
      if (error.message.includes('404')) disableImport('Upload unavailable: replay API is not installed.');
      // A demo under repo-root http.server needs no API and does not imply any real replay exists.
      await addDemo();
    }
  }
  async function addDemo() {
    try {
      const match = await json(new URL('match.json', demoRoot));
      $('demo-cards').append(card({id: 'demo', mapName: match.map?.name, mapImage: match.map?.image,
        patch: match.patch, rounds: match.rounds?.length, durationMs: match.durationMs, status: 'ready'}, true));
      $('demo-slot').hidden = false;
      status.textContent += ' · Synthetic demo available below';
    } catch { /* no demo available */ }
  }
  async function upload(file) {
    if (!file || !/\.vrf$/i.test(file.name)) { uploadStatus.textContent = 'Choose a .vrf file.'; return; }
    if (pick.disabled) return;
    if (stream) { stream.close(); stream = null; }
    progress.hidden = false; bar.style.width = '5%';
    uploadStatus.textContent = `Uploading ${file.name}…`;
    pick.disabled = true; drop.classList.add('busy');
    try {
      const job = await json(`/api/replays/upload?name=${encodeURIComponent(file.name)}`,
        {method: 'PUT', headers: {'Content-Type': 'application/octet-stream'}, body: file});
      if (!job.jobId) throw new Error('Upload response is missing jobId');
      uploadStatus.textContent = `Queued ${file.name} · job ${job.jobId}`;
      bar.style.width = '15%';
      if (!window.EventSource) { uploadStatus.textContent += ' · Live progress unavailable; refresh the library.'; return; }
      stream = new EventSource('/api/jobs/stream');
      stream.addEventListener('progress', event => {
        let update; try { update = JSON.parse(event.data); } catch { return; }
        if (update.file && update.file !== file.name) return;
        if (Number.isFinite(Number(update.pct))) bar.style.width = `${Math.max(0, Math.min(100, Number(update.pct)))}%`;
        uploadStatus.textContent = `${String(update.stage || 'processing').toUpperCase()} · ${update.message || file.name}`;
        if (update.stage === 'done' || update.stage === 'error') {
          stream.close(); stream = null; load();
        }
      });
      stream.onerror = () => {
        stream.close(); stream = null;
        uploadStatus.textContent = 'Uploaded. Live progress endpoint unavailable; refresh to check status.';
        uploadStatus.title = 'GET /api/jobs/stream could not be reached';
      };
    } catch (error) {
      uploadStatus.textContent = `Upload unavailable: ${error.message}`;
      if (error.message.includes('404')) disableImport('Upload unavailable: PUT /api/replays/upload is not installed.');
    } finally { drop.classList.remove('busy'); if (!drop.classList.contains('disabled')) pick.disabled = false; picker.value = ''; }
  }
  pick.addEventListener('click', e => { e.stopPropagation(); picker.click(); });
  picker.addEventListener('change', () => upload(picker.files?.[0]));
  $('empty-pick').addEventListener('click', () => { if (!pick.disabled) picker.click(); });
  document.addEventListener('keydown', e => { const d = $('decoder-details'); if (e.key === 'Escape' && d && d.open) { d.open = false; d.querySelector('summary').focus(); } });
  for (const type of ['dragenter', 'dragover']) drop.addEventListener(type, e => { e.preventDefault(); if (!pick.disabled) drop.classList.add('dragging'); });
  for (const type of ['dragleave', 'drop']) drop.addEventListener(type, e => { e.preventDefault(); drop.classList.remove('dragging'); });
  drop.addEventListener('drop', e => { if (!pick.disabled) upload(e.dataTransfer?.files?.[0]); });
  async function watcherStatus() {
    try {
      const result = await json('/api/watcher/status');
      const badge = $('watcher-badge');
      badge.hidden = false;
      badge.textContent = result.enabled ? `WATCHER ON · ${Number(result.watched) || 0} FILES` : 'WATCHER OFF';
      badge.title = result.enabled ? 'Replay folder monitoring is active' : 'Replay folder monitoring is off';
    } catch { $('watcher-badge').hidden = true; }
  }
  async function decoderStatus() {
    try {
      const info = await json('/api/decoder');
      if (info.updaterEnabled === false) { $('decoder-update').hidden = true; const dry = $('decoder-dry-run'); if (dry && dry.parentElement) dry.parentElement.hidden = true; }
      $('decoder-state').textContent = (info.status || 'unavailable').toUpperCase();
      $('decoder-state').className = `status-pill ${info.status === 'ready' ? 'ready' : 'failed'}`;
      $('decoder-commit').textContent = info.parserCommit || 'Unknown';
      $('decoder-overlay').textContent = info.overlayHash || 'Unknown';
      const branches = Array.isArray(info.supported) ? info.supported : [];
      $('decoder-branches').textContent = `${branches.length} supported · ${branches.slice(-3).join(', ') || 'none'}`;
      $('decoder-branches').title = branches.join('\n');
      $('decoder-last-check').textContent = info.lastCheck || 'Never';
      if (!decoderJob && info.lastResult) $('decoder-progress').textContent = `${info.lastResult.status.toUpperCase()} · ${info.lastResult.message || 'check completed'}`;
    } catch (error) {
      $('decoder-state').textContent = 'UNAVAILABLE';
      $('decoder-progress').textContent = `Decoder status unavailable: ${error.message}`;
      $('decoder-update').disabled = true;
    }
  }
  function watchDecoder(jobId) {
    if (decoderStream) decoderStream.close();
    decoderJob = jobId;
    $('decoder-update').disabled = true;
    $('decoder-progress-track').hidden = false;
    if (!window.EventSource) { $('decoder-progress').textContent = 'Live progress unavailable; refresh to check the result.'; return; }
    decoderStream = new EventSource('/api/jobs/stream');
    decoderStream.addEventListener('progress', event => {
      let update; try { update = JSON.parse(event.data); } catch { return; }
      if (update.jobId !== jobId) return;
      $('decoder-progress-bar').style.width = `${Math.max(0, Math.min(100, Number(update.pct) || 0))}%`;
      $('decoder-progress').textContent = `${String(update.stage || 'checking').toUpperCase()} · ${update.message || 'parser'}`;
      if (update.status === 'done' || update.status === 'error') {
        decoderStream.close(); decoderStream = null; decoderJob = null;
        $('decoder-update').disabled = false; decoderStatus();
      }
    });
    decoderStream.onerror = () => { decoderStream.close(); decoderStream = null; $('decoder-progress').textContent = 'Progress stream disconnected; refresh to check result.'; };
  }
  $('decoder-update').addEventListener('click', async () => {
    $('decoder-update').disabled = true;
    $('decoder-progress').textContent = 'Queueing parser update…';
    try {
      const response = await fetch('/api/decoder/update', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({dryRun: $('decoder-dry-run').checked})});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || `HTTP ${response.status}`);
      watchDecoder(result.jobId);
    } catch (error) { $('decoder-progress').textContent = `Update unavailable: ${error.message}`; $('decoder-update').disabled = false; }
  });
  $('refresh').addEventListener('click', () => { load(); watcherStatus(); decoderStatus(); });
  load();
  watcherStatus();
  decoderStatus();
  json('/api/jobs').then(data => {
    const active = data.jobs?.find(job => job.kind === 'decoder-update' && ['queued', 'running'].includes(job.status));
    if (active) watchDecoder(active.id);
  }).catch(() => {});
})();
