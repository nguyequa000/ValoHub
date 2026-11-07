// ASAR-integrity negative control: pristine packaged exe vs same exe with a tampered app.asar.
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
const src = path.resolve('dist/win-unpacked');
const dst = path.resolve(process.argv[2]);
fs.cpSync(src, dst, { recursive: true });
const exe = path.join(dst, 'ValoHub.exe');
const home = path.join(dst, '..', 'home-' + Date.now());
const run = (label) => {
  const r = spawnSync(exe, ['--check', '--home', home], { encoding: 'utf8', timeout: 30000 });
  console.log(label, 'exit=', r.status, 'signal=', r.signal, 'stdout=', (r.stdout || '').trim().slice(0, 400), 'stderr=', (r.stderr || '').trim().slice(0, 200));
  return r.status;
};
const a = run('pristine');
const asar = path.join(dst, 'resources', 'app.asar');
const buf = fs.readFileSync(asar);
buf[buf.length - 1] ^= 0xff; // flip last byte (inside file data)
fs.writeFileSync(asar, buf);
const b = run('tampered');
console.log('RESULT', a === 2 && b !== 2 ? 'integrity enforced (tampered build does not run)' : 'NOT CONCLUSIVE', a, b);
