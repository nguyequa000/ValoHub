"""Self-tests for cdp/run_checks.mjs and window_smoke.py, with negative controls.

Run: py -3.12 -m unittest tools.desktop_qa.cdp.tests.test_selftests -v   (or pytest this file)
Env: L5_SCRATCH (default hermes scratch), L5_OUT (evidence dir), L5_SPIKECON, L5_ELECTRON_STUB_DIR.
"""
import json, os, shutil, subprocess, sys, threading, time, unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CDP = HERE.parent
SMOKE = CDP.parent / "window_smoke.py"
SCRATCH = Path(os.environ.get("L5_SCRATCH", "C:/Users/johnq/AppData/Local/hermes/cache/scratch/l5"))
OUT = Path(os.environ.get("L5_OUT", str(SCRATCH / "out")))
EDGE = next((p for p in ["C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe", "C:/Program Files/Microsoft/Edge/Application/msedge.exe"] if os.path.exists(p)), None)
SPIKE = os.environ.get("L5_SPIKECON", "C:/Users/johnq/AppData/Local/hermes/cache/scratch/dt-spike/run1/dist2/spikecon/spikecon.exe")
STUB = Path(os.environ.get("L5_ELECTRON_STUB_DIR", str(SCRATCH / "electron-stub")))
APP_PORT, DEBUG_PORT = 18841, 19241

PAINT = ('<canvas id="c" width="300" height="200"></canvas><script>const x=document.getElementById("c").getContext("2d");'
         'x.fillStyle="#102030";x.fillRect(0,0,300,200);x.fillStyle="#ff4655";x.fillRect(30,30,120,90);x.fillStyle="#fff";x.fillRect(180,100,60,60);</script>')
GOOD_IMG = "<img src='/ok.png'>"
PIXEL = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c6360000002000001e221bc330000000049454e44ae426082")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        if p == "/ok.png":
            return self._send(PIXEL, "image/png")
        if p == "/replays":
            body = "<title>t</title>" + "".join("<article class='replay-card'>r%d</article>" % i for i in range(4))
        elif p.endswith("/view"):
            rid = p.split("/")[2]
            body = {"good": PAINT + GOOD_IMG,
                    "blank": '<canvas id="c" width="300" height="200"></canvas>' + GOOD_IMG,
                    "ext": PAINT + "<script>fetch('https://example.invalid/x').catch(()=>{})</script>",
                    "brokenimg": PAINT + "<img src='/missing.png'>"}.get(rid, PAINT)
        elif p.endswith("/analysis"):
            body = PAINT
        else:
            self.send_response(404); self.end_headers(); return
        self._send(body.encode(), "text/html")

    def _send(self, data, ct):
        self.send_response(200); self.send_header("content-type", ct); self.send_header("content-length", str(len(data))); self.end_headers(); self.wfile.write(data)


def kill_tree(pid):  # only a PID we started
    subprocess.run(["taskkill", "/pid", str(pid), "/t", "/f"], capture_output=True)


def run_checks(name, ids, expect_cards="4", extra=()):
    out = OUT / name
    shutil.rmtree(out, ignore_errors=True)
    cmd = ["node", str(CDP / "run_checks.mjs"), "--debug-port", str(DEBUG_PORT), "--base-url", "http://127.0.0.1:%d" % APP_PORT,
           "--replay-ids", ids, "--expect-cards", expect_cards, "--out", str(out), "--settle-ms", "1200", *extra]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    (OUT / (name + ".stdout.txt")).write_text(r.stdout + r.stderr + "\nexit=%d\n" % r.returncode)
    rep = json.loads((out / "report.json").read_text()) if (out / "report.json").exists() else {}
    return r.returncode, rep


