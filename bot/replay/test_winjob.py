"""Job-object proof: grandchild dies when the job-joined child is force-killed."""
import subprocess
import sys
import time
from pathlib import Path

import pytest

from bot.replay import winjob
from bot.replay.test_pipeline_lifecycle import pid_alive, wait_for

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows job objects")

CHILD = r"""
import subprocess, sys, time, os
join = sys.argv[1] == "join"
if join:
    sys.path.insert(0, sys.argv[4])
    from bot.replay import winjob
    print("JOINED", winjob.assign_self_to_kill_on_close_job(), flush=True)
g = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(120)"])
open(sys.argv[2], "w").write(str(g.pid))
open(sys.argv[3], "w").write(str(os.getpid()))
time.sleep(120)
"""


def _run(tmp_path, mode):
    script = tmp_path / "child.py"
    script.write_text(CHILD)
    gpid, cpid = tmp_path / "g.txt", tmp_path / "c.txt"
    repo = str(Path(__file__).resolve().parents[2])
    p = subprocess.Popen([sys.executable, str(script), mode, str(gpid), str(cpid), repo],
                         stdout=subprocess.PIPE, text=True)
    assert wait_for(lambda: gpid.exists() and cpid.exists() and gpid.read_text() and cpid.read_text(), 30)
    g = int(gpid.read_text())
    assert pid_alive(g)
    subprocess.run(["taskkill", "/F", "/PID", str(p.pid)], capture_output=True)
    p.wait(timeout=10)
    return g, p


def test_grandchild_dies_with_job_member(tmp_path):
    g, _ = _run(tmp_path, "join")
    try:
        assert wait_for(lambda: not pid_alive(g), 5)
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(g)], capture_output=True)


def test_negative_control_grandchild_survives_without_job(tmp_path):
    g, _ = _run(tmp_path, "nojoin")
    try:
        time.sleep(1.5)
        assert pid_alive(g)
    finally:
        subprocess.run(["taskkill", "/F", "/PID", str(g)], capture_output=True)


def test_failure_returns_false_never_raises(monkeypatch):
    import ctypes
    monkeypatch.setattr(ctypes, "WinDLL", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    assert winjob.assign_self_to_kill_on_close_job() is False


def test_nested_under_parent_job_works(tmp_path):
    # the helper child itself already runs inside whatever job the test runner has; nested join must succeed
    code = ("import sys;sys.path.insert(0,sys.argv[1]);from bot.replay import winjob;"
            "sys.exit(0 if winjob.assign_self_to_kill_on_close_job() else 3)")
    repo = str(Path(__file__).resolve().parents[2])
    inner = (
        "import subprocess,sys;"
        "from bot.replay import winjob;"
        "ok1=winjob.assign_self_to_kill_on_close_job();"
        "r=subprocess.run([sys.executable,'-c',%r,sys.argv[1]]);"
        "sys.exit(0 if ok1 and r.returncode==0 else 4)" % code
    )
    r = subprocess.run([sys.executable, "-c", inner, repo], cwd=repo, capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
