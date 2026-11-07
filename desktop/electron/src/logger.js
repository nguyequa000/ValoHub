'use strict';
// Small rotating file logger (1 MB x 5). Never logs the token.
const fs = require('node:fs');
const path = require('node:path');

class RotatingLog {
  constructor(file, maxBytes = 1024 * 1024, keep = 5, secrets = []) {
    this.file = file; this.maxBytes = maxBytes; this.keep = keep; this.secrets = secrets;
    fs.mkdirSync(path.dirname(file), { recursive: true });
  }
  addSecret(s) { if (s) this.secrets.push(s); }
  _scrub(text) {
    let t = String(text);
    for (const s of this.secrets) if (s) t = t.split(s).join('<redacted>');
    return t;
  }
  _rotate() {
    try {
      if (!fs.existsSync(this.file) || fs.statSync(this.file).size < this.maxBytes) return;
      for (let i = this.keep - 1; i >= 1; i--) {
        const a = `${this.file}.${i}`; const b = `${this.file}.${i + 1}`;
        if (fs.existsSync(a)) { try { fs.renameSync(a, b); } catch { /* ignore */ } }
      }
      fs.renameSync(this.file, `${this.file}.1`);
      const over = `${this.file}.${this.keep + 1}`;
      if (fs.existsSync(over)) fs.unlinkSync(over);
    } catch { /* logging must never throw */ }
  }
  write(line) {
    try {
      this._rotate();
      fs.appendFileSync(this.file, this._scrub(line) + '\n');
    } catch { /* ignore */ }
  }
  info(msg) { this.write(`${new Date().toISOString()} INFO ${msg}`); }
  error(msg) { this.write(`${new Date().toISOString()} ERROR ${msg}`); }
}

module.exports = { RotatingLog };
