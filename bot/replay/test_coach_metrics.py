"""Tests for the P4-A coach metrics (stdlib only).

Two sources of truth:

* a synthetic fixture store written into a temp dir at test time (known-by-hand
  trades, first deaths, spikes, sides and loss involvement), and
* the real Haven store ``data/replays/bad1543033d11396`` (18 rounds, 10 slots).

The load-bearing assertion is the P4-G grounding contract: **every** ref of
**every** finding for **every** slot resolves to an existing event / frame sample
in the store it came from.
"""
import json
import re
import tempfile
import unittest
from pathlib import Path

from bot.replay import coach_metrics as coach

STORE = Path(__file__).resolve().parents[2] / "data" / "replays" / "bad1543033d11396"
TICK = 100
MAP = {"name": "Haven", "path": "/Game/Maps/Triad/Triad",
       "xMultiplier": 7.5e-05, "yMultiplier": -7.5e-05,
       "xScalarToAdd": 1.09345, "yScalarToAdd": 0.642728}
ROSTER = [("P1", 0), ("P2", 0), ("P3", 0), ("P4", 0), ("P5", 0),
          ("P6", 1), ("P7", 1), ("P8", 1), ("P9", 1), ("P10", 1)]
AGENTS = {"P1": "Sova", "P2": "Jett", "P3": "Omen", "P4": "Phoenix", "P5": "Chamber",
          "P6": "Sova", "P7": "Clove", "P8": "Jett", "P9": "Phoenix", "P10": "Cypher"}

# --- fixture plan (round-relative ms) -------------------------------------- #
# r1: plant 5000, P6 kills P1 at 6000 -> P2 kills P6 at 6500 (trade of P1's death)
#     P7 kills P4 at 7000 -> P5 kills P7 at 7100 (trade of P4's death)
#     P1 kills P8 at 8000; winner team 1; team 1 attacks.
# r2: P6 kills P1 at 1000 (never traded), last kill 3000 -> P1 died before the
#     round was decided; winner team 1; team 1 attacks.
# r3: no kills, no plant; winner team 0 (P1's team); team 0 attacks.
# r4: plant 2000 by P2; first contact 2500 (P5 kills P7); P1 ults at 9000 (6.5 s
#     after first contact); P6 defuses at 11000 -> winner team 1; team 0 attacks.
# r5: P7 kills P1 at 500 (never traded) == the deciding kill; winner team 1.
HAND_KILLS = {1: [(6000, "P6", "P1"), (6500, "P2", "P6"), (7000, "P7", "P4"),
                  (7100, "P5", "P7"), (8000, "P1", "P8"), (9000, "P3", "P9")],
              2: [(1000, "P6", "P1"), (2000, "P7", "P3"), (3000, "P10", "P5")],
              3: [],
              4: [(2500, "P5", "P7")],
              5: [(500, "P7", "P1")]}
POSITIONS = {-2465: None}
P1_POS = {1: (-2465, -9166), 2: (-2465, -8166), 3: (-2465, -9166),
          4: (1535, -4166), 5: (-2465, -9166)}
ROUND_PLAN = {1: {"frames": 100, "plant": (5000, "P1", -2465, -9166), "extra": []},
              2: {"frames": 60, "plant": None, "extra": []},
              3: {"frames": 40, "plant": None, "extra": []},
              4: {"frames": 120, "plant": (2000, "P2", -2465, -9166),
                  "defuse": (11000, "P6"),
                  "extra": [{"t": 9000, "type": "ult", "actor": "P1"}]},
              5: {"frames": 40, "plant": None, "extra": []}}
ROUND_META = {1: {"winner": 1, "endType": "elimination", "attackerTeam": 1},
              2: {"winner": 1, "endType": "elimination", "attackerTeam": 1},
              3: {"winner": 0, "endType": "elimination", "attackerTeam": 0},
              4: {"winner": 1, "endType": "defuse", "attackerTeam": 0},
              5: {"winner": 1, "endType": "elimination", "attackerTeam": 0}}


def _event(t, kind, actor=None, target=None):
    return {"t": t, "type": kind, "actor": actor, "target": target,
            "weapon": None, "ability": None, "confidence": "replay"}


