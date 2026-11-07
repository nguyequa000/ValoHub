"""Tests for bot.replay.paths (desktop path contract v1). Hermetic: tmp dirs + explicit env mappings."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from bot.replay import paths

REPO = Path(paths.__file__).resolve().parents[2]


def _env(**kw) -> dict:
    return {key: value for key, value in kw.items() if value is not None}


def test_legacy_mode_is_todays_layout(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    p = paths.compute_paths(_env())
    assert p.mode == "legacy"
    assert p.code_root == REPO and p.data_root == REPO
    assert p.data_replays == REPO / "data" / "replays"
    assert p.originals == REPO / "replays"
    assert p.work == REPO / "VALOPLANT_WORK" and p.tmp == REPO / "VALOPLANT_WORK" / "tmp"
    assert p.cache == REPO / "cache"
    assert p.bundle_root is None


def test_home_env_selects_desktop_layout(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    p = paths.compute_paths(_env(VALOHUB_HOME=str(tmp_path / "home")))
    root = tmp_path / "home"
    assert p.mode == "desktop" and p.data_root == root and p.code_root == REPO
    assert p.data_replays == root / "data" / "replays"
    assert p.originals == root / "replays"
    assert p.work == root / "VALOPLANT_WORK" and p.tmp == root / "VALOPLANT_WORK" / "tmp"
    assert p.logs == root / "logs" and p.config_file == root / "config.json"


def test_desktop_flag_uses_localappdata(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    p = paths.compute_paths(_env(VALOHUB_DESKTOP="1", LOCALAPPDATA=str(tmp_path / "LA")))
    assert p.mode == "desktop" and p.data_root == tmp_path / "LA" / "ValoHub"


def test_frozen_process_is_desktop_and_bundle_is_exe_folder(tmp_path, monkeypatch):
    exe = tmp_path / "app" / "ValoHub.exe"
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    p = paths.compute_paths(_env(LOCALAPPDATA=str(tmp_path / "LA")))
    assert p.mode == "desktop"
    assert p.bundle_root == exe.parent.resolve()
    assert p.data_root == tmp_path / "LA" / "ValoHub"


def test_compute_is_pure_and_get_paths_caches(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setenv("VALOHUB_HOME", str(tmp_path / "a"))
    first = paths.get_paths(refresh=True)
    monkeypatch.setenv("VALOHUB_HOME", str(tmp_path / "b"))
    assert paths.get_paths() is first                       # cached
    assert paths.get_paths(refresh=True).data_root == tmp_path / "b"
    monkeypatch.delenv("VALOHUB_HOME")
    assert paths.get_paths(refresh=True).mode == "legacy"   # restore for other tests


def test_ensure_dirs_and_writable_probe(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    p = paths.compute_paths(_env(VALOHUB_HOME=str(tmp_path / "home")))
    assert not p.data_root.exists()                          # compute never touches disk
    paths.ensure_data_dirs(p)
    for folder in (p.data_replays, p.originals, p.work, p.tmp, p.cache, p.logs):
        assert folder.is_dir()
    paths.ensure_data_dirs(p)                                # idempotent
    assert paths.check_writable(p.data_root) is None
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert "not writable" in paths.check_writable(blocker / "sub")   # negative control


def test_resolve_source_prefers_data_root_then_code_root(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    p = paths.compute_paths(_env(VALOHUB_HOME=str(tmp_path / "home")))
    rel = "replays/zzz-nonexistent-test.vrf"
    assert paths.resolve_source(rel, p) == p.data_root / rel          # nothing exists -> data_root candidate
    (p.data_root / "replays").mkdir(parents=True)
    (p.data_root / rel).write_bytes(b"x")
    assert paths.resolve_source(rel, p) == p.data_root / rel
    absolute = tmp_path / "abs.vrf"
    assert paths.resolve_source(str(absolute), p) == absolute


def test_resolve_decoder_priority_env_bundle_legacy(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    bundle = tmp_path / "bundle"
    (bundle / "runtime" / "dotnet").mkdir(parents=True)
    (bundle / "runtime" / "dotnet" / "dotnet.exe").write_bytes(b"MZ")
    (bundle / "decoder" / "net10.0").mkdir(parents=True)
    (bundle / "decoder" / "net10.0" / "CliReader.dll").write_bytes(b"MZ")
    (bundle / "decoder" / "parser-commit.txt").write_text("upstream_sha=abc\n")
    (bundle / "decoder" / "registered-branches.json").write_text("[]")
    base = {"VALOHUB_HOME": str(tmp_path / "home"), "VALOHUB_BUNDLE": str(bundle)}
    p = paths.compute_paths(_env(**base))

    loc = paths.resolve_decoder(p, base)
    assert loc.source == "bundle" and loc.ready
    assert loc.dotnet_exe == bundle / "runtime" / "dotnet" / "dotnet.exe"
    assert loc.decoder_dll == bundle / "decoder" / "net10.0" / "CliReader.dll"
    assert loc.decoder_dir == bundle / "decoder" / "net10.0"
    assert loc.record == bundle / "decoder" / "parser-commit.txt"
    assert loc.registry_json == bundle / "decoder" / "registered-branches.json"

    other = tmp_path / "other"
    (other / "net10.0").mkdir(parents=True)
    (other / "net10.0" / "CliReader.dll").write_bytes(b"MZ")
    fake_dotnet = tmp_path / "dotnet.exe"
    fake_dotnet.write_bytes(b"MZ")
    env = dict(base, VALO_DECODER_DIR=str(other), VALO_DOTNET=str(fake_dotnet))
    loc = paths.resolve_decoder(p, env)
    assert loc.source == "env" and loc.decoder_dll == other / "net10.0" / "CliReader.dll"
    assert loc.dotnet_exe == fake_dotnet


def test_resolve_decoder_missing_is_reported_not_raised(tmp_path, monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)
    env = {"VALOHUB_HOME": str(tmp_path / "home")}
    p = paths.compute_paths(_env(**env))
    fake_code_root = tmp_path / "no-code"
    p = paths.AppPaths(**{**p.__dict__, "code_root": fake_code_root})
    loc = paths.resolve_decoder(p, env)
    assert not loc.ready and loc.source == "missing"
    assert loc.dotnet_exe is None and loc.decoder_dll is None
    assert loc.record == p.work / "parser-commit.txt"


def test_to_dict_is_json_serializable(tmp_path):
    import json
    p = paths.compute_paths(_env(VALOHUB_HOME=str(tmp_path)))
    json.dumps(p.to_dict())
    json.dumps(paths.resolve_decoder(p, {}).to_dict())


def test_frozen_electron_layout_uses_parent_bundle(tmp_path, monkeypatch):
    import sys
    from bot.replay import paths
    res = tmp_path / "resources"
    (res / "backend").mkdir(parents=True)
    (res / "decoder").mkdir()
    exe = res / "backend" / "vrb.exe"
    exe.write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe))
    assert paths.compute_paths({}).bundle_root == res.resolve()
    # negative control: decoder beside the exe wins; VALOHUB_BUNDLE override wins
    (res / "backend" / "decoder").mkdir()
    assert paths.compute_paths({}).bundle_root == (res / "backend").resolve()
    import os
    assert paths.compute_paths({"VALOHUB_BUNDLE": str(tmp_path)}).bundle_root == Path(os.path.abspath(tmp_path))
