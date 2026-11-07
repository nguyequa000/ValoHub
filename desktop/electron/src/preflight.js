'use strict';
// Data-folder preflight: can <dataRoot>/logs be created and written? Pure node, testable without Electron.
const fs = require('node:fs');
const path = require('node:path');

// Returns null when writable, else the error message (ENOTDIR / EACCES / EROFS / ...).
function dataWritable(dataRoot, pid = process.pid) {
  try {
    fs.mkdirSync(path.join(dataRoot, 'logs'), { recursive: true });
    const p = path.join(dataRoot, 'logs', `.w-${pid}`);
    fs.writeFileSync(p, 'x'); fs.unlinkSync(p);
    return null;
  } catch (e) { return e.message; }
}

// No-op logger used when the real log file cannot be created.
const nullLog = { info() {}, error() {}, addSecret() {}, write() {} };

// Try to create the rotating log; never throws. -> { log, error }
function openLog(RotatingLog, file) {
  try { return { log: new RotatingLog(file), error: null }; } catch (e) { return { log: nullLog, error: e.message }; }
}

// The text of the designed "cannot write" box.
function unwritableText(dataRoot, why) {
  return {
    what: 'ValoHub cannot write its data folder.',
    why: `${dataRoot}: ${why}`,
    fix: 'Free disk space / fix permissions, or start with --home <writable folder>.',
  };
}

module.exports = { dataWritable, nullLog, openLog, unwritableText };
