"""Scrape and aggregate fantasy football projections.

A Python port of the `ffanalytics <https://github.com/FantasyFootballAnalytics/ffanalytics>`_
R package.  The public names below mirror the R package's exports.

    import ffanalytics as ffa

    scrape = ffa.scrape_data(src=["CBS", "NFL", "FanDuel"],
                             pos=["QB", "RB", "WR", "TE", "DST"],
                             season=None, week=None)

    projections = ffa.projections_table(scrape)
    projections = ffa.add_ecr(projections)
    projections = ffa.add_adp(projections)
    projections = ffa.add_aav(projections)
    projections = ffa.add_uncertainty(projections)
    projections = ffa.add_player_info(projections)

``add_ecr`` must be called before ``add_uncertainty``, which uses ``sd_ecr``.
"""

from .adp_functions import (
    cbs_draft,
    espn_draft,
    ffc_draft,
    get_adp,
    mfl_draft,
    nfl_draft,
    rts_draft,
    yahoo_draft,
)
from .caching import clear_ffanalytics_cache, list_ffanalytics_cache
from .calc_projections import (
    add_aav,
    add_adp,
    add_ecr,
    add_player_info,
    add_uncertainty,
    default_baseline,
    default_threshold,
    default_weights,
    default_weights_by_src,
    projections_table,
    source_points,
)
from .custom_scoring import custom_scoring, make_scoring_tables
from .player_data import player_table
from .results import ProjectionsTable, ScrapeResult
from .scoring_rules import scoring, scoring_empty
from .scrape_ecr import scrape_ecr
from .scrape_funcs import POSITIONS, SOURCE_NAMES, scrape_data

__version__ = "3.1.17"

__all__ = [
    # scraping
    "scrape_data",
    "scrape_ecr",
    "SOURCE_NAMES",
    "POSITIONS",
    # scoring and aggregation
    "scoring",
    "scoring_empty",
    "custom_scoring",
    "make_scoring_tables",
    "source_points",
    "projections_table",
    # enrichment
    "add_ecr",
    "add_adp",
    "add_aav",
    "add_uncertainty",
    "add_player_info",
    # ADP / AAV sources
    "get_adp",
    "rts_draft",
    "cbs_draft",
    "yahoo_draft",
    "nfl_draft",
    "mfl_draft",
    "ffc_draft",
    "espn_draft",
    # defaults
    "default_weights",
    "default_weights_by_src",
    "default_baseline",
    "default_threshold",
    # data and containers
    "player_table",
    "ScrapeResult",
    "ProjectionsTable",
    # cache
    "clear_ffanalytics_cache",
    "list_ffanalytics_cache",
]
