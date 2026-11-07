import unittest

from bot.services.first_contact_analyzer import (
    TRADE_WINDOW_SECONDS,
    analyze_trade_kills,
    detect_trade_kills,
)


PLAYERS = [
    {"puuid": "a", "name": "Alpha", "tag": "A", "team": "Red", "character": "Jett"},
    {"puuid": "b", "name": "Bravo", "tag": "B", "team": "Blue", "character": "Sova"},
    {"puuid": "c", "name": "Charlie", "tag": "C", "team": "Blue", "character": "Omen"},
    {"puuid": "d", "name": "Delta", "tag": "D", "team": "Blue", "character": "Sage"},
    {"puuid": "e", "name": "Echo", "tag": "E", "team": "Blue", "character": "Raze"},
    {"puuid": "f", "name": "Foxtrot", "tag": "F", "team": "Red", "character": "Killjoy"},
]


MATCHES = [{
    "metadata": {
        "matchid": "offline-1",
        "map": "Ascent",
        "teams": {"red": {"has_won": True}},
    },
    "players": {"all_players": PLAYERS},
    "rounds": [
        {"kill_events": [
            {"killer_puuid": "a", "victim_puuid": "b", "kill_time_in_round": 10.0},
            {"killer_puuid": "c", "victim_puuid": "a", "kill_time_in_round": 13.0},
        ]},
        {"kill_events": [
            {"killer_puuid": "a", "victim_puuid": "d", "kill_time_in_round": 10.0},
            {"killer_puuid": "c", "victim_puuid": "f", "kill_time_in_round": 10.0 + TRADE_WINDOW_SECONDS + 1},
        ]},
        {"kill_events": [
            {"killer_puuid": "a", "victim_puuid": "e", "kill_time_in_round": 10.0},
            {"killer_puuid": "f", "victim_puuid": "a", "kill_time_in_round": 12.0},
        ]},
        {"kill_events": [
            {"victim_puuid": "b", "kill_time_in_round": 10.0},
            {"killer_puuid": "b", "victim_puuid": "b", "kill_time_in_round": 11.0},
        ]},
    ],
}]


class TradeDetectionTests(unittest.TestCase):
    def test_trade_detection_and_aggregation(self):
        events = detect_trade_kills(MATCHES)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].original_victim_riot_id, "Bravo#B")
        self.assertEqual(events[0].avenger_riot_id, "Charlie#C")
        self.assertEqual(events[0].time_since_original_kill, 3.0)

        stats = {s.riot_id: s for s in analyze_trade_kills(events, MATCHES, "Bravo#B")}
        self.assertEqual(stats["Bravo#B"].deaths_traded, 1)
        self.assertEqual(stats["Bravo#B"].trade_conversion_rate, 1.0)
        self.assertEqual(stats["Charlie#C"].kills_that_were_trades, 1)
        self.assertEqual(stats["Delta#D"].deaths_traded, 0)
        self.assertEqual(stats["Echo#E"].deaths_traded, 0)


if __name__ == "__main__":
    unittest.main()
