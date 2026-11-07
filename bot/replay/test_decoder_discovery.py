"""Decoder discovery: bundle/env win, legacy constants stay patchable, registry fallback."""
import json
import sys
from pathlib import Path

import pytest

from bot.replay import paths, pipeline


@pytest.fixture
def clean_env(monkeypatch):
    for k in ("VALOHUB_BUNDLE", "VALO_DOTNET", "VALO_DECODER_DIR", "VALOHUB_HOME", "VALOHUB_DESKTOP"):
        monkeypatch.delenv(k, raising=False)
    paths.get_paths(refresh=True)
    yield monkeypatch
    monkeypatch.undo()
    paths.get_paths(refresh=True)


def _fake_bundle(root: Path, branches=("++Ares-Core+release-13.06",)) -> Path:
    (root / "runtime" / "dotnet").mkdir(parents=True)
    (root / "runtime" / "dotnet" / "dotnet.exe").write_bytes(b"x")
    (root / "decoder" / "net10.0").mkdir(parents=True)
    (root / "decoder" / "net10.0" / "CliReader.dll").write_bytes(b"x")
    (root / "decoder" / "registered-branches.json").write_text(json.dumps(list(branches)))
    return root


def test_bundle_wins_over_patched_constants(clean_env, tmp_path):
    bundle = _fake_bundle(tmp_path / "b")
    legacy_dotnet = tmp_path / "legacy" / "dotnet.exe"
    legacy_dotnet.parent.mkdir()
    legacy_dotnet.write_bytes(b"x")
    clean_env.setenv("VALOHUB_BUNDLE", str(bundle))
    paths.get_paths(refresh=True)
    clean_env.setattr(pipeline, "DOTNET_PATH", legacy_dotnet)
    dotnet, dll = pipeline.find_decoder()
    assert dotnet == bundle / "runtime" / "dotnet" / "dotnet.exe"
    assert dll == bundle / "decoder" / "net10.0" / "CliReader.dll"
    assert pipeline.decoder_info()["decoderSource"] == "bundle"


def test_env_overrides_bundle(clean_env, tmp_path):
    bundle = _fake_bundle(tmp_path / "b")
    other = tmp_path / "e"
    other.mkdir()
    (other / "dotnet.exe").write_bytes(b"x")
    (other / "CliReader.dll").write_bytes(b"x")
    clean_env.setenv("VALOHUB_BUNDLE", str(bundle))
    clean_env.setenv("VALO_DOTNET", str(other / "dotnet.exe"))
    clean_env.setenv("VALO_DECODER_DIR", str(other))
    paths.get_paths(refresh=True)
    assert pipeline.find_decoder() == (other / "dotnet.exe", other / "CliReader.dll")
    assert pipeline.decoder_info()["decoderSource"] == "env"


def test_patched_constants_honoured_without_bundle(clean_env, tmp_path):
    dotnet = tmp_path / "dotnet.exe"
    dotnet.write_bytes(b"x")
    cli = tmp_path / "bin"
    (cli / "net10.0").mkdir(parents=True)
    (cli / "net10.0" / "CliReader.dll").write_bytes(b"x")
    clean_env.setattr(pipeline, "DOTNET_PATH", dotnet)
    clean_env.setattr(pipeline, "CLI_BIN_DIR", cli)
    clean_env.setattr(pipeline, "_decoder_location", lambda: None)
    assert pipeline.find_decoder() == (dotnet, cli / "net10.0" / "CliReader.dll")
    # negative control: constants pointing nowhere -> nothing found
    clean_env.setattr(pipeline, "DOTNET_PATH", tmp_path / "nope.exe")
    clean_env.setattr(pipeline, "CLI_BIN_DIR", tmp_path / "nope")
    assert pipeline.find_decoder() == (None, None)


def test_registry_json_fallback_and_message(clean_env, tmp_path):
    bundle = _fake_bundle(tmp_path / "b", ("++Ares-Core+release-13.06", "++Ares-Core+release-13.09"))
    clean_env.setenv("VALOHUB_BUNDLE", str(bundle))
    paths.get_paths(refresh=True)
    clean_env.setattr(pipeline, "TRANSFORMS_DIR", tmp_path / "missing-src")
    assert pipeline.supported_branches() == ["++Ares-Core+release-13.06", "++Ares-Core+release-13.09"]
    # negative control: no registry and no source tree -> empty
    clean_env.delenv("VALOHUB_BUNDLE")
    paths.get_paths(refresh=True)
    assert pipeline.supported_branches() == []
    msg = pipeline.decoder_not_built_message()
    assert "VALO_DECODER_DIR" in msg and "runtime/dotnet/dotnet.exe" in msg and "Fix:" in msg


def test_source_tree_scan_still_used(clean_env, tmp_path):
    tdir = tmp_path / "t"
    tdir.mkdir()
    (tdir / "A.cs").write_text('x = "++Ares-Core+release-11.07";')
    clean_env.setattr(pipeline, "TRANSFORMS_DIR", tdir)
    assert pipeline.supported_branches() == ["++Ares-Core+release-11.07"]


def test_source_resolution_guard(clean_env, tmp_path):
    assert pipeline._resolve_source("replays/x.vrf") is not None
