"""Real-replay tests for the stdlib container index (no packet decoder needed).

Fixtures are the gitignored ``.vrf`` originals in ``replays/``: the tests read
them in place and never copy or write them. The 13.06 match (Haven/Triad,
18 rounds) is fully decodable; the 11.07 match (Sunset/Juliett, 21 rounds) has
the pre-12.x header whose CliReader packet decode fails — the container index
must still work on it, which is the point of keeping this reader.
"""
import unittest

from bot.replay import container_index as ci
from bot.replay import store

REPLAY_1306 = store.ORIGINALS_DIR / "bdb6b4ab-fa31-4e51-b77a-35e3a1e7a3c2.vrf"
REPLAY_1107 = store.ORIGINALS_DIR / "2f7c79fb-5fda-4a8f-89a1-7fb27d9ac587.vrf"
FIXTURE_MISSING = "replay fixture missing (gitignored replays/*.vrf must be present)"


def assert_index_shape(case, result):
    """Invariants that must hold for any indexed replay."""
    rounds = result["rounds"]
    case.assertTrue(rounds)
    starts = [r["startMs"] for r in rounds]
    case.assertEqual(starts, sorted(starts))
    case.assertEqual(rounds[0]["startMs"], min(starts))
    case.assertEqual([r["roundNumber"] for r in rounds], [r["roundIndex"] + 1 for r in rounds])
    for round_ in rounds:
        case.assertEqual(round_["defenderWin"], bool(round_["defuseTimesMs"]))
        for time_ms in round_["plantTimesMs"] + round_["defuseTimesMs"]:
            case.assertLessEqual(round_["startMs"], time_ms)
            case.assertLessEqual(time_ms, round_["endMs"])
        case.assertEqual([d["timeMs"] for d in round_["deaths"]],
                         sorted(d["timeMs"] for d in round_["deaths"]))
    counts = result["eventCounts"]
    case.assertEqual(sum(r["deathCount"] for r in rounds), counts.get("characterDeath", 0))
    case.assertEqual(sum(r["ultimateCount"] for r in rounds),
                     counts.get("characterUltimateUsed", 0))
    case.assertEqual(len(result["sideSwitchTimesMs"]), counts.get("switchTeams", 0))


class Haven1306Tests(unittest.TestCase):
    @unittest.skipUnless(REPLAY_1306.is_file(), FIXTURE_MISSING)
    def test_index_1306_haven(self):
        result = ci.index_replay(REPLAY_1306)
        self.assertEqual(len(result["rounds"]), 18)
        self.assertEqual(result["eventCounts"]["roundStarted"], 18)
        self.assertEqual(result["eventCounts"]["spikePlanted"], 11)
        self.assertEqual(result["eventCounts"]["spikeDefused"], 1)
        self.assertEqual(result["undecodedPayloadCount"], 0)
        self.assertEqual(result["container"]["branch"], "++Ares-Core+release-13.06")
        self.assertEqual(result["container"]["mapPath"], "/Game/Maps/Triad/Triad")
        defused = [r for r in result["rounds"] if r["defenderWin"]]
        self.assertEqual(len(defused), 1)
        self.assertEqual(len(defused[0]["defuseTimesMs"]), 1)
        assert_index_shape(self, result)


class Sunset1107Tests(unittest.TestCase):
    @unittest.skipUnless(REPLAY_1107.is_file(), FIXTURE_MISSING)
    def test_index_1107_sunset_without_packet_decode(self):
        result = ci.index_replay(REPLAY_1107)
        self.assertEqual(len(result["rounds"]), 21)
        self.assertEqual(result["eventCounts"]["roundStarted"], 21)
        self.assertEqual(result["undecodedPayloadCount"], 0)
        self.assertEqual(result["container"]["branch"], "++Ares-Core+release-11.07")
        self.assertEqual(result["container"]["mapPath"], "/Game/Maps/Juliett/Juliett")
        assert_index_shape(self, result)


class NotAContainerTests(unittest.TestCase):
    def test_non_vrf_file_is_rejected(self):
        with self.assertRaises(ValueError):
            ci.index_replay(store.ORIGINALS_DIR / "README.md")


if __name__ == "__main__":
    unittest.main()
