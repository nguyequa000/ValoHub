"""Process lifecycle: cancel/timeout kill the real child, env/flags, heartbeat."""
import asyncio
import os
import sys
import time
from pathlib import Path

import pytest

from bot.replay import pipeline, store

SLEEPER = (
    "import os,sys,time\n"
    "open(sys.argv[1],'w').write(str(os.getpid()))\n"
    "print('started',flush=True)\n"
    "time.sleep(float(sys.argv[2]))\n"
)


def pid_alive(pid: int) -> bool:
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True) if sys.platform == "win32" else None
    if k is None:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(code))
    k.CloseHandle(ctypes.c_void_p(h))
    return code.value == 259  # STILL_ACTIVE


def wait_for(pred, secs=15.0):
    end = time.monotonic() + secs
    while time.monotonic() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


@pytest.fixture
def sleeper(tmp_path, monkeypatch):
    script = tmp_path / "sleeper.py"
    script.write_text(SLEEPER)
    monkeypatch.setattr(store, "WORK_DIR", tmp_path / "work")
    return script, tmp_path / "pid.txt"


def _read_pid(pidfile):
    assert wait_for(lambda: pidfile.exists() and pidfile.read_text().strip())
    return int(pidfile.read_text())


def test_cancel_kills_child_and_propagates(sleeper):
    script, pidfile = sleeper

    async def go():
        task = asyncio.create_task(pipeline._run_process([sys.executable, script, pidfile, "60"], timeout=120))
        await asyncio.get_running_loop().run_in_executor(None, _read_pid, pidfile)
        pid = int(pidfile.read_text())
        assert pid_alive(pid), "child must be alive before cancel (control)"
        t0 = time.monotonic()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        return pid, time.monotonic() - t0

    pid, took = asyncio.run(go())
    assert took < 5.5
    assert wait_for(lambda: not pid_alive(pid), 5)


def test_negative_control_prefix_logic_orphans_child(sleeper):
    """The pre-fix logic (cancel without kill) leaves the child running: proves the test can fail."""
    script, pidfile = sleeper

    async def go():
        proc = await asyncio.create_subprocess_exec(sys.executable, str(script), str(pidfile), "60",
                                                    stdout=asyncio.subprocess.PIPE)
        task = asyncio.create_task(proc.wait())
        await asyncio.get_running_loop().run_in_executor(None, _read_pid, pidfile)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        alive = pid_alive(proc.pid)
        proc.kill()
        await proc.wait()
        return alive

    assert asyncio.run(go()) is True


def test_timeout_kills_child(sleeper):
    script, pidfile = sleeper

    async def go():
        with pytest.raises(pipeline.PipelineError, match="timed out"):
            await pipeline._run_process([sys.executable, script, pidfile, "60"], timeout=0.5)

    import bot.replay.pipeline as p
    old = p.HEARTBEAT_INTERVAL_S
    p.HEARTBEAT_INTERVAL_S = 0.2
    try:
        asyncio.run(go())
    finally:
        p.HEARTBEAT_INTERVAL_S = old
    pid = int(pidfile.read_text())
    assert wait_for(lambda: not pid_alive(pid), 5)


def test_heartbeat_fires_and_output_captured(sleeper, monkeypatch):
    script, pidfile = sleeper
    monkeypatch.setattr(pipeline, "HEARTBEAT_INTERVAL_S", 0.2)
    beats = []
    rc, tail = asyncio.run(pipeline._run_process([sys.executable, script, pidfile, "1.2"], timeout=30,
                                                 heartbeat=beats.append))
    assert rc == 0 and tail == ["started"]
    assert len(beats) >= 2


def test_env_cwd_and_creationflags(sleeper, monkeypatch, tmp_path):
    script, pidfile = sleeper
    dotnet_dir = tmp_path / "rt"
    dotnet_dir.mkdir()
    monkeypatch.setattr(pipeline, "find_decoder", lambda: (dotnet_dir / "dotnet.exe", None))

    class Loc:
        source = "bundle"
    monkeypatch.setattr(pipeline, "_decoder_location", lambda: Loc())
    monkeypatch.delenv("DOTNET_CLI_HOME", raising=False)
    monkeypatch.delenv("NUGET_PACKAGES", raising=False)
    env, flags = pipeline._process_env_and_flags()
    assert env["DOTNET_ROOT"] == str(dotnet_dir)
    assert env["DOTNET_MULTILEVEL_LOOKUP"] == "0"
    assert env["DOTNET_NOLOGO"] == "1" and env["DOTNET_CLI_TELEMETRY_OPTOUT"] == "1"
    assert Path(env["DOTNET_CLI_HOME"]).parts[-3:-1] == ("work", "dotnet-home")
    assert Path(env["NUGET_PACKAGES"]).parent.name == "dotnet-home"
    if sys.platform == "win32":
        assert flags == 0x08000000
    # setdefault: caller's value is respected
    monkeypatch.setenv("NUGET_PACKAGES", "X:/pk")
    assert pipeline._process_env_and_flags()[0]["NUGET_PACKAGES"] == "X:/pk"
    # legacy runtime keeps the dotnet10-sdk homes (negative control for the bundled branch)
    Loc.source = "legacy"
    monkeypatch.delenv("DOTNET_CLI_HOME", raising=False)
    assert "dotnet10-sdk" in pipeline._process_env_and_flags()[0]["DOTNET_CLI_HOME"]

    seen = {}
    real = asyncio.create_subprocess_exec

    async def spy(*a, **kw):
        seen.update(kw)
        return await real(*a, **kw)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spy)
    asyncio.run(pipeline._run_process([sys.executable, script, pidfile, "0"], timeout=30))
    assert Path(seen["cwd"]) == tmp_path / "work"
    if sys.platform == "win32":
        assert seen["creationflags"] == 0x08000000
