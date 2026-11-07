from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from bot.replay.tools import update_parser


class DensityGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _export(self, name: str, *, movement_per_round: int, events_per_round: int,
                source_sha: str = "fixture-sha", outcomes: bool = True) -> Path:
        raw = self.root / name
        raw.mkdir()
        (raw / "manifest.json").write_text(
            json.dumps({
                "replay_build": "++Ares-Core+release-13.06",
                "source_sha256": source_sha,
                "parse_status": "completed",
            }),
            encoding="utf-8",
        )
        event_rows = []
        movement_rows = []
        for number, (start, end) in enumerate(((0, 1000), (1001, 2000)), 1):
            for index in range(events_per_round):
                event_rows.append({"type": "rpc_received", "time_ms": start + index + 1})
            if outcomes:
                event_rows.append({
                    "type": "round_outcome",
                    "time_ms": end,
                    "round_number": number,
                })
            for index in range(movement_per_round):
                movement_rows.append({"type": "movement", "time_ms": start + index + 1})
        (raw / "events.ndjson").write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in event_rows),
            encoding="utf-8",
        )
        (raw / "movement.ndjson").write_text(
            "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in movement_rows),
            encoding="utf-8",
        )
        return raw

    def test_good_exact_export_is_ready(self):
        good = self._export("good", movement_per_round=40, events_per_round=20)
        baseline_path = self.root / "baseline.json"
        baseline = update_parser.create_baseline(good, baseline_path)
        verdict = update_parser.assess_decode_density(good, baseline=baseline)
        self.assertFalse(verdict["suspect"])
        self.assertIsNone(verdict["reason"])
        self.assertEqual([row["movementSamples"] for row in verdict["profile"]["rounds"]], [40, 40])

    def test_truncated_transform_is_suspect_not_ready(self):
        good = self._export("good", movement_per_round=40, events_per_round=20)
        broken = self._export("broken", movement_per_round=2, events_per_round=1)
        baseline = update_parser.create_baseline(good, self.root / "baseline.json")
        verdict = update_parser.assess_decode_density(broken, baseline=baseline)
        self.assertTrue(verdict["suspect"])
        self.assertIn("round 1 movement", verdict["reason"])
        self.assertIn("baseline 40", verdict["reason"])

    def test_missing_round_outcomes_is_suspect(self):
        good = self._export("good", movement_per_round=40, events_per_round=20)
        broken = self._export("broken", movement_per_round=40, events_per_round=20, outcomes=False)
        baseline = update_parser.create_baseline(good, self.root / "baseline.json")
        verdict = update_parser.assess_decode_density(broken, baseline=baseline)
        self.assertTrue(verdict["suspect"])
        self.assertIn("round_outcome", verdict["reason"])


class RequeueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.data = self.root / "data"
        self.source = self.root / "source.vrf"
        self.source.write_bytes(b"fixture")

    def tearDown(self):
        self.temp.cleanup()

    def _manifest(self, replay_id: str, **fields) -> None:
        directory = self.data / replay_id
        directory.mkdir(parents=True)
        value = {
            "id": replay_id,
            "status": "failed",
            "branch": "++Ares-Core+release-13.07",
            "notes": "unsupported branch transform for patch 13.07",
            "source": str(self.source),
        }
        value.update(fields)
        (directory / "manifest.json").write_text(json.dumps(value), encoding="utf-8")

    def test_filter_queue_and_dedupe_per_commit(self):
        self._manifest("eligible")
        self._manifest("header", notes="InvalidSeek in replay header")
        self._manifest("old", branch="++Ares-Core+release-12.99")
        self._manifest("ready", status="ready")
        supported = ["++Ares-Core+release-13.07"]
        candidates = update_parser.list_requeue_candidates(
            data_root=self.data,
            supported=supported,
            parser_commit="commit-1",
        )
        self.assertEqual([item["id"] for item in candidates], ["eligible"])
        calls = []

        def post(url: str, replay_id: str) -> dict:
            calls.append((url, replay_id))
            return {"jobId": "job-1", "id": replay_id}

        queued = update_parser.requeue_after_update(
            candidates,
            parser_commit="commit-1",
            api_base="http://127.0.0.1:18793",
            post=post,
        )
        self.assertEqual([item["id"] for item in queued], ["eligible"])
        self.assertEqual(calls, [("http://127.0.0.1:18793", "eligible")])
        second = update_parser.list_requeue_candidates(
            data_root=self.data,
            supported=supported,
            parser_commit="commit-1",
        )
        self.assertEqual(second, [])


class UpdateFlowTests(unittest.TestCase):
    def test_overlay_conflict_stops_before_build_or_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            attempt = Path(temp) / "attempt"
            candidate = attempt / "vendorValorantReplayParser"

            def fake_sync(path: Path) -> None:
                path.mkdir(parents=True)

            conflict = subprocess.CalledProcessError(1, ["git", "apply", "--check"])
            with (
                mock.patch.object(update_parser, "ROOT", Path(temp)),
                mock.patch.object(update_parser, "_attempt_dir", return_value=attempt),
                mock.patch.object(update_parser, "_sync_upstream_to_candidate", side_effect=fake_sync),
                mock.patch.object(update_parser, "_run", side_effect=conflict) as run,
            ):
                with self.assertRaises(subprocess.CalledProcessError):
                    update_parser._prepare_candidate("a" * 40, "b" * 64)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0][:3], ["git", "apply", "--check"])
            self.assertTrue(candidate.is_dir())

    def test_help_documents_required_modes(self):
        help_text = update_parser.build_parser().format_help()
        self.assertIn("--dry-run", help_text)
        self.assertIn("--no-pull", help_text)
        self.assertIn("--force", help_text)
        self.assertIn("--requeue-api", help_text)
        self.assertIn("density-selftest", help_text)

    def test_dry_run_never_prepares_or_publishes_candidate(self):
        with (
            mock.patch.object(update_parser, "_decoder"),
            mock.patch.object(update_parser, "_require"),
            mock.patch.object(update_parser, "_sha256", return_value="b" * 64),
            mock.patch.object(update_parser, "_git_head", return_value="a" * 40),
            mock.patch.object(update_parser, "list_requeue_candidates", return_value=[]),
            mock.patch.object(update_parser, "_prepare_candidate") as prepare,
            mock.patch.object(update_parser, "_publish_candidate") as publish,
        ):
            self.assertEqual(update_parser.main(["--dry-run", "--no-pull"]), 0)
        prepare.assert_not_called()
        publish.assert_not_called()


if __name__ == "__main__":
    unittest.main()
