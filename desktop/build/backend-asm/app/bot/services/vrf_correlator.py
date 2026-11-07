"""
VRF Correlator — Aligns VRF replay data with HenrikDev custom match data
and computes first-contact statistics per round and per player.
"""

import json
import asyncio
import logging
import os
from pathlib import Path
from typing import Optional
from dataclasses import dataclass, field
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class CorrelationResult:
    """Result of correlating VRF replay with HenrikDev match data."""
    vrf_map_name: str
    vrf_total_rounds: int
    henrik_matches: list
    correlated_rounds: list
    first_contact_events: list
    player_first_contact_stats: list
    vrf_abilities: list
    site_attack_heatmap: dict
    site_defense_heatmap: dict


def _get_map_bounds(map_name: str) -> tuple:
    """Get approximate map coordinate bounds for heatmap placement."""
    map_name_lower = map_name.lower()
    bounds = {
        "ascent": (-4000, 4000, -3000, 3000),
        "bind": (-4500, 4500, -3500, 3500),
        "breeze": (-5000, 5000, -4000, 4000),
        "haven": (-4000, 4000, -3000, 3000),
        "icebox": (-4000, 4000, -3000, 3000),
        "lotus": (-4000, 4000, -3000, 3000),
        "pearl": (-4000, 4000, -3000, 3000),
        "split": (-4000, 4000, -3000, 3000),
        "sunset": (-4000, 4000, -3000, 3000),
        "abyss": (-4000, 4000, -3000, 3000),
        "corrode": (-4000, 4000, -3000, 3000),
        "summit": (-4000, 4000, -3000, 3000),
    }
    return bounds.get(map_name_lower, (-4000, 4000, -3000, 3000))


def _get_map_site_centers(map_name: str) -> dict:
    """Get approximate site centers for map heatmaps."""
    map_name_lower = map_name.lower()
    sites = {
        "ascent": {"A": (-2000, 1500), "B": (2000, -1500), "mid": (0, 0)},
        "bind": {"A": (-2500, 2000), "B": (2500, -2000), "mid": (0, 0)},
        "breeze": {"A": (-3000, 2500), "B": (3000, -2500), "mid": (0, 0)},
        "haven": {"A": (-2000, 2000), "B": (2000, 2000), "C": (0, -2000), "mid": (0, 0)},
        "icebox": {"A": (-2500, 2000), "B": (2500, -2000), "mid": (0, 0)},
        "lotus": {"A": (-2000, 2000), "B": (2000, 2000), "C": (0, -2000), "mid": (0, 0)},
        "pearl": {"A": (-2500, 2000), "B": (2500, -2000), "mid": (0, 0)},
        "split": {"A": (-2000, 2000), "B": (2000, -2000), "mid": (0, 0)},
        "sunset": {"A": (-2500, 2000), "B": (2500, -2000), "mid": (0, 0)},
        "abyss": {"A": (-2000, 1500), "B": (2000, -1500), "mid": (0, 0)},
        "corrode": {"A": (-2000, 1500), "B": (2000, -1500), "mid": (0, 0)},
        "summit": {"A": (-2000, 1500), "B": (2000, -1500), "mid": (0, 0)},
    }
    return sites.get(map_name_lower, {"A": (-2000, 1500), "B": (2000, -1500), "mid": (0, 0)})


def _align_vrf_round_with_henrik(
    vrf_round: dict,
    henrik_round: dict,
    positions_data: dict,
    abilities_data: list,
) -> dict:
    """Align a single VRF round with a HenrikDev round."""
    round_num = vrf_round["round_num"]
    site = vrf_round.get("plant_site")
    winner = vrf_round.get("winner", "unknown")
    attacker_team = vrf_round.get("attacker_team")

    plant_time = henrik_round.get("plant_time", 0.0)
    defused = henrik_round.get("defused", False)
    defuse_time = henrik_round.get("defuse_time", 0.0)

    vrf_kill_times = []
    henrik_kills = []
    for kill in henrik_round.get("kill_events", []):
        kill_time = kill.get("kill_time_in_round", 0.0)
        henrik_kills.append(kill)
        vrf_kill_times.append(kill_time)

    first_contact = None
    min_kill_time = min(vrf_kill_times) if vrf_kill_times else 999.0

    if positions_data.get("samples") and min_kill_time < 999:
        samples = positions_data["samples"]
        best_sample = None
        best_diff = float("inf")
        for sample in samples:
            ts = sample[0]
            diff = abs(ts - min_kill_time)
            if diff < best_diff:
                best_diff = diff
                best_sample = sample

        if best_sample:
            ts_ms = best_sample[0]
            guid = best_sample[1]
            x = best_sample[2]
            y = best_sample[3]

            attacker_guids = set()
            defender_guids = set()
            for p in positions_data.get("players", {}):
                if p.get("team") == attacker_team:
                    attacker_guids.add(p["guid"])
                else:
                    defender_guids.add(p["guid"])

            if guid in attacker_guids:
                side = "attack"
            else:
                side = "defense"

            first_contact = {
                "side": side,
                "position": {"x": x, "y": y},
                "timestamp_ms": ts_ms,
                "guid": guid,
            }

    round_abilities = [a for a in abilities_data if 0 <= a.get("t", 0) < 120]

    return {
        "round_num": round_num,
        "site": site,
        "winner": winner,
        "plant_time": plant_time,
        "defuse_time": defuse_time,
        "defused": defused,
        "attacker_team": attacker_team,
        "vrf_kill_times": sorted(vrf_kill_times),
        "henrik_kills": henrik_kills,
        "first_contact": first_contact,
        "positions": vrf_round.get("positions", []),
        "abilities": round_abilities,
    }


