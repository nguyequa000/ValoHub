// Real-Electron + fake-sidecar smoke. Usage: node test/run_real.mjs <home-dir> [port] [extra electron args...]
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const home = path.resolve(process.argv[2]);
const port = process.argv[3] || '18821';
const extra = process.argv.slice(4);
fs.mkdirSync(home, { recursive: true });
const seen = path.join(home, 'seen.jsonl');
const report = path.join(home, 'report.json');
for (const f of [seen, report]) if (fs.existsSync(f)) fs.unlinkSync(f);
const env = Object.assign({}, process.env, {
  FAKE_SEEN: seen, VALOHUB_PORT: port,
  VALOHUB_SIDECAR_CMD: JSON.stringify([process.env.FAKE_PYTHON || path.join(process.env.LOCALAPPDATA, 'Programs/Python/Python312/python.exe'), path.join(here, 'fake_sidecar.py')]),
});
const exe = path.join(root, 'node_modules', 'electron', 'dist', 'electron.exe');
const r = spawnSync(exe, ['.', '--home', home, '--smoke-seconds', '6', '--report', report, ...extra], { cwd: root, env, encoding: 'utf8', timeout: 90000 });
console.log('exit', r.status, r.signal);
console.log('stdout', (r.stdout || '').slice(0, 500));
console.log('stderr', (r.stderr || '').slice(0, 800));
const show = (f) => console.log(`--- ${path.basename(f)}\n` + (fs.existsSync(f) ? fs.readFileSync(f, 'utf8') : '(missing)'));
show(report); show(seen); show(path.join(home, 'logs', 'network-denied.jsonl'));
const lg = path.join(home, 'logs', 'electron.log');
console.log('--- electron.log tail\n' + (fs.existsSync(lg) ? fs.readFileSync(lg, 'utf8').split('\n').slice(-10).join('\n') : '(missing)'));
