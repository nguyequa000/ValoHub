// Window-exists evidence: real Electron + fake sidecar; query Win32 main window (PowerShell) and CDP /json on the QA debug port.
import { spawn, spawnSync } from 'node:child_process';
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const here = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(here, '..');
const home = path.resolve(process.argv[2]); fs.mkdirSync(home, { recursive: true });
const py = process.env.FAKE_PYTHON || path.join(process.env.LOCALAPPDATA, 'Programs/Python/Python312/python.exe');
const env = Object.assign({}, process.env, { VALOHUB_PORT: '18821', VALOHUB_QA_DEBUG_PORT: '19221', FAKE_MODE: 'normal',
  VALOHUB_SIDECAR_CMD: JSON.stringify([py, path.join(here, 'fake_sidecar.py')]) });
const child = spawn(path.join(root, 'node_modules/electron/dist/electron.exe'), ['.', '--home', home, '--smoke-seconds', '14', '--report', path.join(home, 'report.json')], { cwd: root, env, stdio: 'ignore' });
const get = (u) => new Promise((res) => http.get(u, (r) => { let b = ''; r.on('data', (c) => (b += c)); r.on('end', () => res(b)); }).on('error', (e) => res('ERR ' + e.message)));
await new Promise((r) => setTimeout(r, 7000));
console.log('electron pid', child.pid);
const ps = spawnSync('powershell', ['-NoProfile', '-Command',
  `Get-Process electron -ErrorAction SilentlyContinue | Where-Object {$_.MainWindowHandle -ne 0} | ForEach-Object { "$($_.Id) hwnd=$($_.MainWindowHandle) title=$($_.MainWindowTitle)" }; Add-Type -Name W -Namespace N -MemberDefinition '[DllImport("user32.dll")] public static extern bool IsWindowVisible(System.IntPtr h);'; Get-Process electron | Where-Object {$_.MainWindowHandle -ne 0} | ForEach-Object { "visible=" + [N.W]::IsWindowVisible($_.MainWindowHandle) }`], { encoding: 'utf8' });
console.log(ps.stdout, ps.stderr);
const j = await get('http://127.0.0.1:19221/json');
console.log('CDP /json:', j.slice(0, 600));
await new Promise((r) => child.on('exit', r));
console.log('exit', child.exitCode);
console.log('report', fs.readFileSync(path.join(home, 'report.json'), 'utf8'));
