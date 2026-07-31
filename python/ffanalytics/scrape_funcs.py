"""The public scrape entry point.

Ported from ``R/scrape_funcs.R``.
"""

from __future__ import annotations

from typing import Sequence

import pandas as pd

from .helper_funcs import get_scrape_year
from .results import ScrapeResult
from .schedule_data import get_scrape_week
from .source_scrapes import SOURCES

__all__ = ["scrape_data", "SOURCE_NAMES", "POSITIONS"]

#: Every source name ``scrape_data`` accepts.
SOURCE_NAMES = tuple(SOURCES)

#: Every position ``scrape_data`` accepts.
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")

_DEFAULT_SOURCES = (
    "CBS", "ESPN", "FantasyPros", "FantasySharks", "FFToday", "FleaFlicker",
    "NumberFire", "FantasyFootballNerd", "NFL", "RTSports", "Walterfootball",
    "FanDuel",
)

_DEFAULT_ESPN_LEAGUE_ID = 1595759

#: Positions that only exist as intermediate scrapes and never in the output.
_INTERNAL_POSITIONS = ("IDP", "CB", "S", "DT", "DE")


def _match_arg(values, choices, what: str) -> list[str]:
    """``match.arg(several.ok = TRUE)`` -- case-insensitive, order preserved."""
    if isinstance(values, str):
        values = [values]
    lookup = {choice.lower(): choice for choice in choices}
    out = []
    for value in values:
        match = lookup.get(str(value).lower())
        if match is None:
            raise ValueError(
                f"{value!r} is not a valid {what}. Choose from: {', '.join(choices)}"
            )
        if match not in out:
            out.append(match)
    return out


def scrape_data(
    src: Sequence[str] = _DEFAULT_SOURCES,
    pos: Sequence[str] = POSITIONS,
    season: int | None = None,
    week: int | None = None,
    **kwargs,
) -> ScrapeResult:
    """Scrape projections from several sources and combine them by position.

    Returns one :class:`~pandas.DataFrame` per position, with every source's
    rows stacked and a ``data_src`` column identifying them.  Set ``week=0``
    for season-long projections.

    A source that fails, or that does not cover the requested period, is
    reported and skipped rather than aborting the whole scrape.
    """
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week

    sources = _match_arg(src, SOURCE_NAMES, "source")
    positions = _match_arg(pos, POSITIONS, "position")

    if "NumberFire" in sources:
        print("\nHeads up! NumberFire is now FanDuel... Using FanDuel for scrape")
        sources = [s for s in sources if s != "NumberFire"]
        if "FanDuel" not in sources:
            sources.append("FanDuel")

    kwargs.setdefault("espn_league_id", _DEFAULT_ESPN_LEAGUE_ID)

    scraped = []
    for name in sources:
        source = SOURCES[name]

        if week == 0 and not source.draft:
            print(f"\nDraft data not available for {name}")
            continue
        if week > 0 and not source.weekly:
            print(f"\nWeekly data not available for {name}")
            continue

        source_positions = [p for p in positions if p in source.positions]
        if not source_positions and source.positions:
            continue

        try:
            result = source.scrape(
                pos=source_positions or None, season=season, week=week, **kwargs
            )
        except Exception as error:  # noqa: BLE001 - mirrors R's per-source tryCatch
            print(
                f" Uh oh! Error with the {name} scrape.\n"
                f" To get a more specific error message, run:\n"
                f"   ffanalytics.source_scrapes.scrape_{name.lower()}("
                f"pos={source_positions}, season={season}, week={week})\n"
                f"   {error!r}"
            )
            continue

        scraped.append(
            {
                position: _drop_all_na_columns(frame)
                for position, frame in result.items()
                if frame is not None and len(frame)
            }
        )

    combined: dict[str, pd.DataFrame] = {}
    for result in scraped:
        for position, frame in result.items():
            if position in combined:
                combined[position] = pd.concat(
                    [combined[position], frame], ignore_index=True
                )
            else:
                combined[position] = frame

    wanted = [p for p in positions if p not in _INTERNAL_POSITIONS]
    ordered = {p: combined[p] for p in wanted if p in combined and len(combined[p])}
    return ScrapeResult(ordered, season=season, week=week)


def _drop_all_na_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[[column for column in frame.columns if frame[column].notna().any()]]
