"""Restart sweep: stale staging *.part and 'processing' cards (tmp dirs only)."""
from __future__ import annotations

import json

from bot.replay import pipeline, store


def _setup(tmp_path, monkeypatch):
    data = tmp_path / "data" / "replays"
    tmp = tmp_path / "tmp"
    (tmp / "incoming").mkdir(parents=True)
    data.mkdir(parents=True)
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", data)
    monkeypatch.setattr(store, "TMP_DIR", tmp)
    return data, tmp


def _card(data, rid, status):
    d = data / rid
    d.mkdir()
    (d / "manifest.json").write_text(json.dumps({"status": status, "keep": 1}))


def test_sweep_removes_parts_and_fails_processing(tmp_path, monkeypatch):
    data, tmp = _setup(tmp_path, monkeypatch)
    (tmp / "incoming" / "a.part").write_bytes(b"x")
    (tmp / "incoming" / "keep.txt").write_bytes(b"x")
    _card(data, "aaaaaaaaaaaaaaaa", "processing")
    _card(data, "bbbbbbbbbbbbbbbb", "ready")
    msgs = []
    res = pipeline.sweep_interrupted(msgs.append)
    assert res["parts"] == ["a.part"]
    assert res["failed"] == ["aaaaaaaaaaaaaaaa"]
    assert not (tmp / "incoming" / "a.part").exists()
    assert (tmp / "incoming" / "keep.txt").exists()
    m = store.read_manifest("aaaaaaaaaaaaaaaa")
    assert m["status"] == "failed" and "interrupted" in m["notes"] and m["keep"] == 1
    assert store.read_manifest("bbbbbbbbbbbbbbbb")["status"] == "ready"
    assert msgs and "aaaaaaaaaaaaaaaa" in msgs[0]
    assert pipeline.sweep_interrupted()["failed"] == []  # idempotent


def test_sweep_missing_dirs_is_noop(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_REPLAYS_DIR", tmp_path / "nope")
    monkeypatch.setattr(store, "TMP_DIR", tmp_path / "nope2")
    assert pipeline.sweep_interrupted() == {"parts": [], "failed": []}
