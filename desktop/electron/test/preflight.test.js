'use strict';
const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { dataWritable, openLog, nullLog, unwritableText } = require('../src/preflight');
const { RotatingLog } = require('../src/logger');

function tmp() { return fs.mkdtempSync(path.join(process.env.TMPDIR || os.tmpdir(), 'vp-pre-')); }

test('writable folder -> null and no leftover probe file', () => {
  const d = tmp();
  assert.strictEqual(dataWritable(d), null);
  assert.deepStrictEqual(fs.readdirSync(path.join(d, 'logs')), []);
});

test('data root below a regular file -> error message, no throw', () => {
  const d = tmp(); const f = path.join(d, 'file'); fs.writeFileSync(f, 'x');
  const msg = dataWritable(path.join(f, 'sub'));
  assert.ok(msg && /ENOTDIR|EEXIST|not a directory/i.test(msg), msg);
});

test('openLog never throws and falls back to a no-op log', () => {
  const d = tmp(); const f = path.join(d, 'file'); fs.writeFileSync(f, 'x');
  const r = openLog(RotatingLog, path.join(f, 'sub', 'logs', 'electron.log'));
  assert.strictEqual(r.log, nullLog);
  assert.ok(r.error);
  r.log.info('x'); r.log.error('y'); r.log.addSecret('z'); // must be harmless
  const ok = openLog(RotatingLog, path.join(d, 'logs', 'electron.log'));
  assert.strictEqual(ok.error, null);
  ok.log.info('hi');
  assert.match(fs.readFileSync(path.join(d, 'logs', 'electron.log'), 'utf8'), /hi/);
});

test('box text names the folder', () => {
  const t = unwritableText('C:\\x', 'EACCES');
  assert.strictEqual(t.what, 'ValoHub cannot write its data folder.');
  assert.ok(t.why.includes('C:\\x') && t.why.includes('EACCES'));
  assert.ok(t.fix.includes('--home'));
});
