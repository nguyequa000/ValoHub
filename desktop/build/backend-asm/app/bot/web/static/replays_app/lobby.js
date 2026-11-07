(() => {
  'use strict';
  const $ = id => document.getElementById(id);
  const params = new URLSearchParams(location.search);
  const invite = params.get('lobby');
  const validCode = code => /^[A-Z2-9]{8}$/.test(code);
  const replayId = params.get('demo') === '1' ? 'demo' : params.get('id') ||
    decodeURIComponent(location.pathname.match(/\/replays\/([^/]+)\/view\/?$/)?.[1] || '');
  let ws = null, code = null, token = null, host = false, lastSent = 0, timer = null, retryHref = null;
  const notice = text => { $('lobby-status').textContent = text; };
  function retryLink(label = 'rejoin') {
    const link = document.createElement('a');
    link.href = retryHref;
    link.textContent = label;
    $('lobby-status').append(link, ' to retry.');
  }
  const show = () => { $('lobby-panel').hidden = false; };
  const endpoint = () => `/api/lobby/${encodeURIComponent(code)}`;
  async function json(url, options) {
    const response = await fetch(url, options);
    let result = {};
    try { result = await response.json(); } catch { /* informative HTTP status below */ }
    if (!response.ok) throw new Error(response.status === 404 ? 'Lobby unavailable (enable VALO_LOBBY=1 on the server).' : result.error || `HTTP ${response.status}`);
    return result;
  }
  function members(list) {
    $('lobby-members').textContent = list?.length ? list.map(item => `${item.name}${item.host ? ' (host)' : ''}${item.connected ? ' ●' : ''}`).join(' · ') : '';
  }
  function sendState() {
    if (!host || ws?.readyState !== WebSocket.OPEN) return;
    const state = window.replayPlayback?.get();
    if (state) { ws.send(JSON.stringify({type: 'state', ...state})); lastSent = Date.now(); }
  }
  function scheduleState() {
    if (!host || ws?.readyState !== WebSocket.OPEN) return;
    if (Date.now() - lastSent >= 400) sendState();
    else if (!timer) timer = setTimeout(() => { timer = null; sendState(); }, 400);
  }
  document.addEventListener('replay:state', scheduleState);
  function connect() {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    ws = new WebSocket(`${scheme}//${location.host}/ws/lobby/${code}?token=${encodeURIComponent(token)}`);
    ws.onopen = () => { notice(host ? `Hosting ${code}. Share the guest link.` : `Following host in ${code}.`); sendState(); };
    ws.onmessage = async event => {
      let message; try { message = JSON.parse(event.data); } catch { return; }
      if (message.type === 'state' && !host) await window.replayPlayback?.apply(message);
      if (message.type === 'presence') members(message.members);
      if (message.type === 'closed') { notice('Host closed the lobby.'); reset(false); }
      if (message.type === 'error') notice(message.error);
    };
    ws.onclose = () => {
      if (!token) return;
      if (host) notice('Disconnected from lobby. Leave and host a new lobby to retry.');
      else { notice('Disconnected from lobby. Leave and '); retryLink(); }
    };
  }
  function active() {
    const rejoin = new URL(location.href);
    rejoin.searchParams.set('lobby', code);
    retryHref = host ? null : rejoin.href;
    $('lobby-create').hidden = true; $('lobby-join').hidden = true; $('lobby-leave').hidden = false;
    $('lobby-following').hidden = host;
    $('lobby-open').textContent = host ? `HOSTING ${code}` : `LOBBY ${code}`;
    connect();
  }
  function reset(clearLink = true) {
    const old = ws;
    ws = null; token = null; host = false; code = null;
    if (timer) clearTimeout(timer);
    timer = null; if (old) old.close();
    $('lobby-following').hidden = true; $('lobby-open').textContent = 'Watch together';
    $('lobby-leave').hidden = true; $('lobby-create').hidden = false;
    $('lobby-join').hidden = true;
    $('lobby-invite').hidden = true; members([]);
    if (clearLink) { params.delete('lobby'); history.replaceState(null, '', `${location.pathname}?${params}${location.hash}`); }
  }
  $('lobby-open').addEventListener('click', () => { show(); $('lobby-name').focus(); });
  $('lobby-dismiss').addEventListener('click', () => { $('lobby-panel').hidden = true; });
  $('lobby-create').addEventListener('click', async () => {
    if (!window.replayPlayback?.get()) { notice('Wait for the replay to load before hosting.'); return; }
    try {
      const data = await json('/api/lobby', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: $('lobby-name').value, replayId})});
      code = data.code; token = data.token; host = true;
      $('lobby-link').value = data.urls.guest;
      $('lobby-invite').hidden = false;
      active();
    } catch (error) { notice(error.message); }
  });
  $('lobby-join').addEventListener('click', async () => {
    const requested = (params.get('lobby') || '').toUpperCase();
    if (!validCode(requested) || !window.replayPlayback?.get()) { notice('Open a valid lobby link after the replay loads.'); return; }
    try {
      const state = await json(`/api/lobby/${requested}`);
      if (state.replayId !== replayId) throw new Error('This lobby belongs to another replay.');
      const data = await json(`/api/lobby/${requested}/join`, {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: $('lobby-name').value})});
      code = data.code; token = data.token; host = false; members(state.members); active();
    } catch (error) { notice(error.message); }
  });
  $('lobby-leave').addEventListener('click', async () => {
    try { await json(`${endpoint()}/leave`, {method: 'POST', headers: {'Authorization': `Bearer ${token}`}}); }
    catch (error) { notice(`Could not leave server: ${error.message}`); return; }
    const disconnected = ws?.readyState === WebSocket.CLOSED;
    reset(); notice('Left the lobby. Playback is local again.');
    if (disconnected && retryHref) { $('lobby-status').append(' '); retryLink('Rejoin'); }
  });
  $('lobby-copy').addEventListener('click', async () => {
    try { await navigator.clipboard.writeText($('lobby-link').value); notice('Guest link copied.'); }
    catch { $('lobby-link').focus(); $('lobby-link').select(); notice('Copy the selected guest link (Ctrl+C).'); }
  });
  if (validCode((invite || '').toUpperCase())) {
    $('lobby-join').hidden = false; $('lobby-create').hidden = true; show();
    $('lobby-heading').textContent = `Join lobby ${invite.toUpperCase()}`;
    notice('Enter your name, then choose Join lobby to opt in.');
  }
})();