async def correlate_replay(
    vrf_data,
    henrik_matches: list,
    target_riot_id: Optional[str] = None,
    limit: int = 25,
    token: Optional[str] = None,
) -> CorrelationResult:
    """
    Correlate VRF replay data with HenrikDev custom match data.

    Args:
        vrf_data: Parsed VRF replay data.
        henrik_matches: List of HenrikDev custom matches.
        target_riot_id: Optional Riot ID to filter for specific player.
        limit: Number of HenrikDev matches to consider.
        token: Optional HenrikDev API token.

    Returns:
        CorrelationResult with aligned rounds and computed statistics.
    """
    from .first_contact_analyzer import (
        FirstContactEvent,
        PlayerFirstContactStats,
        fetch_henrikdev_custom_matches,
    )

    if not token:
        token = os.getenv("HENRIKDEV_TOKEN")
        if not token:
            raise ValueError("HenrikDev token not configured. Set HENRIKDEV_TOKEN in .env")

    if target_riot_id:
        matches = await fetch_henrikdev_custom_matches(
            riot_id=target_riot_id, region="na", limit=limit, token=token
        )
    else:
        matches = henrik_matches

    # Filter to competitive customs
    competitive_matches = []
    for match in matches:
        metadata = match.get("metadata", {})
        mode = metadata.get("mode", "").lower()
        queue = metadata.get("queue", "").lower()
        if mode in ("", "competitive") and queue in ("", "standard"):
            competitive_matches.append(match)

    if not competitive_matches:
        competitive_matches = matches

    correlated_rounds = []
    all_first_contact_events = []
    site_attack_heatmap = defaultdict(list)
    site_defense_heatmap = defaultdict(list)

    vrf_rounds = vrf_data.rounds if hasattr(vrf_data, "rounds") else []
    henrik_rounds = competitive_matches[0].get("rounds", [])[: vrf_data.total_rounds]

    for vrf_round, henrik_round in zip(vrf_rounds, henrik_rounds):
        aligned = _align_vrf_round_with_henrik(vrf_round, henrik_round, vrf_data.raw_positions or {}, vrf_data.abilities or [])

        fc = aligned.get("first_contact")
        if fc:
            event = FirstContactEvent(
                match_id=competitive_matches[0].get("matchid", ""),
                map_name=vrf_data.map_name,
                round_num=aligned["round_num"],
                killer_name="Unknown",
                killer_team=aligned["attacker_team"],
                killer_agent="",
                victim_name="Unknown",
                victim_team=aligned["attacker_team"] if fc["side"] == "attack" else "Blue",
                victim_agent="",
                kill_time=aligned["vrf_kill_times"][0] if aligned["vrf_kill_times"] else 0,
                is_first_blood=False,
                side=fc["side"],
                killer_pos=fc["position"],
            )
            all_first_contact_events.append(event)

            if fc["side"] == "attack":
                site_attack_heatmap[aligned["site"]].append(fc["position"])
            else:
                site_defense_heatmap[aligned["site"]].append(fc["position"])

        correlated_rounds.append(aligned)

    player_stats = []
    player_events = defaultdict(list)
    for event in all_first_contact_events:
        player_name = event.killer_name if event.side == "attack" else event.victim_name
        player_events[player_name].append(event)

    for player_name, events in player_events.items():
        stats = PlayerFirstContactStats(
            player_name=player_name,
            riot_id="",
            agent="",
            maps=[],
            total_rounds=len(events),
            first_kills=sum(1 for e in events if e.side == "attack"),
            first_deaths=sum(1 for e in events if e.side == "defense"),
            first_bloods=0,
            first_blooded=0,
            attack_first_kills=0,
            attack_first_deaths=0,
            defense_first_kills=0,
            defense_first_deaths=0,
            avg_kill_time_attack=0.0,
            avg_kill_time_defense=0.0,
            sites_attacked={},
            sites_defended={},
        )
        player_stats.append(stats)

    return CorrelationResult(
        vrf_map_name=vrf_data.map_name,
        vrf_total_rounds=vrf_data.total_rounds,
        henrik_matches=competitive_matches,
        correlated_rounds=correlated_rounds,
        first_contact_events=all_first_contact_events,
        player_first_contact_stats=player_stats,
        vrf_abilities=vrf_data.abilities or [],
        site_attack_heatmap=dict(site_attack_heatmap),
        site_defense_heatmap=dict(site_defense_heatmap),
    )


async def correlate_replay_sync(
    vrf_data,
    henrik_matches: list,
    target_riot_id: Optional[str] = None,
    limit: int = 25,
    token: Optional[str] = None,
) -> CorrelationResult:
    """Synchronous wrapper for correlate_replay."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None,
        lambda: asyncio.run(
            correlate_replay(
                vrf_data,
                henrik_matches,
                target_riot_id=target_riot_id,
                limit=limit,
                token=token,
            )
        ),
    )