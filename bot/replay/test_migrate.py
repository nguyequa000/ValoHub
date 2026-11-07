"""Hermetic tests for bot.replay.migrate (synthetic trees under tmp dirs only)."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from bot.replay import migrate


def snapshot(root: Path):
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns)
            for p in root.rglob("*") if p.is_file()}


class MigrateTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.src = base / "legacy"
        self.dst = base / "home"
        (self.src / "data" / "replays" / "abc" / "viewer").mkdir(parents=True)
        (self.src / "data" / "replays" / "abc" / "manifest.json").write_text('{"a":1}')
        (self.src / "data" / "replays" / "abc" / "viewer" / "match.json").write_text("m" * 100)
        (self.src / "replays").mkdir()
        (self.src / "replays" / "x.vrf").write_bytes(b"V" * 5000)
        (self.src / "replays" / "ignore.txt").write_text("no")
        (self.src / "VALOPLANT_WORK").mkdir()
        (self.src / "VALOPLANT_WORK" / "parser-commit.txt").write_text("c")

    def test_fresh_copy_scope_and_report(self):
        before = snapshot(self.src)
        calls = []
        rep = migrate.import_legacy(self.src, self.dst, progress=lambda *a: calls.append(a))
        self.assertEqual(sorted(rep["copied"]), ["data/replays/abc/manifest.json",
                         "data/replays/abc/viewer/match.json", "replays/x.vrf"])
        self.assertFalse((self.dst / "replays" / "ignore.txt").exists())
        self.assertFalse((self.dst / "VALOPLANT_WORK" / "parser-commit.txt").exists())
        self.assertEqual((self.dst / "replays" / "x.vrf").read_bytes(), b"V" * 5000)
        self.assertEqual(rep["bytes"], 5000 + 100 + 7)
        self.assertEqual(calls[-1][0], calls[-1][1])
        mig = json.loads((self.dst / "VALOPLANT_WORK" / "migration.json").read_text())
        for key in ("source", "started", "finished", "copied", "skipped", "conflicts", "bytes"):
            self.assertIn(key, mig)
        self.assertEqual(snapshot(self.src), before)  # source untouched
        self.assertEqual(list(self.dst.rglob("*.part")), [])

    def test_rerun_is_idempotent(self):
        migrate.import_legacy(self.src, self.dst)
        rep = migrate.import_legacy(self.src, self.dst)
        self.assertEqual(rep["copied"], [])
        self.assertEqual(len(rep["skipped"]), 3)
        self.assertEqual(rep["bytes"], 0)

    def test_resume_after_interruption(self):
        target = self.dst / "replays"
        target.mkdir(parents=True)
        (target / "x.vrf.part").write_bytes(b"V" * 10)       # interrupted copy
        rep = migrate.import_legacy(self.src, self.dst)
        self.assertIn("replays/x.vrf", rep["copied"])
        self.assertEqual((target / "x.vrf").read_bytes(), b"V" * 5000)
        self.assertFalse((target / "x.vrf.part").exists())

    def test_conflict_kept_and_reported(self):
        (self.dst / "replays").mkdir(parents=True)
        (self.dst / "replays" / "x.vrf").write_bytes(b"DIFFERENT")
        rep = migrate.import_legacy(self.src, self.dst)
        self.assertEqual(rep["conflicts"], ["replays/x.vrf"])
        self.assertEqual((self.dst / "replays" / "x.vrf").read_bytes(), b"DIFFERENT")
        self.assertEqual(migrate.main(["--from", str(self.src), "--home", str(self.dst), "--json"]),
                         migrate.EXIT_CONFLICT)

    def test_same_and_nested_refused(self):
        with self.assertRaises(migrate.MigrationRefused):
            migrate.import_legacy(self.src, self.src)
        with self.assertRaises(migrate.MigrationRefused):
            migrate.import_legacy(self.src, self.src / "inside")
        with self.assertRaises(migrate.MigrationRefused):
            migrate.import_legacy(self.src / "data", self.src)
        self.assertEqual(migrate.main(["--from", str(self.src), "--home", str(self.src)]), migrate.EXIT_REFUSED)
        self.assertFalse((self.src / "inside").exists())

    def test_dry_run_writes_nothing(self):
        plan = migrate.plan_import(self.src, self.dst)
        self.assertEqual((plan["files"], plan["vrf"], plan["stores"], plan["to_copy"]), (3, 1, ["abc"], 3))
        self.assertFalse(self.dst.exists())

    def test_negative_control_detects_modified_source(self):
        before = snapshot(self.src)
        migrate.import_legacy(self.src, self.dst)
        (self.src / "replays" / "x.vrf").write_bytes(b"changed")
        self.assertNotEqual(snapshot(self.src), before)  # the snapshot check can fail


if __name__ == "__main__":
    unittest.main()
