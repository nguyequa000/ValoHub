// Dependency-free CDP client (Node >= 22, global WebSocket + fetch).
export async function listTargets(port, host = '127.0.0.1') {
  const r = await fetch(`http://${host}:${port}/json`);
  if (!r.ok) throw new Error(`/json HTTP ${r.status}`);
  return r.json();
}

export async function waitForTargets(port, { timeoutMs = 15000, type = 'page' } = {}) {
  const end = Date.now() + timeoutMs;
  let last;
  while (Date.now() < end) {
    try {
      const t = (await listTargets(port)).filter((x) => !type || x.type === type);
      if (t.length) return t;
    } catch (e) { last = e; }
    await new Promise((r) => setTimeout(r, 250));
  }
  throw new Error(`no CDP target on port ${port}: ${last ? last.message : 'empty list'}`);
}

export class CdpClient {
  constructor(ws) {
    this.ws = ws; this.id = 0; this.pending = new Map(); this.handlers = new Map();
    ws.addEventListener('message', (ev) => {
      const m = JSON.parse(typeof ev.data === 'string' ? ev.data : String(ev.data));
      if (m.id !== undefined) {
        const p = this.pending.get(m.id);
        if (!p) return;
        this.pending.delete(m.id); clearTimeout(p.timer);
        m.error ? p.reject(new Error(`${p.method}: ${m.error.message}`)) : p.resolve(m.result);
      } else if (m.method) {
        for (const h of this.handlers.get(m.method) || []) h(m.params, m);
        for (const h of this.handlers.get('*') || []) h(m.params, m);
      }
    });
    ws.addEventListener('close', () => {
      for (const p of this.pending.values()) { clearTimeout(p.timer); p.reject(new Error('socket closed')); }
      this.pending.clear();
    });
  }
  static async connect(wsUrl, timeoutMs = 10000) {
    const ws = new WebSocket(wsUrl);
    await new Promise((res, rej) => {
      const t = setTimeout(() => rej(new Error('ws connect timeout')), timeoutMs);
      ws.addEventListener('open', () => { clearTimeout(t); res(); });
      ws.addEventListener('error', () => { clearTimeout(t); rej(new Error('ws connect error')); });
    });
    return new CdpClient(ws);
  }
  static async attach(port, { match } = {}) {
    const targets = await waitForTargets(port);
    const t = match ? targets.find(match) : targets[0];
    if (!t) throw new Error('no matching target');
    return CdpClient.connect(t.webSocketDebuggerUrl);
  }
  send(method, params = {}, timeoutMs = 15000) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`${method}: timeout ${timeoutMs}ms`)); }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer, method });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(method, fn) {
    if (!this.handlers.has(method)) this.handlers.set(method, []);
    this.handlers.get(method).push(fn);
    return () => this.handlers.set(method, this.handlers.get(method).filter((h) => h !== fn));
  }
  once(method, pred = () => true, timeoutMs = 15000) {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { off(); reject(new Error(`event ${method}: timeout`)); }, timeoutMs);
      const off = this.on(method, (p) => { if (pred(p)) { clearTimeout(timer); off(); resolve(p); } });
    });
  }
  async evaluate(expression, timeoutMs = 15000) {
    const r = await this.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true }, timeoutMs);
    if (r.exceptionDetails) throw new Error('evaluate: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
    return r.result.value;
  }
  close() { try { this.ws.close(); } catch {} }
}
