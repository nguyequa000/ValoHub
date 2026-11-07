"""Frozen normalizer child + command."""
import json
import subprocess
import sys

from bot.replay import childmain, pipeline


def test_maybe_run_child_dispatch(monkeypatch):
    calls = []
    monkeypatch.setattr(childmain, "run_normalize", lambda argv: calls.append(list(argv)) or 7)
    assert childmain.maybe_run_child(["normalize", "a", "b"]) == 7
    assert childmain.maybe_run_child(["--run-normalizer", "c", "d"]) == 7
    assert calls == [["a", "b"], ["c", "d"]]
    # negative control: anything else is not a child invocation
    assert childmain.maybe_run_child(["--home", "x"]) is None
    assert childmain.maybe_run_child([]) is None
    assert len(calls) == 2


def test_run_normalize_returns_exit_code(monkeypatch):
    from bot.replay import normalize
    monkeypatch.setattr(normalize, "main", lambda argv: 3)
    assert childmain.run_normalize(["x"]) == 3
    monkeypatch.setattr(normalize, "main", lambda argv: None)
    assert childmain.run_normalize(["x"]) == 0


def test_real_child_help_exit_zero():
    code = ("import sys;from bot.replay import childmain;"
            "sys.exit(childmain.maybe_run_child(['normalize','--help']))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr


def test_childmain_import_is_light():
    code = "import sys;import bot.replay.childmain;print('bot.replay.normalize' in sys.modules)"
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    assert r.stdout.strip() == "False"


def test_normalizer_command_frozen_vs_source(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert pipeline.normalizer_command("r", "v") == [sys.executable, pipeline.NORMALIZER_PATH, "r", "v"]
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert pipeline.normalizer_command("r", "v") == [sys.executable, "normalize", "r", "v"]


def test_normalize_does_not_need_script_when_frozen(monkeypatch, tmp_path):
    import asyncio
    from pathlib import Path
    captured = {}

    async def fake_run(argv, *, timeout, heartbeat=None):
        captured["argv"] = argv
        return 0, []
    monkeypatch.setattr(pipeline, "_run_process", fake_run)
    monkeypatch.setattr(pipeline, "NORMALIZER_PATH", tmp_path / "missing.py")
    monkeypatch.setattr(pipeline.store, "update_manifest", lambda *a, **k: None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    asyncio.run(pipeline._normalize("rid", tmp_path / "raw", tmp_path / "viewer", lambda *a: None))
    assert captured["argv"][1] == "normalize"
    # negative control: not frozen + missing script -> PipelineError
    monkeypatch.delattr(sys, "frozen", raising=False)
    try:
        asyncio.run(pipeline._normalize("rid", tmp_path / "raw", tmp_path / "viewer", lambda *a: None))
    except pipeline.PipelineError:
        pass
    else:
        raise AssertionError("expected PipelineError")
