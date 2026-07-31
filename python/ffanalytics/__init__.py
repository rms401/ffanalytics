"""Fantasy football projections, scored for your Sleeper league.

Scrapes projected stats from the public projection sites, reads your league out
of Sleeper, and combines the two into one ranked table:

    import ffanalytics as ffa

    result = ffa.build_league_projections("1234567890")
    result.table.head(30)

Or from a shell::

    python -m ffanalytics --league 1234567890 --db draft.sqlite

The pieces are usable on their own -- :func:`scrape_data` to pull the sites,
:func:`projections_table` to aggregate them under any
:class:`~ffanalytics.scoring.ScoringRules`, and :func:`fetch_league` to read a
league without touching either.
"""

from .adp import get_adp
from .db import write_sqlite
from .ecr import scrape_ecr
from .league import (
    LeagueProjections,
    attach_league_context,
    build_league_projections,
    replacement_ranks,
)
from .players import player_ids, player_table, resolve_ids
from .projections import (
    add_ecr,
    add_player_info,
    add_uncertainty,
    projections_table,
    source_points,
)
from .scoring import DEFAULT_SCORING, PointsAllowedTier, ScoringRules
from .scrape import POSITIONS, Scrape, scrape_data
from .season import current_season, current_week
from .sleeper import (
    SleeperLeague,
    fetch_league,
    leagues_for_user,
    scoring_rules_from_sleeper,
    sleeper_player_map,
)
from .sources import DEFAULT_SOURCES, SOURCES

__version__ = "4.0.0"

__all__ = [
    # the whole thing, one call
    "build_league_projections",
    "LeagueProjections",
    # scraping
    "scrape_data",
    "Scrape",
    "SOURCES",
    "DEFAULT_SOURCES",
    "POSITIONS",
    "scrape_ecr",
    "get_adp",
    # scoring and aggregation
    "ScoringRules",
    "PointsAllowedTier",
    "DEFAULT_SCORING",
    "projections_table",
    "source_points",
    "add_ecr",
    "add_uncertainty",
    "add_player_info",
    # Sleeper
    "fetch_league",
    "SleeperLeague",
    "leagues_for_user",
    "scoring_rules_from_sleeper",
    "sleeper_player_map",
    "replacement_ranks",
    "attach_league_context",
    # players and identity
    "player_table",
    "player_ids",
    "resolve_ids",
    # output
    "write_sqlite",
    # odds and ends
    "current_season",
    "current_week",
]
