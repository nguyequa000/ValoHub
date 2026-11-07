"use strict";
// Electron stub standing in for ValoHub: sandboxed window titled ValoHub, served from a local
// loopback page; CDP only when VALOHUB_QA_DEBUG_PORT is set; closes when the window closes.
const { app, BrowserWindow, session } = require('electron');
const http = require('node:http');
const port = Number(process.env.STUB_PORT || 18842);
if (process.env.VALOHUB_QA_DEBUG_PORT) {
  app.commandLine.appendSwitch('remote-debugging-port', process.env.VALOHUB_QA_DEBUG_PORT);
  app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1');
}
const srv = http.createServer((q, r) => {
  r.setHeader('content-type', 'text/html');
  r.end('<title>ValoHub</title><body style="background:#223"><article class="replay-card">a</article><canvas id="c" width="200" height="100"></canvas><script>const x=document.getElementById("c").getContext("2d");x.fillStyle="#223";x.fillRect(0,0,200,100);x.fillStyle="#f44";x.fillRect(20,20,100,50)</script></body>');
});
app.whenReady().then(() => {
  session.defaultSession.webRequest.onBeforeRequest((d, cb) => cb({ cancel: !/^(http:\/\/127\.0\.0\.1|devtools|data|chrome)/.test(d.url) }));
  srv.listen(port, '127.0.0.1', () => {
    const w = new BrowserWindow({ width: 800, height: 500, title: 'ValoHub', webPreferences: { sandbox: true, contextIsolation: true, nodeIntegration: false } });
    w.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    w.on('page-title-updated', (e) => e.preventDefault());
    w.loadURL(`http://127.0.0.1:${port}/replays`);
  });
});
app.on('window-all-closed', () => { srv.close(); app.quit(); });
