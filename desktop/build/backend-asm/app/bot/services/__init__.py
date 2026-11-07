"""
Services package for Valorant Analyst Bot.
"""
from .rate_limiter import rate_limiter
from .cache import cache, cached_fetch
from .first_contact_analyzer import (
    FirstContactEvent,
    TradeKillEvent,
    PlayerTradeStats,
    PlayerFirstContactStats,
    TeamFirstContactSummary,
    fetch_henrikdev_custom_matches,
    filter_competitive_customs,
    analyze_first_contacts,
    detect_trade_kills,
    analyze_trade_kills,
    generate_first_contact_heatmap,
    generate_team_heatmap,
    generate_summary_heatmaps,
    format_player_stats_table,
    format_trade_stats_table,
    format_team_summary_table,
    analyze_player_first_contacts,
)
from .vrf_correlator import (
    CorrelationResult,
    correlate_replay,
    correlate_replay_sync,
)

__all__ = [
    "rate_limiter",
    "cache",
    "cached_fetch",
    "FirstContactEvent",
    "TradeKillEvent",
    "PlayerTradeStats",
    "PlayerFirstContactStats",
    "TeamFirstContactSummary",
    "fetch_henrikdev_custom_matches",
    "filter_competitive_customs",
    "analyze_first_contacts",
    "detect_trade_kills",
    "analyze_trade_kills",
    "generate_first_contact_heatmap",
    "generate_team_heatmap",
    "generate_summary_heatmaps",
    "format_player_stats_table",
    "format_trade_stats_table",
    "format_team_summary_table",
    "analyze_player_first_contacts",
    # VRF correlator (kept: replay+HenrikDev correlation helpers)
    "CorrelationResult",
    "correlate_replay",
    "correlate_replay_sync",
]