def _round_document(number, frames, kills, positions, plant=None, defuse=None, extra=()):
    start = (number - 1) * 100000
    events = [_event(t, "kill", actor, target) for t, actor, target in kills]
    if plant:
        events.append(_event(plant[0], "plant", plant[1]))
    if defuse:
        events.append(_event(defuse[0], "defuse", defuse[1]))
    events.extend(dict(item) for item in extra)
    events.sort(key=lambda item: (item["t"], item["type"]))
    players = {}
    for slot, _team in ROSTER:
        x, y = positions[slot]
        deaths = [t for t, _actor, target in kills if target == slot]
        alive = [0 if any(t <= i * TICK for t in deaths) else 1 for i in range(frames)]
        track = {"x": [x] * frames, "y": [y] * frames, "yaw": [0.0] * frames, "alive": alive}
        if slot != "P8":
            track["hp"] = [100 if a else 0 for a in alive]
        players[slot] = track
    return {"schema": "valoplant_round/v2", "n": number, "startMs": start,
            "endMs": start + (frames - 1) * TICK, "tickMs": TICK, "frames": frames,
            "players": players, "events": events,
            "spike": {"plantT": plant[0] if plant else None,
                      "plantX": plant[2] if plant else None,
                      "plantY": plant[3] if plant else None,
                      "defuseT": defuse[0] if defuse else None, "site": None}}


def fixture_match(publish_sides=True):
    rounds = []
    for number in sorted(ROUND_PLAN):
        plan = ROUND_PLAN[number]
        entry = {"n": number, "startMs": (number - 1) * 100000,
                 "endMs": (number - 1) * 100000 + (plan["frames"] - 1) * TICK,
                 "winner": ROUND_META[number]["winner"],
                 "endType": ROUND_META[number]["endType"],
                 "kills": len(HAND_KILLS[number]),
                 "plants": 1 if plan["plant"] else 0,
                 "defuses": 1 if plan.get("defuse") else 0, "confidence": "replay"}
        if publish_sides:
            entry["attackerTeam"] = ROUND_META[number]["attackerTeam"]
        rounds.append(entry)
    return {"schema": "valoplant_match/v2", "id": "fixture", "patch": "13.06", "map": MAP,
            "players": [{"id": slot, "name": None, "agent": AGENTS[slot], "team": team,
                         "subject": None, "confidence": "loadout"} for slot, team in ROSTER],
            "rounds": rounds}


def fixture_rounds():
    docs = {}
    for number in sorted(ROUND_PLAN):
        plan = ROUND_PLAN[number]
        positions = {slot: P1_POS[number] if slot == "P1" else (-2465, -9166)
                     for slot, _team in ROSTER}
        docs[number] = _round_document(number, plan["frames"], HAND_KILLS[number], positions,
                                       plant=plan["plant"], defuse=plan.get("defuse"),
                                       extra=plan.get("extra", []))
    return docs


def write_store(directory, match, rounds):
    root = Path(directory)
    (root / "viewer" / "rounds").mkdir(parents=True, exist_ok=True)
    (root / "viewer" / "match.json").write_text(json.dumps(match), encoding="utf-8")
    for number, doc in rounds.items():
        (root / "viewer" / "rounds" / f"r{number:02d}.json").write_text(
            json.dumps(doc), encoding="utf-8")
    return str(root)


def all_refs(report):
    for finding in report["findings"]:
        for ref in finding["refs"]:
            yield finding, ref


class FixtureMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store_dir = write_store(cls._tmp.name, fixture_match(), fixture_rounds())
        cls.store = coach.load_store(cls.store_dir)
        cls.report = coach.player_report(cls.store_dir, "P1")
        cls.metrics = cls.report["metrics"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_round_and_death_metrics(self):
        metrics = self.metrics
        self.assertEqual(metrics["rounds"], 5)
        self.assertEqual(metrics["roundsWithDocument"], 5)
        self.assertEqual(metrics["kills"], 1)
        self.assertEqual(metrics["deaths"], 3)
        self.assertEqual((metrics["tradeableDeaths"], metrics["tradedDeaths"],
                          metrics["untradedDeaths"]), (3, 1, 2))
        self.assertAlmostEqual(metrics["untradedDeathRate"], 2 / 3)
        self.assertEqual(metrics["tradeWindowMs"], 5000)

    def test_first_death_and_first_blood(self):
        # P1 is the first kill victim of r1 (6000), r2 (1000) and r5 (500)
        self.assertEqual(self.metrics["firstDeaths"], 3)
        self.assertAlmostEqual(self.metrics["firstDeathRate"], 0.6)
        self.assertEqual(self.metrics["firstBloods"], 0)
        self.assertEqual(self.metrics["firstDeathsBySide"],
                         {"attack": 1, "defense": 2, "unknown": 0})

    def test_death_clusters_use_grid_and_region(self):
        metrics = self.metrics
        self.assertEqual(metrics["deathCells"], {"384,832": 2, "480,832": 1})
        self.assertEqual(metrics["topDeathCluster"]["count"], 2)
        region = metrics["topDeathRegion"]
        self.assertEqual(region["count"], 3)
        self.assertEqual(region["region"], [384, 768])
        self.assertEqual(region["size"], coach.REGION)
        self.assertEqual(region["cells"], [[384, 832], [480, 832]])
        self.assertAlmostEqual(region["share"], 1.0)
        self.assertEqual(region["rounds"], [1, 2, 5])
        self.assertEqual([ref["src"] for ref in region["refs"]], ["sample"] * 3)

    def test_util_timing(self):
        metrics = self.metrics
        self.assertEqual(metrics["utilEvents"], 1)
        self.assertEqual(metrics["utilByType"], {"ult": 1})
        self.assertEqual(metrics["utilMedianTMs"], 9000)
        self.assertEqual(metrics["utilMedianVsContactMs"], 9000 - 2500)
        self.assertEqual(metrics["utilAfterFirstContact"], 1)

    def test_post_plant_positioning(self):
        post = self.metrics["postPlant"]
        self.assertEqual(post["rounds"], 2)
        self.assertEqual(post["aliveAtPlant"], 2)
        self.assertEqual(post["deathsPostPlant"], 1)
        self.assertEqual(post["within1000"], 10)          # r1 while alive at the spike
        self.assertEqual(post["nearestDistance"], 0.0)
        self.assertGreater(post["meanDistance"], 5000)    # r4 was ~6.4 km away while alive
        self.assertLess(post["aliveFraction"], 1.0)

    def test_survival_by_side(self):
        sides = self.metrics["sides"]
        self.assertEqual(sides["attack"]["rounds"], 3)
        self.assertEqual(sides["attack"]["survived"], 2)
        self.assertEqual(sides["attack"]["deaths"], 1)
        self.assertAlmostEqual(sides["attack"]["survivalRate"], 2 / 3)
        self.assertEqual((sides["defense"]["rounds"], sides["defense"]["survived"],
                          sides["defense"]["deaths"]), (2, 0, 2))
        self.assertEqual(self.metrics["unknownSideRounds"], 0)

    def test_round_loss_involvement(self):
        metrics = self.metrics
        self.assertEqual(metrics["lostRounds"], 4)                 # r1, r2, r4, r5 (winner team 1)
        self.assertEqual(metrics["lossDeaths"], 3)                 # died in r1, r2, r5
        self.assertEqual(metrics["lossRoundsWithDeath"], 3)
        self.assertEqual(metrics["lossDeathsBeforeDecision"], 2)   # r1 (6000<9000), r2 (1000<3000)
        self.assertAlmostEqual(metrics["lossDeathRate"], 3 / 4)

    def test_rounds_table(self):
        table = self.metrics["roundsTable"]
        self.assertEqual([row["n"] for row in table], [1, 2, 3, 4, 5])
        self.assertEqual([row["side"] for row in table],
                         ["defense", "defense", "attack", "attack", "attack"])
        self.assertEqual([row["sideSource"] for row in table], ["replay"] * 5)
        self.assertEqual(table[0]["aliveAtPlant"], 1)
        self.assertEqual(table[3]["decidedAt"], 11000)             # defuse decides r4
        self.assertEqual(table[1]["decidedAt"], 3000)              # last kill decides r2

    def test_findings_cover_the_rubric_and_every_ref_resolves(self):
        kinds = {finding["kind"] for finding in self.report["findings"]}
        self.assertEqual(kinds, {"first_death", "untraded_death", "death_cluster",
                                 "util_timing", "post_plant", "survival_side",
                                 "loss_involvement"})
        ids = [finding["id"] for finding in self.report["findings"]]
        self.assertEqual(len(ids), len(set(ids)))
        seen = 0
        for finding in self.report["findings"]:
            self.assertTrue(finding["refs"], finding["id"])
            self.assertLessEqual(len(finding["refs"]), coach.MAX_REFS)
            self.assertIn(finding["severity"], ("info", "minor", "major"))
            self.assertTrue(finding["text"])
            self.assertIn("-P1", finding["id"])
            for ref in finding["refs"]:
                seen += 1
                self.assertTrue(coach.resolve_ref(self.store["match"], self.store["rounds"], ref),
                                f"{finding['id']} -> {ref}")
        self.assertGreater(seen, 10)

    def test_finding_severities_match_the_thresholds(self):
        by_kind = {finding["kind"]: finding for finding in self.report["findings"]}
        self.assertEqual(by_kind["first_death"]["severity"], "major")      # 3/5 >= 0.25
        self.assertEqual(by_kind["untraded_death"]["severity"], "major")   # 2/3 >= 0.50
        self.assertEqual(by_kind["death_cluster"]["severity"], "minor")    # share 1.0
        self.assertEqual(by_kind["util_timing"]["severity"], "minor")      # 6.5s after contact
        self.assertEqual(by_kind["loss_involvement"]["severity"], "info")  # only 2 early deaths
        self.assertIn("2 of 3", by_kind["untraded_death"]["text"])
        self.assertIn("3 of 5", by_kind["first_death"]["text"])

    def test_baseline_ranks_all_ten_players(self):
        baseline = self.report["baseline"]
        self.assertEqual(baseline["count"], 10)
        self.assertEqual([row["slot"] for row in baseline["players"]],
                         ["P1", "P2", "P3", "P4", "P5", "P6", "P7", "P8", "P9", "P10"])
        for key, entry in baseline["ranks"].items():
            self.assertIsNotNone(entry, key)
            self.assertGreaterEqual(entry["rank"], 1)
            self.assertIn(entry["better"], ("lower", "higher"))
            # "of" counts the players with a value for that metric (some rates are
            # undefined for a player who never died / never planted).
            measured = [row[key] for row in baseline["players"] if row[key] is not None]
            self.assertEqual(entry["of"], len(measured), key)
            self.assertLessEqual(entry["of"], 10)
            # competition ranking: 1 + how many peers are strictly better
            peers = [row[key] for row in baseline["players"]
                     if row["slot"] != "P1" and row[key] is not None]
            if entry["better"] == "lower":
                expected = 1 + sum(1 for value in peers if value < entry["value"])
            else:
                expected = 1 + sum(1 for value in peers if value > entry["value"])
            self.assertEqual(entry["rank"], expected, key)
        # Eight players never take a first death (0.0), P7 takes one (0.2) and P1 takes
        # three (0.6) -> P1 is the worst of the ten on this metric.
        self.assertEqual(baseline["ranks"]["firstDeathRate"]["rank"], 10)
        self.assertEqual(baseline["ranks"]["firstDeathRate"]["of"], 10)

    def test_deterministic_and_json_serialisable(self):
        again = coach.player_report(self.store_dir, "P1")
        self.assertEqual(json.dumps(again, sort_keys=True), json.dumps(self.report, sort_keys=True))
        json.dumps(self.report)

    def test_flat_and_normalized_layouts_agree(self):
        flat = Path(self._tmp.name) / "flat"
        (flat / "rounds").mkdir(parents=True)
        match, rounds = fixture_match(), fixture_rounds()
        (flat / "match.json").write_text(json.dumps(match), encoding="utf-8")
        for number, doc in rounds.items():
            (flat / "rounds" / f"r{number:02d}.json").write_text(json.dumps(doc), encoding="utf-8")
        flat_report = coach.player_report(str(flat), "P1")
        self.assertEqual(flat_report["metrics"]["roundsTable"], self.metrics["roundsTable"])
        self.assertEqual(flat_report["findings"], self.report["findings"])

    def test_missing_hp_array_is_handled_and_noted(self):
        self.assertNotIn("hp", self.store["rounds"][1]["players"]["P8"])
        self.assertTrue(any("no per-frame hp samples for P8" in note
                            for note in self.report["notes"]), self.report["notes"])

    def test_inferred_sides_when_attacker_team_is_absent(self):
        directory = tempfile.mkdtemp()
        store_dir = write_store(directory, fixture_match(publish_sides=False), fixture_rounds())
        report = coach.player_report(store_dir, "P1")
        sides = report["sideInference"]
        self.assertEqual(sides["publishedRounds"], [])
        self.assertEqual(set(sides["source"].values()), {"inferred"})
        # both planters are team 0 -> team 0 attacked, so P1 is on attack every round
        self.assertEqual(sides["halves"][0]["attackerTeamInferred"], 0)
        self.assertTrue(all(team == 0 for team in sides["rounds"].values()))
        self.assertEqual(report["metrics"]["sides"]["attack"]["rounds"], 5)
        self.assertEqual(report["metrics"]["roundsTable"][0]["sideSource"], "inferred")
        for finding, ref in all_refs(report):
            self.assertTrue(coach.resolve_ref(coach.load_store(store_dir)["match"],
                                              coach.load_store(store_dir)["rounds"], ref),
                            f"{finding['id']} -> {ref}")

    def test_no_hp_and_no_util_store_is_honest(self):
        match = {"id": "empty", "map": MAP, "players": [{"id": "P1", "team": 0},
                                                        {"id": "P2", "team": 1}],
                 "rounds": [{"n": 1, "winner": 0, "endType": "elimination"}]}
        rounds = {1: {"n": 1, "startMs": 0, "endMs": 900, "tickMs": TICK, "frames": 10,
                      "players": {"P1": {"x": [0] * 10, "y": [0] * 10, "alive": [1] * 10},
                                  "P2": {"x": [0] * 10, "y": [0] * 10, "alive": [1] * 10}},
                      "events": [], "spike": {"plantT": None}}}
        metrics = coach.compute_metrics(match, rounds, "P1")
        self.assertEqual(metrics["deaths"], 0)
        self.assertIsNone(metrics["untradedDeathRate"])
        self.assertIsNone(metrics["postPlant"]["meanDistance"])
        findings = coach.build_findings("P1", metrics, coach.compare_baseline({"P1": metrics}, "P1"),
                                        match, rounds)
        self.assertEqual([finding["kind"] for finding in findings], ["util_timing"])
        findings[0]["refs"] = findings[0]["refs"][:1]
        self.assertTrue(all(coach.resolve_ref(match, rounds, ref) for ref in findings[0]["refs"]))


class ReviveLossInvolvementTests(unittest.TestCase):
    """A revive agent (Clove) can die twice inside one lost round.

    Regression for the P4-G gate note 2: the ``loss_involvement`` text labelled
    ``lossDeaths / lostRounds`` as "% of the lost rounds", so a revive agent printed a
    share above 100%. The share must be the fraction of the team's lost rounds the
    player died in (never above 100%), with the death count kept as a plain count.
    """

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        match = fixture_match()
        rounds = fixture_rounds()
        # P1 revives: a second death inside r2 (a lost round) plus one in r4 (lost too,
        # where the unmodified fixture has P1 survive). 5 deaths over 4 lost rounds.
        extra = {2: (2600, "P9"), 4: (2600, "P8")}
        for number, (t, killer) in extra.items():
            rounds[number]["events"].append(_event(t, "kill", killer, "P1"))
            rounds[number]["events"].sort(key=lambda item: (item["t"], item["type"]))
        for meta in match["rounds"]:
            if meta["n"] in extra:
                meta["kills"] = sum(1 for event in rounds[meta["n"]]["events"]
                                    if event["type"] == "kill")
        cls.store_dir = write_store(cls._tmp.name, match, rounds)
        cls.store = coach.load_store(cls.store_dir)
        cls.report = coach.player_report(cls.store_dir, "P1")

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def loss_finding(self):
        by_kind = {finding["kind"]: finding for finding in self.report["findings"]}
        self.assertIn("loss_involvement", by_kind)
        return by_kind["loss_involvement"]

    def test_two_deaths_in_one_lost_round_and_the_share_stays_capped(self):
        metrics = self.report["metrics"]
        self.assertEqual(metrics["lostRounds"], 4)             # r1, r2, r4, r5
        self.assertEqual(metrics["lossRoundsWithDeath"], 4)    # died in every lost round
        self.assertEqual(metrics["lossDeaths"], 5)             # r2 and r4 now hold two each
        self.assertGreater(metrics["lossDeaths"], metrics["lossRoundsWithDeath"])
        # the ratio the finding used to print as a share: 5 / 4 = 125%
        self.assertGreater(metrics["lossDeathRate"], 1.0)
        text = self.loss_finding()["text"]
        self.assertIn("100% of the lost rounds", text)
        self.assertIn("5 deaths", text)
        for value in re.findall(r"(\d+)%", text):
            self.assertLessEqual(int(value), 100, text)

    def test_loss_refs_are_kept_and_still_resolve(self):
        finding = self.loss_finding()
        self.assertEqual(len(finding["refs"]), 5)              # one per death in a lost round
        for ref in finding["refs"]:
            self.assertTrue(coach.resolve_ref(self.store["match"], self.store["rounds"], ref),
                            f"{finding['id']} -> {ref}")


class TradeSemanticsTests(unittest.TestCase):
    """Mirrors bot/test_trade_detection_offline.py's expectations on viewer rounds."""

    def _metrics(self, deaths):
        # deaths: [(victim, killer, killer's avenger time offset or None)]
        kills = []
        for index, (victim, killer, avenged_after) in enumerate(deaths):
            kills.append(_event(10000 + index * 1000, "kill", killer, victim))
            if avenged_after is not None:
                kills.append(_event(10000 + index * 1000 + avenged_after, "kill",
                                    "AVENGER-%d" % index, killer))
        match = {"id": "trades", "map": MAP,
                 "players": [{"id": "P1", "team": 0}, {"id": "P6", "team": 1},
                             {"id": "P2", "team": 0}, {"id": "P7", "team": 1},
                             {"id": "AVENGER-0", "team": 0}, {"id": "AVENGER-1", "team": 0},
                             {"id": "AVENGER-2", "team": 0}],
                 "rounds": [{"n": 1, "winner": 0, "endType": "elimination"}]}
        doc = _round_document(1, 400, [(e["t"], e["actor"], e["target"]) for e in kills],
                              {slot: (0, 0) for slot, _ in ROSTER})
        doc["events"] = sorted(kills, key=lambda item: (item["t"], item["type"]))
        return coach.compute_metrics(match, {1: doc}, "P1")

    def test_trade_inside_the_window_and_untraded_outside_it(self):
        # P1 dies to P6 at 10000 and is avenged 4900 ms later -> traded.
        metrics = self._metrics([("P1", "P6", 4900)])
        self.assertEqual(metrics["tradedDeaths"], 1)
        self.assertEqual(metrics["untradedDeaths"], 0)

        # 5100 ms later -> outside the 5 s window -> untraded.
        metrics = self._metrics([("P1", "P6", 5100)])
        self.assertEqual(metrics["tradedDeaths"], 0)
        self.assertEqual(metrics["untradedDeaths"], 1)

    def test_enemy_kill_of_the_killer_is_not_a_trade_and_self_kills_are_ignored(self):
        kills = [_event(10000, "kill", "P6", "P1"),
                 _event(10500, "kill", "P7", "P6"),      # P7 is on the killer's team: no trade
                 _event(11000, "kill", "P1", "P1")]      # self kill: neither kill nor death
        match = {"id": "trades", "map": MAP,
                 "players": [{"id": "P1", "team": 0}, {"id": "P6", "team": 1},
                             {"id": "P7", "team": 1}],
                 "rounds": [{"n": 1, "winner": 0, "endType": "elimination"}]}
        doc = _round_document(1, 400, [(k["t"], k["actor"], k["target"]) for k in kills],
                              {slot: (0, 0) for slot, _ in ROSTER})
        doc["events"] = sorted(kills, key=lambda item: (item["t"], item["type"]))
        metrics = coach.compute_metrics(match, {1: doc}, "P1")
        self.assertEqual(metrics["deaths"], 2)              # real death + self kill
        self.assertEqual(metrics["tradeableDeaths"], 1)     # the self kill is not tradeable
        self.assertEqual(metrics["untradedDeaths"], 1)
        # P6's death at the hands of a teammate is not a trade of P1's death
        self.assertEqual(metrics["killsThatWereTrades"], 0)


class ResolveRefTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory()
        cls.store_dir = write_store(cls._tmp.name, fixture_match(), fixture_rounds())
        cls.store = coach.load_store(cls.store_dir)
        cls.match, cls.rounds = cls.store["match"], cls.store["rounds"]

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_real_refs_resolve(self):
        self.assertTrue(coach.resolve_ref(self.match, self.rounds,
                                          {"src": "event", "round": 1, "t": 6000,
                                           "type": "kill", "actor": "P6", "target": "P1"}))
        self.assertTrue(coach.resolve_ref(self.match, self.rounds,
                                          {"src": "sample", "round": 1, "t": 5000,
                                           "slot": "P1", "x": -2465, "y": -9166, "alive": 1}))
        self.assertTrue(coach.resolve_ref(self.match, self.rounds,
                                          {"src": "round", "round": 1, "t": 9900,
                                           "winner": 1, "endType": "elimination"}))

    def test_tampered_refs_are_rejected(self):
        cases = [
            {"src": "event", "round": 1, "t": 6001, "type": "kill", "actor": "P6", "target": "P1"},
            {"src": "event", "round": 1, "t": 6000, "type": "kill", "actor": "P1", "target": "P6"},
            {"src": "event", "round": 9, "t": 6000, "type": "kill", "actor": "P6", "target": "P1"},
            {"src": "sample", "round": 1, "t": 5050, "slot": "P1", "x": -2465, "y": -9166},
            {"src": "sample", "round": 1, "t": 5000, "slot": "P1", "x": -2464, "y": -9166},
            {"src": "sample", "round": 1, "t": 5000, "slot": "P1", "x": -2465, "y": -9166,
             "alive": 0},
            {"src": "sample", "round": 1, "t": 990000, "slot": "P1", "x": -2465, "y": -9166},
            {"src": "round", "round": 1, "t": 9900, "winner": 0, "endType": "elimination"},
            {"src": "round", "round": 1, "t": 9899, "winner": 1, "endType": "elimination"},
            {"src": "prose", "round": 1, "t": 6000},
            {"round": 1, "t": 6000},
            {"src": "event", "round": 1, "t": -1},
            "r1@6s",
            None,
        ]
        for ref in cases:
            self.assertFalse(coach.resolve_ref(self.match, self.rounds, ref), ref)


class ApiTests(unittest.TestCase):
    def test_slot_normalisation_and_errors(self):
        self.assertEqual(coach.normalize_slot("P3"), "P3")
        self.assertEqual(coach.normalize_slot("p3"), "P3")
        self.assertEqual(coach.normalize_slot(3), "P3")
        self.assertEqual(coach.normalize_slot(" 10 "), "P10")
        for bad in ("", "X1", -1, None, True, 1.5):
            with self.assertRaises(ValueError):
                coach.normalize_slot(bad)

    def test_missing_store_and_unknown_slot(self):
        with tempfile.TemporaryDirectory() as empty:
            report = coach.player_report(empty, "P1")
            self.assertIn("error", report)
            self.assertEqual(report["player"], {"slot": "P1"})
        self._tmp = tempfile.TemporaryDirectory()
        store_dir = write_store(self._tmp.name, fixture_match(), fixture_rounds())
        with self.assertRaises(ValueError):
            coach.player_report(store_dir, "P11")
        self._tmp.cleanup()

    def test_project_cell_matches_p2_analysis(self):
        try:
            from bot.replay import p2_analysis
        except ImportError:                    # pragma: no cover - aiohttp absent
            self.skipTest("p2_analysis (aiohttp) unavailable")
        for x, y in ((-2465, -9166), (1535, -4166), (0, 0), (7500, -5000), (-9000, 9000)):
            self.assertEqual(coach.project_cell(MAP, x, y), p2_analysis._coordinate(MAP, x, y))
        self.assertIsNone(coach.project_cell({}, 0, 0))


@unittest.skipUnless(STORE.is_dir(), f"real store missing: {STORE}")
class RealStoreTests(unittest.TestCase):
    """The P4-A acceptance test: all 10 slots of the real Haven store."""

    @classmethod
    def setUpClass(cls):
        cls.store = coach.load_store(STORE)
        assert cls.store is not None
        cls.reports = coach.all_player_reports(STORE)

    def test_all_ten_slots_and_every_ref_resolves(self):
        self.assertEqual(sorted(self.reports), ["P1", "P10", "P2", "P3", "P4", "P5",
                                                "P6", "P7", "P8", "P9"])
        checked = 0
        for slot, report in sorted(self.reports.items()):
            self.assertEqual(report["schema"], "valoplant_coach/v1")
            self.assertEqual(report["replayId"], "bad1543033d11396")
            self.assertEqual(report["map"], "Haven")
            self.assertEqual(report["roundsAnalyzed"], 18)
            self.assertEqual(report["baseline"]["count"], 10)
            self.assertTrue(report["findings"], slot)
            for finding in report["findings"]:
                self.assertTrue(finding["refs"], f"{slot} {finding['id']}")
                self.assertLessEqual(len(finding["refs"]), coach.MAX_REFS)
                self.assertIn(finding["severity"], ("info", "minor", "major"))
                for ref in finding["refs"]:
                    checked += 1
                    self.assertTrue(
                        coach.resolve_ref(self.store["match"], self.store["rounds"], ref),
                        f"{slot} {finding['id']} -> {ref}")
        self.assertGreater(checked, 200)

    def test_findings_exclude_hallucinated_sources(self):
        # every ref is also resolvable through the report's own store slice, i.e. the
        # payload is self-contained and cannot cite a round it never read.
        for slot, report in self.reports.items():
            for finding, ref in all_refs(report):
                self.assertIn(ref["round"], self.store["rounds"], f"{slot} {finding['id']}")
                self.assertIn(ref["src"], ("event", "sample", "round"))

    def test_counts_agree_with_the_store(self):
        match = self.store["match"]
        kills_total = sum(meta["kills"] for meta in match["rounds"])
        metrics = {slot: report["metrics"] for slot, report in self.reports.items()}
        self.assertEqual(sum(row["kills"] for row in metrics.values()), kills_total)
        self.assertEqual(sum(row["deaths"] for row in metrics.values()), kills_total)
        self.assertEqual(sum(row["firstBloods"] for row in metrics.values()), 18)
        self.assertEqual(sum(row["firstDeaths"] for row in metrics.values()), 18)
        planted = sum(1 for meta in match["rounds"] if meta["plants"])
        for slot, row in metrics.items():
            self.assertEqual(row["postPlant"]["rounds"], planted, slot)
            self.assertEqual(len(row["roundsTable"]), 18, slot)
            self.assertEqual(row["sides"]["attack"]["rounds"] + row["sides"]["defense"]["rounds"]
                             + row["unknownSideRounds"], 18, slot)

    def test_sides_flip_after_round_12(self):
        sides = self.reports["P1"]["sideInference"]
        first_half = {sides["rounds"][n] for n in range(1, 13)}
        second_half = {sides["rounds"][n] for n in range(13, 19)}
        self.assertEqual(len(first_half), 1, sides)
        self.assertEqual(len(second_half), 1, sides)
        self.assertNotEqual(first_half, second_half, "sides must swap after round 12")
        self.assertEqual(first_half, {1})       # Haven bad1543033d11396: team 1 attacks R1-12
        self.assertEqual(second_half, {0})
        self.assertNotIn(None, sides["rounds"].values())
        self.assertTrue(set(sides["source"].values()) <= {"replay", "inferred"})

    def test_side_source_is_replay_when_published(self):
        sides = self.reports["P1"]["sideInference"]
        published = sides["publishedRounds"]
        for number in published:
            self.assertEqual(sides["source"][number], "replay")
        if published:
            self.assertEqual(len(published), 18)
        else:
            self.assertEqual(set(sides["source"].values()), {"inferred"})
            self.assertEqual([half["attackerTeamInferred"] for half in sides["halves"]], [1, 0])

    def test_expected_haven_facts(self):
        metrics = self.reports["P1"]["metrics"]
        self.assertEqual(metrics["rounds"], 18)
        self.assertEqual(self.store["match"]["rounds"][17]["endType"], "defuse")
        self.assertEqual(metrics["roundsTable"][17]["decidedAt"], 92459)   # defuse r18
        self.assertEqual(metrics["roundsTable"][0]["decidedAt"], 75766)    # last kill r1
        # P7 is the Clove that revives herself: two deaths in one lost round, so
        # death counts can exceed the lost-round count.
        p7 = self.reports["P7"]["metrics"]
        self.assertGreater(p7["lossDeaths"], p7["lostRounds"] - 1)
        self.assertEqual(p7["lossRoundsWithDeath"], p7["lostRounds"])

    def test_loss_involvement_share_never_exceeds_100_percent(self):
        # P7 is the Clove that revives herself: 5 lost rounds, 6 deaths. The finding used
        # to print deaths / lostRounds under a "% of the lost rounds" label, i.e. 120%.
        for slot, report in self.reports.items():
            finding = {row["kind"]: row for row in report["findings"]}.get("loss_involvement")
            if finding is None:
                continue
            for value in re.findall(r"(\d+)%", finding["text"]):
                self.assertLessEqual(int(value), 100, f"{slot}: {finding['text']}")
        metrics = self.reports["P7"]["metrics"]
        self.assertEqual((metrics["lostRounds"], metrics["lossRoundsWithDeath"],
                          metrics["lossDeaths"]), (5, 5, 6))
        self.assertGreater(metrics["lossDeathRate"], 1.0)   # the old, mislabelled ratio
        p7 = {row["kind"]: row for row in self.reports["P7"]["findings"]}["loss_involvement"]
        self.assertIn("100% of the lost rounds", p7["text"])
        self.assertIn("6 deaths", p7["text"])
        for ref in p7["refs"]:                              # refs and grounding unchanged
            self.assertTrue(coach.resolve_ref(self.store["match"], self.store["rounds"], ref),
                            f"P7 loss_involvement -> {ref}")

    def test_determinism_and_size(self):
        again = coach.all_player_reports(STORE)
        self.assertEqual(json.dumps(again, sort_keys=True),
                         json.dumps(self.reports, sort_keys=True))
        self.assertLess(len(json.dumps(self.reports["P1"])), 60000)


if __name__ == "__main__":
    unittest.main()