@unittest.skipUnless(EDGE and shutil.which("node"), "needs msedge + node")
class EdgeTargets(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        OUT.mkdir(parents=True, exist_ok=True)
        cls.srv = ThreadingHTTPServer(("127.0.0.1", APP_PORT), H)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        prof = SCRATCH / ("edge-profile-%d" % os.getpid())
        cls.edge = subprocess.Popen([EDGE, "--headless=new", "--remote-debugging-port=%d" % DEBUG_PORT, "--remote-debugging-address=127.0.0.1",
                                     "--user-data-dir=" + str(prof), "--no-first-run", "--block-new-web-contents", "--window-size=1000,700",
                                     "http://127.0.0.1:%d/replays" % APP_PORT], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    @classmethod
    def tearDownClass(cls):
        kill_tree(cls.edge.pid); cls.srv.shutdown()

    def test_positive(self):
        code, rep = run_checks("edge-positive", "good")
        self.assertEqual(code, 0, rep.get("checks"))
        names = [c["name"] for c in rep["checks"]]
        self.assertIn("view good: canvas painted", names)
        self.assertEqual(rep["nonLoopback"], [])
        self.assertTrue(rep["screenshots"])

    def test_negative_blank_canvas(self):
        code, rep = run_checks("edge-neg-blank", "blank")
        self.assertEqual(code, 1)
        bad = [c["name"] for c in rep["checks"] if not c["ok"]]
        self.assertIn("view blank: canvas painted", bad)

    def test_negative_non_loopback(self):
        code, rep = run_checks("edge-neg-ext", "ext")
        self.assertEqual(code, 1)
        self.assertTrue(any("example.invalid" in u for u in rep["nonLoopback"]), rep["nonLoopback"])
        self.assertEqual(rep["blocked"], [])

    def test_negative_broken_image(self):
        code, rep = run_checks("edge-neg-img", "brokenimg")
        self.assertEqual(code, 1)
        self.assertIn("view brokenimg: images load", [c["name"] for c in rep["checks"] if not c["ok"]])

    def test_negative_wrong_card_count(self):
        code, rep = run_checks("edge-neg-cards", "good", expect_cards="7")
        self.assertEqual(code, 1)

    def test_negative_sandbox_open(self):  # plain Edge exposes no require/process; window.open is covered by --block-new-web-contents
        code, rep = run_checks("edge-sandbox", "good")
        self.assertTrue(all(c["ok"] for c in rep["checks"] if c["name"].startswith("sandbox:")), rep["checks"])


@unittest.skipUnless(os.path.exists(SPIKE), "spikecon missing")
class WindowStandIn(unittest.TestCase):
    def smoke(self, name, *args):
        OUT.mkdir(parents=True, exist_ok=True)
        j = OUT / (name + ".json")
        r = subprocess.run([sys.executable, str(SMOKE), "--json", str(j), *args], capture_output=True, text=True, timeout=120)
        (OUT / (name + ".stdout.txt")).write_text(r.stdout + r.stderr + "\nexit=%d\n" % r.returncode)
        return r.returncode, json.loads(j.read_text())

    def test_positive(self):
        code, rep = self.smoke("window-spikecon", "--cmd", SPIKE, "--title", "ValoHub spike window", "--expect-image", SPIKE, "--screenshot", str(OUT / "window-spikecon.png"), "--timeout", "40")
        self.assertEqual(code, 0, rep["checks"])
        self.assertTrue((OUT / "window-spikecon.png").stat().st_size > 1000)

    def test_negative_wrong_image(self):
        code, rep = self.smoke("window-neg-image", "--cmd", SPIKE, "--title", "ValoHub spike window", "--expect-image", "C:/Windows/notepad.exe", "--timeout", "40")
        self.assertEqual(code, 1)
        self.assertIn("owner image == expected", [c["name"] for c in rep["checks"] if not c["ok"]])

    def test_negative_missing_window(self):
        code, rep = self.smoke("window-neg-title", "--cmd", SPIKE, "--title", "NoSuchTitle", "--timeout", "6")
        self.assertEqual(code, 1)

    def test_negative_browser_rejected(self):
        # an Edge window must be rejected as "browser shortcut" (title match on about:blank page title)
        if not EDGE:
            self.skipTest("no edge")
        prof = SCRATCH / ("edge-smoke-%d" % os.getpid())
        url = "data:text/html,<title>ValoBrowserNeg</title>"
        code, rep = self.smoke("window-neg-browser", "--cmd", '"%s" --user-data-dir="%s" --no-first-run --new-window "%s"' % (EDGE, prof, url), "--title", "ValoBrowserNeg", "--title-match", "contains", "--timeout", "20", "--close-mode", "kill")
        self.assertEqual(code, 1)
        self.assertIn("owner is not a browser", [c["name"] for c in rep["checks"] if not c["ok"]])


@unittest.skipUnless((STUB / "node_modules" / "electron" / "dist" / "electron.exe").exists(), "electron stub not installed")
class ElectronStub(unittest.TestCase):
    def test_electron(self):
        OUT.mkdir(parents=True, exist_ok=True)
        exe = STUB / "node_modules" / "electron" / "dist" / "electron.exe"
        main_dir = HERE / "electron_stub" / "main.js"
        env = dict(os.environ, STUB_PORT="18842", VALOHUB_QA_DEBUG_PORT=str(DEBUG_PORT))
        j = OUT / "window-electron.json"
        sm = subprocess.Popen([sys.executable, str(SMOKE), "--cmd", '"%s" "%s"' % (exe, main_dir), "--title", "ValoHub", "--expect-image", str(exe),
                               "--expect-class", "Chrome_WidgetWin_1", "--expect-chromium-children", "--hold", "14", "--screenshot", str(OUT / "window-electron.png"),
                               "--json", str(j), "--timeout", "60"], env=env, stdout=open(OUT / "window-electron.stdout.txt", "w"), stderr=subprocess.STDOUT)
        time.sleep(8)
        out = OUT / "electron-checks"
        shutil.rmtree(out, ignore_errors=True)
        r = subprocess.run(["node", str(CDP / "run_checks.mjs"), "--debug-port", str(DEBUG_PORT), "--base-url", "http://127.0.0.1:18842",
                            "--replay-ids", "x", "--expect-cards", "1", "--out", str(out), "--settle-ms", "800"], capture_output=True, text=True, timeout=120)
        (OUT / "electron-checks.stdout.txt").write_text(r.stdout + r.stderr + "\nexit=%d\n" % r.returncode)
        sm.wait(timeout=90)
        rep = json.loads(j.read_text())
        self.assertEqual(sm.returncode, 0, rep["checks"])
        self.assertEqual(r.returncode, 0, r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
