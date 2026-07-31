"""NFL.com projections (``R/source_scrapes.R:129-271``)."""

from __future__ import annotations

import re

import pandas as pd

from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import nfl_columns, nfl_pos_idx
from ._common import (
    Session,
    cached_positions,
    convert_types,
    drop_all_na_columns,
    html_table,
    rate_limit,
    store_scrape,
)

__all__ = ["scrape_nfl"]

DRAFT = True
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")

_RECORD_COUNTS = {"QB": 42, "RB": 100, "WR": 150, "TE": 60, "K": 64, "DST": 32}
_PLAYER_PATTERN = re.compile(r"(.*?)\s+\b(QB|RB|WR|TE|K)\b.*?([A-Z]{2,3})")


def scrape_nfl(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape projections from fantasy.nfl.com."""
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    positions = list(pos)

    cached = cached_positions("NFL Scrape", "nfl_scrape.rds", positions)
    if cached is not None:
        return ScrapeResult(cached, season=season, week=week)

    print("\nThe NFL.com scrape uses a 2 second delay between pages")

    first = nfl_pos_idx[positions[0]]
    session = Session(
        f"https://fantasy.nfl.com/research/projections?position={first}"
        f"&sort=projectedPts&statCategory=projectedStats&statSeason={season}"
        f"&statType=seasonProjectedStats"
    )

    def scrape_one(position: str) -> pd.DataFrame:
        pos_idx = nfl_pos_idx[position]
        count = _RECORD_COUNTS[position]

        if week == 0:
            url = (
                f"https://fantasy.nfl.com/research/projections?position={pos_idx}"
                f"&count={count}&sort=projectedPts&statCategory=projectedStats"
                f"&statSeason={season}&statType=seasonProjectedStats"
            )
        else:
            url = (
                f"https://fantasy.nfl.com/research/projections?position={pos_idx}"
                f"&count={count}&sort=projectedPts&statCategory=projectedStats"
                f"&statSeason={season}&statType=weekProjectedStats&statWeek={week}"
            )
        print(f"Scraping {position} projections from\n  {url}")
        page = session.read_html(url)

        site_id = [
            re.sub(r".*=", "", link.get("href", ""))
            for link in page.cssselect("table td:first-child a.playerName")
        ]

        head = html_table(page.cssselect("table > thead")[0], header=False)
        col_names = [
            " ".join(f"{a} {b}".split())
            for a, b in zip(head.iloc[0], head.iloc[1])
        ]
        col_names = rename_vec(col_names, nfl_columns)

        frame = html_table(page.cssselect("table > tbody")[0], header=False)
        frame.columns = col_names[: frame.shape[1]]

        if position != "DST":
            extracted = frame["player"].str.extract(_PLAYER_PATTERN)
            frame["player"], frame["pos"], frame["team"] = (
                extracted[0], extracted[1], extracted[2]
            )
        else:
            frame["team"] = frame["team"].str.replace(r"\s+DEF$", "", regex=True)
            frame["pos"] = "DST"

        if position in ("RB", "WR", "TE") and "pass_int" in frame.columns:
            frame = frame.drop(columns="pass_int")

        frame["data_src"] = "NFL"
        frame["nfl_id"] = [str(value) for value in site_id[: len(frame)]]
        frame = frame.drop(columns="opp", errors="ignore")

        frame = frame.replace("-", pd.NA)
        frame = convert_types(frame, exclude=("id", "nfl_id"))
        frame = frame[frame["site_pts"].notna() & (frame["site_pts"] > 0)]

        frame["id"] = get_mfl_id(
            frame["nfl_id"],
            id_col_name="nfl_id",
            player_name=None if position == "DST" else frame["player"],
            pos=frame["pos"],
            team=frame["team"],
        ).to_numpy()
        frame = frame.rename(columns={"nfl_id": "src_id"})

        leading = [c for c in ("id", "src_id", "player", "pos", "team") if c in frame.columns]
        frame = frame[leading + [c for c in frame.columns if c not in leading]]

        rate_limit()
        return drop_all_na_columns(frame)

    frames = lapply_safe(positions, scrape_one)
    result = {p: f for p, f in zip(positions, frames) if f is not None}
    store_scrape(result, "nfl_scrape.rds")
    return ScrapeResult(result, season=season, week=week)
