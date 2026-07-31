"""FantasySharks projections (``R/source_scrapes.R:274-383``).

The only source that serves a CSV, and the only one whose player ids are
already MyFantasyLeague ids, so no name matching is needed.
"""

from __future__ import annotations

import io

import pandas as pd
import requests

from ..helper_funcs import get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec, type_convert_frame
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import fantasysharks_columns
from ._common import USER_AGENT, cached_positions, rate_limit, store_scrape

__all__ = ["scrape_fantasysharks"]

DRAFT = True
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")

#: Season -> the site's "segment" id for the season-long projections page.
_SEASON_SEGMENTS = {
    2025: 842, 2024: 810, 2023: 778, 2022: 746, 2021: 714,
    2020: 682, 2019: 650, 2018: 618, 2017: 586,
}
_POSITION_IDS = {
    "QB": 1, "RB": 2, "WR": 4, "TE": 5, "K": 7, "DST": 6, "DL": 8, "LB": 9, "DB": 10,
}


def scrape_fantasysharks(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape projections from fantasysharks.com."""
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    positions = list(pos)

    cached = cached_positions(
        "FantasySharks Scrape", "fantasysharks_scrape.rds", positions
    )
    if cached is not None:
        return ScrapeResult(cached, season=season, week=week)

    print("\nThe FantasySharks scrape uses a 2 second delay between pages")

    year = _SEASON_SEGMENTS.get(season)
    if week == 0:
        segment = year
    elif week == "ros":
        segment = 813
    elif year is not None and 1 <= week <= 22:
        segment = year + week + 8
    else:
        segment = year

    def scrape_one(position: str) -> pd.DataFrame:
        url = (
            "https://www.fantasysharks.com/apps/bert/forecasts/projections.php"
            f"?csv=1&Sort=&League=-1&Position={_POSITION_IDS[position]}"
            f"&scoring=1&Segment={segment}&uid=4"
        )
        rate_limit()
        print(f"Scraping {position} projections from\n  {url}")

        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
        response.raise_for_status()
        frame = pd.read_csv(io.StringIO(response.text), dtype=str)
        frame = frame.drop(columns="Rank", errors="ignore")
        frame.columns = rename_vec(list(frame.columns), fantasysharks_columns)

        # Two receiving-yardage bonus columns share a header on the site.
        duplicated = frame.columns.duplicated()
        if duplicated.any():
            columns = list(frame.columns)
            for name, index in zip(("rec_50_yds", "rec_100_yds"), duplicated.nonzero()[0]):
                columns[index] = name
            frame.columns = columns

        if position == "K":
            frame.columns = [
                "fg_att" if c == "pass_att" else c for c in frame.columns
            ]
        if position == "DST":
            frame.columns = [
                "dst_int" if c == "pass_int" else c for c in frame.columns
            ]
            frame["id"] = frame["id"].astype(float).map(lambda v: f"{int(v):04d}")
        if position in ("DL", "LB", "DB"):
            frame.columns = [
                c.replace("dst_", "idp_", 1).replace("pass_", "idp_", 1)
                if c.startswith(("dst_", "pass_")) else c
                for c in frame.columns
            ]

        frame["id"] = frame["id"].astype(str)
        frame["data_src"] = "FantasySharks"
        frame = type_convert_frame(frame, exclude=("id",))
        return frame[frame["site_pts"] > 0]

    frames = lapply_safe(positions, scrape_one)
    result = {p: f for p, f in zip(positions, frames) if f is not None}
    store_scrape(result, "fantasysharks_scrape.rds")
    return ScrapeResult(result, season=season, week=week)
