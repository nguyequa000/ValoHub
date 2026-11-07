'use strict';
// Sidecar supervisor: spawn, ready-line wait, health check, graceful stop with escalation.
// Pure Node (no electron import) so it is testable with a fake sidecar.
const { spawn, spawnSync } = require('node:child_process');
const http = require('node:http');
const { EventEmitter } = require('node:events');

class SupervisorError extends Error {
  constructor(code, message, extra = {}) { super(message); this.code = code; Object.assign(this, extra); }
}

function minimalEnv(extra = {}) {
  const sysRoot = process.env.SystemRoot || 'C:\\Windows';
  const env = { SystemRoot: sysRoot, PATH: `${sysRoot}\\System32` };
  for (const k of ['TEMP', 'TMP', 'LOCALAPPDATA', 'APPDATA', 'USERPROFILE']) if (process.env[k]) env[k] = process.env[k];
  return Object.assign(env, extra);
}

function request(method, port, path, token, timeoutMs = 3000, body) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      host: '127.0.0.1', port, path, method, timeout: timeoutMs,
      headers: Object.assign({ 'X-ValoHub-Token': token }, body ? { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(body) } : {}),
    }, (res) => {
      const chunks = [];
      res.on('data', (c) => chunks.push(c));
      res.on('end', () => resolve({ status: res.statusCode, body: Buffer.concat(chunks).toString('utf8') }));
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

class Supervisor extends EventEmitter {
  /**
   * @param {{command:string,args:string[],token:string,env?:object,readyTimeoutMs?:number,log?:object,cwd?:string}} o
   */
  constructor(o) {
    super();
    this.o = Object.assign({ readyTimeoutMs: 20000, env: {}, args: [] }, o);
    this.child = null; this.port = null; this.stderrTail = []; this.exited = null; this.stopping = false;
  }

  _log(m) { if (this.o.log) this.o.log.info(m); }

  start() {
    const o = this.o;
    return new Promise((resolve, reject) => {
      let settled = false;
      const fail = (err) => { if (!settled) { settled = true; clearTimeout(timer); this._hardKillSync(); reject(err); } };
      let child;
      try {
        child = spawn(o.command, o.args, {
          env: minimalEnv(Object.assign({ VRB_TOKEN: o.token }, o.env)),
          stdio: ['pipe', 'pipe', 'pipe'], windowsHide: true, shell: false, detached: false, cwd: o.cwd,
        });
      } catch (e) { return reject(new SupervisorError('SPAWN_FAILED', `cannot spawn ${o.command}: ${e.message}`)); }
      this.child = child;
      this._log(`spawned pid=${child.pid} cmd=${o.command} args=${JSON.stringify(o.args)}`);
      const timer = setTimeout(() => fail(new SupervisorError('READY_TIMEOUT', `sidecar did not print a ready line within ${o.readyTimeoutMs} ms`, { stderrTail: this.tail() })), o.readyTimeoutMs);
      child.on('error', (e) => fail(new SupervisorError('SPAWN_FAILED', `cannot start sidecar: ${e.message}`)));
      child.stdin.on('error', () => {});
      let buf = ''; let gotFirst = false;
      child.stdout.on('data', (d) => {
        if (gotFirst) { this._log(`stdout: ${String(d).trimEnd()}`); return; }
        buf += d.toString('utf8');
        const nl = buf.indexOf('\n');
        if (nl < 0) return;
        gotFirst = true;
        const line = buf.slice(0, nl).trim();
        let msg = null;
        try { msg = JSON.parse(line); } catch { /* handled below */ }
        if (!msg || msg.event !== 'ready' || !Number.isInteger(msg.port) || msg.port < 1 || msg.port > 65535) {
          return fail(new SupervisorError('BAD_READY', `unexpected first output line from sidecar: ${line.slice(0, 200)}`, { stderrTail: this.tail() }));
        }
        this.port = msg.port; this.sidecarPid = msg.pid;
        this.health(o.readyTimeoutMs).then(() => {
          if (settled) return;
          settled = true; clearTimeout(timer);
          resolve({ port: this.port, pid: child.pid });
        }, (e) => fail(new SupervisorError('HEALTH_FAILED', `health check failed: ${e.message}`, { stderrTail: this.tail() })));
      });
      child.stderr.on('data', (d) => {
        for (const l of String(d).split(/\r?\n/)) if (l) { this.stderrTail.push(l); if (this.stderrTail.length > 40) this.stderrTail.shift(); }
        this._log(`stderr: ${String(d).trimEnd()}`);
      });
      child.on('exit', (code, signal) => {
        this.exited = { code, signal };
        this._log(`sidecar exited code=${code} signal=${signal}`);
        if (!settled) return fail(new SupervisorError('EARLY_EXIT', `sidecar exited before becoming ready (code ${code})`, { exitCode: code, stderrTail: this.tail() }));
        if (!this.stopping) this.emit('crashed', { code, signal, stderrTail: this.tail() });
        this.emit('exit', { code, signal });
      });
    });
  }

  tail() { return this.stderrTail.slice(-15).join('\n'); }

  async health(totalMs = 5000) {
    const end = Date.now() + totalMs; let last;
    while (Date.now() < end) {
      try {
        const r = await request('GET', this.port, '/api/app/health', this.o.token, 2000);
        if (r.status === 200) return r;
        last = new Error(`HTTP ${r.status}`);
      } catch (e) { last = e; }
      await new Promise((r) => setTimeout(r, 100));
    }
    throw last || new Error('health timeout');
  }

  _alive() { return this.child && this.exited === null; }

  _hardKillSync() {
    if (!this.child || !this.child.pid || this.exited) return;
    try { spawnSync('taskkill', ['/pid', String(this.child.pid), '/t', '/f'], { windowsHide: true, timeout: 5000 }); } catch { /* ignore */ }
    try { this.child.kill(); } catch { /* ignore */ }
  }

  _waitExit(ms) {
    return new Promise((resolve) => {
      if (!this._alive()) return resolve(true);
      const t = setTimeout(() => { this.removeListener('exit', onExit); resolve(false); }, ms);
      const onExit = () => { clearTimeout(t); resolve(true); };
      this.once('exit', onExit);
    });
  }

  /** Graceful stop -> taskkill /t /f -> child.kill(). Always resolves; returns how it ended. */
  async stop(graceMs = 5000) {
    if (!this.child) return 'not-started';
    this.stopping = true;
    if (!this._alive()) return 'already-exited';
    if (this.port) { try { await request('POST', this.port, '/api/app/shutdown', this.o.token, 1500, '{}'); } catch { /* fallthrough to stdin */ } }
    try { this.child.stdin.end(); } catch { /* ignore */ }
    if (await this._waitExit(graceMs)) return 'graceful';
    this._log('graceful shutdown timed out; escalating to taskkill /t /f');
    try { spawnSync('taskkill', ['/pid', String(this.child.pid), '/t', '/f'], { windowsHide: true, timeout: 5000 }); } catch { /* ignore */ }
    if (await this._waitExit(2000)) return 'taskkill';
    try { this.child.kill(); } catch { /* ignore */ }
    await this._waitExit(1000);
    return 'killed';
  }
}

module.exports = { Supervisor, SupervisorError, minimalEnv, request };
