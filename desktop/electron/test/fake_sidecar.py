"""Fake ValoHub sidecar for hermetic supervisor tests (stdlib only).

Usage: python fake_sidecar.py serve --port P --data-dir D
Env: VRB_TOKEN (required token), FAKE_MODE (comma list):
  normal | hang (never print ready) | early_exit | bad_ready | ignore_shutdown
  | crash (exit 3 shortly after ready) | grandchild (spawn a sleeping child, pid -> FAKE_PIDFILE)
  | busy_port (ignore --port, bind ephemeral)
FAKE_SEEN: file where every request is appended as JSON line {path, token, cookie, host}.
"""
import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MODES = set(filter(None, os.environ.get("FAKE_MODE", "normal").split(",")))
TOKEN = os.environ.get("VRB_TOKEN", "")
SEEN = os.environ.get("FAKE_SEEN")
SERVER = None


def arg(name, default=None):
    a = sys.argv
    return a[a.index(name) + 1] if name in a else default


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _record(self):
        if SEEN:
            rec = {"path": self.path, "token": self.headers.get("X-ValoHub-Token"),
                   "cookie": self.headers.get("Cookie"), "host": self.headers.get("Host")}
            with open(SEEN, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")

    def _authed(self):
        if self.headers.get("X-ValoHub-Token") == TOKEN and TOKEN:
            return True
        return ("vh_session=" + TOKEN) in (self.headers.get("Cookie") or "") and bool(TOKEN)

    def _send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self):
        self._record()
        p = self.path.split("?")[0]
        if p.startswith("/api/") and not self._authed():
            return self._send(401, b'{"error":"unauthorized"}')
        if p == "/ws" and not self._authed():
            return self._send(401, b'{"error":"unauthorized"}')
        if p == "/api/app/health":
            return self._send(200, b'{"ok":true}')
        if p == "/api/app/shutdown" and self.command == "POST":
            self._send(200, b'{"ok":true}')
            if "ignore_shutdown" not in MODES:
                threading.Thread(target=lambda: (time.sleep(0.1), os._exit(0)), daemon=True).start()
            return
        if p == "/api/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b"data: hello\n\n")
            self.wfile.flush()
            self.close_connection = True
            return
        if p == "/ws" and self.headers.get("Upgrade", "").lower() == "websocket":
            key = self.headers.get("Sec-WebSocket-Key", "")
            acc = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", acc)
            self.end_headers()
            self.wfile.write(b"\x81\x02hi")
            self.wfile.flush()
            time.sleep(0.2)
            self.close_connection = True
            return
        if p in ("/replays", "/replays/"):
            html = (b"<!doctype html><title>Fake Replays</title><body>fake<script src='/static/app.js'></script>"
                    b"<img src='/static/pic.png'>"
                    + (b"<img src='http://evil.invalid/leak.png'>" if "external" in MODES else b"") + b"</body>")
            return self._send(200, html, "text/html")
        if p == "/static/app.js":
            js = (b"var st={};function up(k,v){st[k]=v;document.title=['fetch','es','ws'].filter(function(x){return st[x]}).map(function(x){return st[x]}).join('|')}"
                  b"fetch('/api/ping').then(r=>up('fetch','fetch:'+r.status));"
                  b"try{var es=new EventSource('/api/events');es.onmessage=function(){up('es','es-ok');es.close()}}catch(e){}"
                  b"try{var w=new WebSocket('ws://'+location.host+'/ws');w.onmessage=function(){up('ws','ws-ok')}}catch(e){}")
            return self._send(200, js, "application/javascript")
        if p == "/static/pic.png":
            return self._send(200, b"x", "image/png")
        if p == "/api/ping":
            return self._send(200, b'{"pong":true}')
        return self._send(404, b"{}")

    do_GET = do_POST = _handle


def main():
    global SERVER
    if "early_exit" in MODES:
        sys.stderr.write("fake: early exit\n")
        sys.exit(4)
    if "hang" in MODES:
        time.sleep(3600)
    port = 0 if "busy_port" in MODES else int(arg("--port", "0"))
    SERVER = ThreadingHTTPServer(("127.0.0.1", port), H)
    SERVER.daemon_threads = True
    if "grandchild" in MODES:
        gc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
        pf = os.environ.get("FAKE_PIDFILE")
        if pf:
            open(pf, "w").write(str(gc.pid))
    if "bad_ready" in MODES:
        print("hello not json", flush=True)
    else:
        print(json.dumps({"event": "ready", "port": SERVER.server_address[1], "pid": os.getpid()}, separators=(",", ":")), flush=True)
    sys.stderr.write("fake: serving\n")
    sys.stderr.flush()

    def stdin_watch():
        try:
            while sys.stdin.buffer.read(1024):
                pass
        except Exception:
            pass
        if "ignore_shutdown" not in MODES:
            os._exit(0)

    threading.Thread(target=stdin_watch, daemon=True).start()
    if "crash" in MODES:
        threading.Thread(target=lambda: (time.sleep(float(os.environ.get("FAKE_CRASH_S", "1.0"))), sys.stderr.write("fake: boom\n"), sys.stderr.flush(), os._exit(3)), daemon=True).start()
    SERVER.serve_forever()


main()
