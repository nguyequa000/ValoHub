"""Hermetic tests for store.py path wiring (legacy equality, desktop layout, patch semantics)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.replay import p2_identity, store

REPO = Path(__file__).resolve().parents[2]

PROBE = (
    "import json\n"
    "from bot.replay import store\n"
    "print(json.dumps({k: str(getattr(store, k)) for k in "
    "('ROOT','DATA_ROOT','DATA_REPLAYS_DIR','ORIGINALS_DIR','WORK_DIR','TMP_DIR')}))\n"
)


def probe(env_extra):
    env = {k: v for k, v in os.environ.items() if k not in ("VALOHUB_HOME", "VALOHUB_DESKTOP")}
    env.update(env_extra)
    out = subprocess.run([sys.executable, "-c", PROBE], cwd=REPO, env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout.strip().splitlines()[-1])


class LegacyEquality(unittest.TestCase):
    def test_legacy_values_match_old_formulas(self):
        root = Path(store.__file__).resolve().parents[2]
        if os.environ.get("VALOHUB_HOME") or os.environ.get("VALOHUB_DESKTOP"):
            self.skipTest("desktop env set")
        self.assertEqual(store.ROOT, root)
        self.assertEqual(store.DATA_ROOT, root)
        self.assertEqual(store.DATA_REPLAYS_DIR, root / "data" / "replays")
        self.assertEqual(store.ORIGINALS_DIR, root / "replays")
        self.assertEqual(store.WORK_DIR, root / "VALOPLANT_WORK")
        self.assertEqual(store.TMP_DIR, root / "VALOPLANT_WORK" / "tmp")
        self.assertEqual(store.STATIC_REPLAYS_DIR, root / "bot" / "web" / "static" / "replays_app")


class DesktopLayout(unittest.TestCase):
    def test_desktop_layout_under_home(self):
        with tempfile.TemporaryDirectory() as home:
            got = probe({"VALOHUB_HOME": home})
            home_p = Path(os.path.abspath(home))
            self.assertEqual(Path(got["DATA_ROOT"]), home_p)
            self.assertEqual(Path(got["DATA_REPLAYS_DIR"]), home_p / "data" / "replays")
            self.assertEqual(Path(got["ORIGINALS_DIR"]), home_p / "replays")
            self.assertEqual(Path(got["WORK_DIR"]), home_p / "VALOPLANT_WORK")
            self.assertEqual(Path(got["TMP_DIR"]), home_p / "VALOPLANT_WORK" / "tmp")
            self.assertEqual(Path(got["ROOT"]), REPO)  # code root unchanged
            self.assertEqual(os.listdir(home), [])      # import creates nothing

    def test_negative_control_legacy_differs_from_desktop(self):
        with tempfile.TemporaryDirectory() as home:
            self.assertNotEqual(probe({})["DATA_REPLAYS_DIR"], probe({"VALOHUB_HOME": home})["DATA_REPLAYS_DIR"])


class RelAndResolve(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        (self.base / "data").mkdir()
        (self.base / "code").mkdir()
        (self.base / "data" / "replays").mkdir()
        (self.base / "data" / "replays" / "a.vrf").write_bytes(b"x")
        (self.base / "code" / "replays").mkdir()
        (self.base / "code" / "replays" / "b.vrf").write_bytes(b"y")

    def test_rel_source_prefers_data_root_then_root_then_absolute(self):
        with mock.patch.object(store, "DATA_ROOT", self.base / "data"), mock.patch.object(store, "ROOT", self.base / "code"):
            self.assertEqual(store.rel_source(self.base / "data" / "replays" / "a.vrf"), "replays/a.vrf")
            self.assertEqual(store.rel_source(self.base / "code" / "replays" / "b.vrf"), "replays/b.vrf")
            outside = Path(tempfile.gettempdir()) / "zz" / "c.vrf"
            self.assertEqual(store.rel_source(outside), outside.as_posix())

    def test_resolve_source_order_and_patch_semantics(self):
        data, code = self.base / "data", self.base / "code"
        with mock.patch.object(store, "DATA_ROOT", data), mock.patch.object(store, "ROOT", code):
            # ROOT patched away from the computed root -> ROOT first
            self.assertEqual(store.resolve_source("replays/b.vrf"), code / "replays" / "b.vrf")
            self.assertEqual(store.resolve_source("replays/a.vrf"), data / "replays" / "a.vrf")
            self.assertEqual(store.resolve_source("replays/none.vrf"), code / "replays" / "none.vrf")
        with mock.patch.object(store, "DATA_ROOT", data):
            self.assertEqual(store.resolve_source("replays/a.vrf"), data / "replays" / "a.vrf")
        absolute = self.base / "data" / "replays" / "a.vrf"
        self.assertEqual(store.resolve_source(str(absolute)), absolute)

    def test_identity_mtime_traversal_negative(self):
        outside = self.base / "outside.vrf"
        outside.write_bytes(b"z")
        with mock.patch.object(store, "DATA_ROOT", self.base / "data"), mock.patch.object(store, "ROOT", self.base / "code"):
            ok = p2_identity._start_anchor({"source": "replays/a.vrf"})
            bad = p2_identity._start_anchor({"source": "../outside.vrf"})
        self.assertIsNotNone(ok)   # control: in-root source resolves
        self.assertIsNone(bad)     # traversal rejected


class RecordPrecedence(unittest.TestCase):
    def test_bundle_record_wins_else_work_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            work = tmp / "work"
            work.mkdir()
            (work / "parser-commit.txt").write_text("from=work\n")
            rec = tmp / "decoder" / "parser-commit.txt"
            rec.parent.mkdir()
            rec.write_text("from=bundle\n")
            loc = mock.Mock(source="bundle", record=rec, decoder_dir=tmp / "decoder" / "net10.0")
            with mock.patch.object(store, "WORK_DIR", work):
                with mock.patch.object(store._paths, "resolve_decoder", return_value=loc):
                    self.assertEqual(store._parser_commit_text(), "from=bundle")
                    self.assertEqual(store.parser_bin_dir(), tmp / "decoder" / "net10.0")
                loc.source = "legacy"
                with mock.patch.object(store._paths, "resolve_decoder", return_value=loc):
                    self.assertEqual(store._parser_commit_text(), "from=work")


if __name__ == "__main__":
    unittest.main()
