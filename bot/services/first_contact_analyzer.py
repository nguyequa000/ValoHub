"""
First Contact / Kill Analysis Service for Valorant Analyst Bot.

Analyzes HenrikDev custom match data to extract:
- First kill/death events per round (first contact)
- Player-level first contact statistics
- Site-based heatmaps for first contacts
- Team-level attack/defense first contact tendencies
"""
import asyncio
import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple
from pathlib import Path

import aiohttp

from .rate_limiter import rate_limiter
from .cache import cache, cached_fetch


TRADE_WINDOW_SECONDS = 5.0


# exports/ is created lazily on the first report write (generate_summary_heatmaps),
# so a plain app boot or test run does not recreate the empty folder.
EXPORTS_DIR = Path(__file__).parent.parent.parent / "exports"

from bot.replay.paths import get_paths

CACHE_DIR = get_paths().cache  # no import-time mkdir (read-only installs); writers create it lazily


def _load_plotting():
    """Import matplotlib/numpy on first heatmap use (keeps bot startup fast)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot
    import matplotlib.patches
    from matplotlib.colors import LinearSegmentedColormap
    import numpy
    return matplotlib.pyplot, matplotlib.patches, LinearSegmentedColormap, numpy


@dataclass
class FirstContactEvent:
    """Represents a first kill/death event in a round."""
    match_id: str
    map_name: str
    round_num: int
    killer_name: str
    killer_team: str
    killer_agent: str
    victim_name: str
    victim_team: str
    victim_agent: str
    kill_time: float
    is_first_blood: bool
    side: str  # "attack" or "defense" for killer's team
    site: Optional[str] = None
    killer_pos: Optional[Tuple[float, float]] = None
    victim_pos: Optional[Tuple[float, float]] = None


@dataclass
class TradeKillEvent:
    """Represents a kill avenged within the trade window."""
    original_killer_riot_id: str
    original_victim_riot_id: str
    original_killer_team: str
    original_victim_team: str
    original_kill_time: float
    avenger_riot_id: str
    avenged_teammate_riot_id: str
    trade_time: float
    time_since_original_kill: float


@dataclass
class PlayerTradeStats:
    """Trade statistics for a single player."""
    riot_id: str
    player_name: str
    deaths_total: int
    deaths_traded: int
    kills_total: int
    kills_that_were_trades: int
    trade_conversion_rate: float
    maps: List[str]


@dataclass
class PlayerFirstContactStats:
    """First contact statistics for a single player."""
    player_name: str
    riot_id: str
    agent: str
    maps: List[str]
    total_rounds: int
    first_kills: int
    first_deaths: int
    first_bloods: int
    first_blooded: int
    attack_first_kills: int
    attack_first_deaths: int
    defense_first_kills: int
    defense_first_deaths: int
    avg_kill_time_attack: float
    avg_kill_time_defense: float
    sites_attacked: Dict[str, int]
    sites_defended: Dict[str, int]
    
    @property
    def first_blood_rate(self) -> float:
        if self.total_rounds == 0:
            return 0.0
        return round((self.first_bloods / self.total_rounds) * 100, 1)
    
    @property
    def first_death_rate(self) -> float:
        if self.total_rounds == 0:
            return 0.0
        return round((self.first_blooded / self.total_rounds) * 100, 1)
    
    @property
    def kd_ratio_first_contact(self) -> float:
        if self.first_deaths == 0:
            return float(self.first_kills) if self.first_kills > 0 else 0.0
        return round(self.first_kills / self.first_deaths, 2)
    
    @property
    def attack_kd_first_contact(self) -> float:
        if self.attack_first_deaths == 0:
            return float(self.attack_first_kills) if self.attack_first_kills > 0 else 0.0
        return round(self.attack_first_kills / self.attack_first_deaths, 2)
    
    @property
    def defense_kd_first_contact(self) -> float:
        if self.defense_first_deaths == 0:
            return float(self.defense_first_kills) if self.defense_first_kills > 0 else 0.0
        return round(self.defense_first_kills / self.defense_first_deaths, 2)


@dataclass
class TeamFirstContactSummary:
    """Team-level first contact summary."""
    team_name: str
    total_rounds: int
    attack_first_kills: int
    attack_first_deaths: int
    defense_first_kills: int
    defense_first_deaths: int
    site_first_kills: Dict[str, int]
    site_first_deaths: Dict[str, int]
    player_stats: List[PlayerFirstContactStats]


def get_map_bounds(map_name: str) -> Tuple[float, float, float, float]:
    """Get approximate map coordinate bounds for heatmap placement."""
    map_name_lower = map_name.lower()
    bounds = {
        "ascent": (-4000, 4000, -3000, 3000),
        "bind": (-4500, 4500, -3500, 3500),
        "breeze": (-5000, 5000, -4000, 4000),
        "fracture": (-4000, 4000, -3000, 3000),
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


def get_map_site_centers(map_name: str) -> Dict[str, Tuple[float, float]]:
    """Get approximate site centers for map heatmaps."""
    map_name_lower = map_name.lower()
    sites = {
        "ascent": {"A": (-2000, 1500), "B": (2000, -1500), "mid": (0, 0)},
        "bind": {"A": (-2500, 2000), "B": (2500, -2000), "mid": (0, 0)},
        "breeze": {"A": (-3000, 2500), "B": (3000, -2500), "mid": (0, 0)},
        "fracture": {"A": (-2000, 1500), "B": (2000, -1500), "mid": (0, 0)},
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


def extract_kill_position(
    kill_event: dict, who: str = "killer"
) -> Optional[Tuple[float, float]]:
    """Extract x, y position for 'killer' or 'victim' from a kill event."""
    if not kill_event:
        return None
    location = kill_event.get(f"{who}_location") or kill_event.get("location")
    if location and isinstance(location, dict):
        x = location.get("x")
        y = location.get("y")
        if x is not None and y is not None:
            return (float(x), float(y))
    return None


async def fetch_henrikdev_custom_matches(
    riot_id: str,
    region: str = "na",
    limit: int = 25,
    token: Optional[str] = None,
    session: Optional[aiohttp.ClientSession] = None
) -> List[dict]:
    """Fetch custom matches from HenrikDev API."""
    if not token:
        token = os.getenv("HENRIKDEV_TOKEN")
    if not token:
        raise ValueError("HenrikDev token not configured. Set HENRIKDEV_TOKEN in .env")

    name, tag = riot_id.split("#", 1) if "#" in riot_id else (riot_id, "")
    if not name or not tag:
        raise ValueError("Invalid Riot ID format. Use Name#Tag")

    await rate_limiter.acquire("henrikdev")

    encoded_name = name.replace(" ", "%20")
    encoded_tag = tag.replace("#", "%23")
    url = f"https://api.henrikdev.xyz/valorant/v3/matches/{region.lower()}/{encoded_name}/{encoded_tag}?filter=custom&size={limit}"
    headers = {"Authorization": token, "User-Agent": "AntiStratsBot/1.0"}

    if session is None:
        async with aiohttp.ClientSession() as owned_session:
            return await _get_custom_matches(owned_session, url, headers)
    return await _get_custom_matches(session, url, headers)


async def _get_custom_matches(
    session: aiohttp.ClientSession, url: str, headers: Dict[str, str]
) -> List[dict]:
    async with session.get(url, headers=headers) as resp:
        if resp.status == 200:
            data = await resp.json()
            return data.get("data", [])
        elif resp.status == 429:
            raise Exception("Rate limited (429). Try again later.")
        else:
            raise Exception(f"API Error: HTTP {resp.status}")


def filter_competitive_customs(matches: List[dict]) -> List[dict]:
    """Filter matches to only competitive/standard customs."""
    filtered = []
    for match in matches:
        metadata = match.get("metadata", {})
        mode = metadata.get("mode", "").lower()
        queue = metadata.get("queue", "").lower()
        if mode in ("", "competitive") and queue in ("", "standard"):
            filtered.append(match)
    return filtered


def _iter_match_rounds(matches):
    """Yield normalized round data for rounds containing kill events."""
    for match in matches:
        metadata = match.get("metadata", {})
        player_map = {
            p.get("puuid", ""): p
            for p in match.get("players", {}).get("all_players", [])
        }
        attacker_team = "Red" if metadata.get("teams", {}).get("red", {}).get("has_won", False) else "Blue"
        for round_num, round_data in enumerate(match.get("rounds", []), 1):
            kill_events = round_data.get("kill_events", [])
            if kill_events:
                yield {
                    "match_id": metadata.get("matchid", ""),
                    "map_name": metadata.get("map", "Unknown"),
                    "round_num": round_num,
                    "kill_events": sorted(kill_events, key=lambda k: k.get("kill_time_in_round", 0)),
                    "player_map": player_map,
                    "attacker_team": attacker_team,
                    "site": (round_data.get("plant_events") or {}).get("plant_site"),
                }


def analyze_first_contacts(matches: List[dict], target_riot_id: str) -> Tuple[List[FirstContactEvent], List[PlayerFirstContactStats]]:
    """
    Analyze all matches for first contact events.
    Returns (all_events, player_stats_list)
    """
    all_events = []
    player_stats_map = {}
    target_name, target_tag = target_riot_id.split("#", 1) if "#" in target_riot_id else (target_riot_id, "")
    target_name_lower = target_name.lower()
    target_tag_lower = target_tag.lower()
    
    for round_info in _iter_match_rounds(matches):
        match_id = round_info["match_id"]
        map_name = round_info["map_name"]
        player_map = round_info["player_map"]
        round_num = round_info["round_num"]
        first_kill = round_info["kill_events"][0]
        killer_puuid = first_kill.get("killer_puuid", "")
        victim_puuid = first_kill.get("victim_puuid", "")
        killer_info = player_map.get(killer_puuid, {})
        victim_info = player_map.get(victim_puuid, {})
        killer_name = killer_info.get("name", "Unknown")
        killer_tag = killer_info.get("tag", "")
        killer_riot = f"{killer_name}#{killer_tag}" if killer_tag else killer_name
        killer_team = killer_info.get("team", "")
        killer_agent = killer_info.get("character", "")
        victim_name = victim_info.get("name", "Unknown")
        victim_tag = victim_info.get("tag", "")
        victim_riot = f"{victim_name}#{victim_tag}" if victim_tag else victim_name
        victim_team = victim_info.get("team", "")
        victim_agent = victim_info.get("character", "")
        kill_time = first_kill.get("kill_time_in_round", 0)
        site = round_info["site"]
        attacker_team = round_info["attacker_team"]
        side = "attack" if killer_team == attacker_team else "defense"
        all_events.append(FirstContactEvent(
            match_id=match_id,
            map_name=map_name,
            round_num=round_num,
            killer_name=killer_riot,
            killer_team=killer_team,
            killer_agent=killer_agent,
            victim_name=victim_riot,
            victim_team=victim_team,
            victim_agent=victim_agent,
            kill_time=kill_time,
            is_first_blood=True,
            side=side,
            site=site,
            killer_pos=extract_kill_position(first_kill, "killer"),
            victim_pos=extract_kill_position(first_kill, "victim"),
        ))

        for riot_id, role, agent, team in [
            (killer_riot, "killer", killer_agent, killer_team),
            (victim_riot, "victim", victim_agent, victim_team)
        ]:
            if riot_id not in player_stats_map:
                player_stats_map[riot_id] = {
                    "name": riot_id.split("#")[0] if "#" in riot_id else riot_id,
                    "riot_id": riot_id,
                    "agents": defaultdict(lambda: {
                        "maps": set(), "total_rounds": 0, "first_kills": 0,
                        "first_deaths": 0, "first_bloods": 0, "first_blooded": 0,
                        "attack_first_kills": 0, "attack_first_deaths": 0,
                        "defense_first_kills": 0, "defense_first_deaths": 0,
                        "kill_times_attack": [], "kill_times_defense": [],
                        "sites_attacked": defaultdict(int), "sites_defended": defaultdict(int),
                    })
                }
            agent_stats = player_stats_map[riot_id]["agents"][agent]
            agent_stats["maps"].add(map_name)
            agent_stats["total_rounds"] += 1
            if role == "killer":
                agent_stats["first_kills"] += 1
                agent_stats["first_bloods"] += 1
                if side == "attack":
                    agent_stats["attack_first_kills"] += 1
                    agent_stats["kill_times_attack"].append(kill_time)
                    if site:
                        agent_stats["sites_attacked"][site] += 1
                else:
                    agent_stats["defense_first_kills"] += 1
                    agent_stats["kill_times_defense"].append(kill_time)
                    if site:
                        agent_stats["sites_defended"][site] += 1
            else:
                agent_stats["first_deaths"] += 1
                agent_stats["first_blooded"] += 1
                if side == "attack":
                    agent_stats["attack_first_deaths"] += 1
                else:
                    agent_stats["defense_first_deaths"] += 1
    
    player_stats_list = []
    for riot_id, data in player_stats_map.items():
        for agent, stats in data["agents"].items():
            total_rounds = stats["total_rounds"]
            avg_kill_time_attack = (
                sum(stats["kill_times_attack"]) / len(stats["kill_times_attack"]) 
                if stats["kill_times_attack"] else 0
            )
            avg_kill_time_defense = (
                sum(stats["kill_times_defense"]) / len(stats["kill_times_defense"]) 
                if stats["kill_times_defense"] else 0
            )
            
            player_stats_list.append(PlayerFirstContactStats(
                player_name=data["name"],
                riot_id=riot_id,
                agent=agent,
                maps=list(stats["maps"]),
                total_rounds=total_rounds,
                first_kills=stats["first_kills"],
                first_deaths=stats["first_deaths"],
                first_bloods=stats["first_bloods"],
                first_blooded=stats["first_blooded"],
                attack_first_kills=stats["attack_first_kills"],
                attack_first_deaths=stats["attack_first_deaths"],
                defense_first_kills=stats["defense_first_kills"],
                defense_first_deaths=stats["defense_first_deaths"],
                avg_kill_time_attack=round(avg_kill_time_attack, 1),
                avg_kill_time_defense=round(avg_kill_time_defense, 1),
                sites_attacked=dict(stats["sites_attacked"]),
                sites_defended=dict(stats["sites_defended"]),
            ))
    
    return all_events, player_stats_list


def _riot_id(player: dict) -> str:
    name = player.get("name", "Unknown")
    tag = player.get("tag", "")
    return f"{name}#{tag}" if tag else name


def detect_trade_kills(matches: List[dict]) -> List[TradeKillEvent]:
    """Find earliest qualifying teammate avenges for every kill."""
    trades = []
    for round_info in _iter_match_rounds(matches):
        kills = round_info["kill_events"]
        players = round_info["player_map"]
        for index, original in enumerate(kills):
            killer_puuid = original.get("killer_puuid")
            victim_puuid = original.get("victim_puuid")
            if not killer_puuid or not victim_puuid or killer_puuid == victim_puuid:
                continue
            killer = players.get(killer_puuid, {})
            victim = players.get(victim_puuid, {})
            killer_team = killer.get("team", "")
            victim_team = victim.get("team", "")
            original_time = original.get("kill_time_in_round", 0)
            for trade in kills[index + 1:]:
                trade_time = trade.get("kill_time_in_round", 0)
                elapsed = trade_time - original_time
                if elapsed > TRADE_WINDOW_SECONDS:
                    break
                if elapsed < 0:
                    continue
                if (trade.get("killer_puuid") and trade.get("victim_puuid") == killer_puuid
                        and players.get(trade["killer_puuid"], {}).get("team") == victim_team
                        and trade["killer_puuid"] != trade["victim_puuid"]):
                    trades.append(TradeKillEvent(
                        original_killer_riot_id=_riot_id(killer),
                        original_victim_riot_id=_riot_id(victim),
                        original_killer_team=killer_team,
                        original_victim_team=victim_team,
                        original_kill_time=original_time,
                        avenger_riot_id=_riot_id(players[trade["killer_puuid"]]),
                        avenged_teammate_riot_id=_riot_id(victim),
                        trade_time=trade_time,
                        time_since_original_kill=elapsed,
                    ))
                    break
    return trades


def analyze_trade_kills(
    trade_events: List[TradeKillEvent], all_kill_events_by_round, target_riot_id: str
) -> List[PlayerTradeStats]:
    """Aggregate total and traded kills/deaths from normalized round data."""
    rounds = all_kill_events_by_round
    if isinstance(rounds, list) and rounds and "metadata" in rounds[0]:
        rounds = _iter_match_rounds(rounds)
    totals = {}
    for round_info in rounds:
        players = round_info["player_map"]
        map_name = round_info["map_name"]
        for player in players.values():
            riot_id = _riot_id(player)
            totals.setdefault(riot_id, {
                "player_name": player.get("name", "Unknown"),
                "deaths_total": 0, "deaths_traded": 0, "kills_total": 0,
                "kills_that_were_trades": 0, "maps": set(),
            })["maps"].add(map_name)
        for kill in round_info["kill_events"]:
            killer_puuid = kill.get("killer_puuid")
            victim_puuid = kill.get("victim_puuid")
            if not killer_puuid or not victim_puuid or killer_puuid == victim_puuid:
                continue
            for puuid, field in ((killer_puuid, "kills_total"), (victim_puuid, "deaths_total")):
                player = players.get(puuid, {})
                if not player:
                    continue
                entry = totals.setdefault(_riot_id(player), {
                    "player_name": player.get("name", "Unknown"),
                    "deaths_total": 0, "deaths_traded": 0, "kills_total": 0,
                    "kills_that_were_trades": 0, "maps": set(),
                })
                entry[field] += 1
                entry["maps"].add(map_name)

    for event in trade_events:
        if event.original_victim_riot_id in totals:
            totals[event.original_victim_riot_id]["deaths_traded"] += 1
        if event.avenger_riot_id in totals:
            totals[event.avenger_riot_id]["kills_that_were_trades"] += 1

    return [PlayerTradeStats(
        riot_id=riot_id,
        player_name=data["player_name"],
        deaths_total=data["deaths_total"],
        deaths_traded=data["deaths_traded"],
        kills_total=data["kills_total"],
        kills_that_were_trades=data["kills_that_were_trades"],
        trade_conversion_rate=round(data["deaths_traded"] / data["deaths_total"], 3) if data["deaths_total"] else 0.0,
        maps=sorted(data["maps"]),
    ) for riot_id, data in totals.items()]


def format_trade_stats_table(stats: List[PlayerTradeStats], target_riot_id: str) -> str:
    """Format player trade stats as a markdown table."""
    target = [s for s in stats if s.riot_id.lower() == target_riot_id.lower()]
    others = [s for s in stats if s.riot_id.lower() != target_riot_id.lower()]
    lines = ["## Trade-Kill Statistics", ""]
    header = "| Player | Maps | Deaths | Traded Deaths | Trade Conversion | Kills | Trade Kills |"
    separator = "|--------|------|--------|---------------|------------------|-------|-------------|"
    if target:
        lines += [f"### Target Player: {target_riot_id}", "", header, separator]
        rows = target
    else:
        rows = []
    for player in sorted(rows, key=lambda s: -s.deaths_total):
        lines.append(f"| {player.riot_id} | {', '.join(player.maps)} | {player.deaths_total} | {player.deaths_traded} | {player.trade_conversion_rate:.1%} | {player.kills_total} | {player.kills_that_were_trades} |")
    if others:
        lines += ["", "### Other Tracked Players", "", header, separator]
        for player in sorted(others, key=lambda s: -s.deaths_total):
            lines.append(f"| {player.riot_id} | {', '.join(player.maps)} | {player.deaths_total} | {player.deaths_traded} | {player.trade_conversion_rate:.1%} | {player.kills_total} | {player.kills_that_were_trades} |")
    return "\n".join(lines)


def _accumulate_heatmap(grid, events: List[FirstContactEvent], who: str, bounds) -> int:
    """Add real event coordinates to the heatmap grid. Returns plotted point count."""
    x_min, x_max, y_min, y_max = bounds
    rows, cols = grid.shape
    plotted = 0
    for event in events:
        pos = event.killer_pos if who == "killer" else event.victim_pos
        if not pos:
            continue
        x, y = pos
        if not (x_min <= x <= x_max and y_min <= y <= y_max):
            continue
        ix = int((x - x_min) / (x_max - x_min) * (cols - 1))
        iy = int((y - y_min) / (y_max - y_min) * (rows - 1))
        grid[iy, ix] += 1
        plotted += 1
    return plotted


def generate_first_contact_heatmap(
    events: List[FirstContactEvent],
    map_name: str,
    output_path: Path,
    team_filter: Optional[str] = None,
    side_filter: Optional[str] = None,
    event_type: str = "kill"
) -> bool:
    """Generate a heatmap of first contact events for a specific map."""
    plt, mpatches, LinearSegmentedColormap, np = _load_plotting()
    map_events = [e for e in events if e.map_name.lower() == map_name.lower()]
    
    if team_filter:
        needle = team_filter.lower()
        map_events = [
            e for e in map_events
            if needle in e.killer_name.lower() or needle in e.victim_name.lower()
        ]
    
    if side_filter:
        map_events = [e for e in map_events if e.side == side_filter]
    
    bounds = get_map_bounds(map_name)
    x_min, x_max, y_min, y_max = bounds
    
    fig, ax = plt.subplots(figsize=(12, 10))
    
    site_centers = get_map_site_centers(map_name)
    for site, (sx, sy) in site_centers.items():
        circle = plt.Circle((sx, sy), 500, color='gray', alpha=0.3, fill=True)
        ax.add_patch(circle)
        ax.text(sx, sy + 600, site, ha='center', va='center', fontsize=14, fontweight='bold', color='gray')
    
    color = 'red' if event_type == "kill" else 'blue'
    label = "First Kills" if event_type == "kill" else "First Deaths"
    who = "killer" if event_type == "kill" else "victim"
    
    cmap = LinearSegmentedColormap.from_list("custom", [(0, 0, 0, 0), color], N=256)
    
    heatmap_data = np.zeros((100, 100))
    plotted = _accumulate_heatmap(heatmap_data, map_events, who, bounds)
    if plotted == 0:
        plt.close(fig)
        return False
    
    if heatmap_data.max() > 0:
        heatmap_data = np.log1p(heatmap_data)
        im = ax.imshow(heatmap_data, extent=[x_min, x_max, y_min, y_max], 
                       origin='lower', cmap=cmap, alpha=0.7, aspect='auto')
        plt.colorbar(im, ax=ax, label=f'{label} Density (log scale)')
    
    for site, (sx, sy) in site_centers.items():
        site_kills = len([e for e in map_events if e.site == site])
        if site_kills > 0:
            ax.text(sx, sy - 800, f"{site}: {site_kills}", ha='center', va='center', 
                   fontsize=11, fontweight='bold', 
                   bbox=dict(boxstyle="round,pad=0.3", facecolor='white', alpha=0.8))
    
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_title(f"{map_name} - First {label} Heatmap", fontsize=16, fontweight='bold')
    ax.set_xlabel("X Coordinate")
    ax.set_ylabel("Y Coordinate")
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    return True


def generate_team_heatmap(
    events: List[FirstContactEvent],
    map_name: str,
    output_path: Path,
    team_name: str
) -> bool:
    """Generate a combined team heatmap showing both kills and deaths."""
    plt, mpatches, LinearSegmentedColormap, np = _load_plotting()
    map_events = [e for e in events if e.map_name.lower() == map_name.lower()]
    
    team_kill_events = [e for e in map_events if team_name.lower() in e.killer_name.lower()]
    team_death_events = [e for e in map_events if team_name.lower() in e.victim_name.lower()]
    
    if not team_kill_events and not team_death_events:
        return False
    
    bounds = get_map_bounds(map_name)
    x_min, x_max, y_min, y_max = bounds
    
    fig, ax = plt.subplots(figsize=(14, 12))
    
    site_centers = get_map_site_centers(map_name)
    for site, (sx, sy) in site_centers.items():
        circle = plt.Circle((sx, sy), 500, color='gray', alpha=0.2, fill=True)
        ax.add_patch(circle)
        ax.text(sx, sy + 600, site, ha='center', va='center', fontsize=14, fontweight='bold', color='gray', alpha=0.7)
    
    kill_heatmap = np.zeros((100, 100))
    death_heatmap = np.zeros((100, 100))
    
    kills_plotted = _accumulate_heatmap(kill_heatmap, team_kill_events, "killer", bounds)
    deaths_plotted = _accumulate_heatmap(death_heatmap, team_death_events, "victim", bounds)
    
    if kills_plotted == 0 and deaths_plotted == 0:
        plt.close(fig)
        return False
    
    if kill_heatmap.max() > 0:
        kill_heatmap = np.log1p(kill_heatmap)
        im1 = ax.imshow(kill_heatmap, extent=[x_min, x_max, y_min, y_max], 
                       origin='lower', cmap='Reds', alpha=0.6, aspect='auto')
        plt.colorbar(im1, ax=ax, label='First Kills Density (log)', shrink=0.6)
    
    if death_heatmap.max() > 0:
        death_heatmap = np.log1p(death_heatmap)
        im2 = ax.imshow(death_heatmap, extent=[x_min, x_max, y_min, y_max], 
                       origin='lower', cmap='Blues', alpha=0.6, aspect='auto')
        plt.colorbar(im2, ax=ax, label='First Deaths Density (log)', shrink=0.6)
    
    for site, (sx, sy) in site_centers.items():
        site_kills = len([e for e in team_kill_events if e.site == site])
        site_deaths = len([e for e in team_death_events if e.site == site])
        if site_kills > 0 or site_deaths > 0:
            ax.text(sx, sy - 800, f"{site}: K{site_kills}/D{site_deaths}", ha='center', va='center', 
                   fontsize=11, fontweight='bold', 
                   bbox=dict(boxstyle="round,pad=0.3", facecolor='white', alpha=0.8))
    
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_title(f"{map_name} - {team_name} First Contact Heatmap\n(Red=First Kills, Blue=First Deaths)", 
                 fontsize=16, fontweight='bold')
    ax.set_xlabel("X Coordinate")
    ax.set_ylabel("Y Coordinate")
    ax.set_aspect('equal')
    ax.grid(True, alpha=0.3)
    
    red_patch = mpatches.Patch(color='red', alpha=0.6, label='First Kills')
    blue_patch = mpatches.Patch(color='blue', alpha=0.6, label='First Deaths')
    ax.legend(handles=[red_patch, blue_patch], loc='upper right')
    
    plt.tight_layout()
    plt.savefig(output_path, dpi=150, bbox_inches='tight')
    plt.close()
    return True


def generate_summary_heatmaps(
    events: List[FirstContactEvent],
    player_stats: List[PlayerFirstContactStats],
    output_dir: Path,
    team_name: str,
    target_riot_id: str
) -> List[Path]:
    """Generate summary heatmaps for all maps with sufficient data."""
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    
    maps_with_data = set(e.map_name for e in events)
    
    for map_name in maps_with_data:
        map_events = [e for e in events if e.map_name == map_name]
        if len(map_events) < 5:
            continue
        
        safe_map = map_name.replace(" ", "_").replace("/", "_")
        heatmap_path = output_dir / f"{team_name}_{safe_map}_first_contact_heatmap.png"
        
        if generate_team_heatmap(events, map_name, heatmap_path, team_name):
            generated.append(heatmap_path)
    
    return generated


def format_player_stats_table(stats: List[PlayerFirstContactStats], target_riot_id: str) -> str:
    """Format player first contact stats as a markdown table."""
    target_stats = [s for s in stats if s.riot_id.lower() == target_riot_id.lower()]
    other_stats = [s for s in stats if s.riot_id.lower() != target_riot_id.lower()]
    
    lines = []
    lines.append("## First Contact Statistics")
    lines.append("")
    
    if target_stats:
        lines.append("### Target Player: " + target_riot_id)
        lines.append("")
        lines.append("| Agent | Maps | Rounds | First Kills | First Deaths | First Bloods | First Blooded | FB% | FD% | K/D (FC) | ATK K/D | DEF K/D | Avg Kill Time (ATK/DEF) |")
        lines.append("|-------|------|--------|-------------|--------------|--------------|---------------|-----|-----|----------|---------|---------|------------------------|")
        for s in sorted(target_stats, key=lambda x: -x.total_rounds):
            lines.append(f"| {s.agent} | {', '.join(s.maps)} | {s.total_rounds} | {s.first_kills} | {s.first_deaths} | {s.first_bloods} | {s.first_blooded} | {s.first_blood_rate}% | {s.first_death_rate}% | {s.kd_ratio_first_contact} | {s.attack_kd_first_contact} | {s.defense_kd_first_contact} | {s.avg_kill_time_attack}/{s.avg_kill_time_defense}s |")
        lines.append("")
    
    if other_stats:
        lines.append("### Other Tracked Players")
        lines.append("")
        lines.append("| Player | Agent | Maps | Rounds | First Kills | First Deaths | First Bloods | FB% | K/D (FC) |")
        lines.append("|--------|-------|------|--------|-------------|--------------|--------------|-----|----------|")
        for s in sorted(other_stats, key=lambda x: -x.total_rounds)[:15]:
            lines.append(f"| {s.player_name} | {s.agent} | {', '.join(s.maps)} | {s.total_rounds} | {s.first_kills} | {s.first_deaths} | {s.first_bloods} | {s.first_blood_rate}% | {s.kd_ratio_first_contact} |")
        lines.append("")
    
    return "\n".join(lines)


def format_team_summary_table(summary: TeamFirstContactSummary) -> str:
    """Format team first contact summary as markdown."""
    lines = []
    lines.append("## Team First Contact Summary")
    lines.append("")
    lines.append(f"**Team:** {summary.team_name}")
    lines.append(f"**Total Rounds Analyzed:** {summary.total_rounds}")
    lines.append("")
    lines.append("### Overall First Contact Performance")
    lines.append("")
    lines.append("| Side | First Kills | First Deaths | K/D Ratio |")
    lines.append("|------|-------------|--------------|-----------|")
    
    atk_kd = (summary.attack_first_kills / summary.attack_first_deaths) if summary.attack_first_deaths > 0 else (summary.attack_first_kills if summary.attack_first_kills > 0 else 0)
    def_kd = (summary.defense_first_kills / summary.defense_first_deaths) if summary.defense_first_deaths > 0 else (summary.defense_first_kills if summary.defense_first_kills > 0 else 0)
    
    lines.append(f"| Attack | {summary.attack_first_kills} | {summary.attack_first_deaths} | {atk_kd:.2f} |")
    lines.append(f"| Defense | {summary.defense_first_kills} | {summary.defense_first_deaths} | {def_kd:.2f} |")
    lines.append("")
    
    if summary.site_first_kills or summary.site_first_deaths:
        lines.append("### Site-Based First Contacts")
        lines.append("")
        lines.append("| Site | First Kills | First Deaths |")
        lines.append("|------|-------------|--------------|")
        all_sites = set(list(summary.site_first_kills.keys()) + list(summary.site_first_deaths.keys()))
        for site in sorted(all_sites):
            kills = summary.site_first_kills.get(site, 0)
            deaths = summary.site_first_deaths.get(site, 0)
            lines.append(f"| {site} | {kills} | {deaths} |")
        lines.append("")
    
    return "\n".join(lines)


async def analyze_player_first_contacts(
    riot_id: str,
    region: str = "na",
    limit: int = 25,
    token: Optional[str] = None,
    generate_heatmaps: bool = True,
    session: Optional[aiohttp.ClientSession] = None
) -> Dict[str, Any]:
    """Full analysis pipeline for a player's first contact stats."""
    matches = await fetch_henrikdev_custom_matches(
        riot_id, region, limit, token, session=session
    )
    competitive_matches = filter_competitive_customs(matches)
    
    if not competitive_matches:
        return {
            "success": False,
            "error": "No competitive custom matches found",
            "matches_found": len(matches)
        }
    
    events, player_stats = analyze_first_contacts(competitive_matches, riot_id)
    trade_events = detect_trade_kills(competitive_matches)
    trade_stats = analyze_trade_kills(
        trade_events, _iter_match_rounds(competitive_matches), riot_id
    )
    
    target_stats = [s for s in player_stats if s.riot_id.lower() == riot_id.lower()]
    team_name = target_stats[0].player_name.split("#")[0] if target_stats else "Unknown"
    
    site_kills = defaultdict(int)
    site_deaths = defaultdict(int)
    if target_stats:
        target_riot = target_stats[0].riot_id.lower()
        for event in events:
            if not event.site:
                continue
            if event.killer_name.lower() == target_riot:
                site_kills[event.site] += 1
            if event.victim_name.lower() == target_riot:
                site_deaths[event.site] += 1
    
    team_summary = TeamFirstContactSummary(
        team_name=team_name,
        total_rounds=len(events),
        attack_first_kills=sum(s.attack_first_kills for s in target_stats),
        attack_first_deaths=sum(s.attack_first_deaths for s in target_stats),
        defense_first_kills=sum(s.defense_first_kills for s in target_stats),
        defense_first_deaths=sum(s.defense_first_deaths for s in target_stats),
        site_first_kills=dict(site_kills),
        site_first_deaths=dict(site_deaths),
        player_stats=target_stats
    )
    
    heatmap_paths = []
    if generate_heatmaps and events:
        heatmap_dir = EXPORTS_DIR / "first_contact_heatmaps" / team_name
        heatmap_paths = await asyncio.to_thread(
            generate_summary_heatmaps, events, player_stats, heatmap_dir, team_name, riot_id
        )
    
    return {
        "success": True,
        "matches_analyzed": len(competitive_matches),
        "total_rounds": len(events),
        "target_player": target_stats[0] if target_stats else None,
        "all_player_stats": player_stats,
        "team_summary": team_summary,
        "events": events,
        "trade_events": trade_events,
        "trade_stats": trade_stats,
        "heatmap_paths": heatmap_paths
    }


__all__ = [
    "FirstContactEvent",
    "TRADE_WINDOW_SECONDS",
    "TradeKillEvent",
    "PlayerTradeStats",
    "PlayerFirstContactStats",
    "TeamFirstContactSummary",
    "fetch_henrikdev_custom_matches",
    "filter_competitive_customs",
    "analyze_first_contacts",
    "_iter_match_rounds",
    "detect_trade_kills",
    "analyze_trade_kills",
    "generate_first_contact_heatmap",
    "generate_team_heatmap",
    "generate_summary_heatmaps",
    "format_player_stats_table",
    "format_trade_stats_table",
    "format_team_summary_table",
    "analyze_player_first_contacts",
    "get_map_bounds",
    "get_map_site_centers",
]
