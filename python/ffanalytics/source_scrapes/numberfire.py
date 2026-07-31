"""NumberFire projections (``R/source_scrapes.R:386-660``).

The site splits each position page into two tables -- names on the left, stats
on the right -- and the stats table carries a second header row.  Individual
defensive players all come from one "idp" page and are split out afterwards.
"""

from __future__ import annotations

import posixpath
import re

import pandas as pd

from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import numberfire_columns, numberfire_idp_columns
from ._common import (
    Session,
    cached_positions,
    convert_types,
    html_table,
    rate_limit,
    store_scrape,
)

__all__ = ["scrape_numberfire"]

DRAFT = True
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "LB", "DB", "DL")

_SITE_POSITIONS = {
    "QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "K": "k", "DST": "d", "LB": "idp",
}
_IDP = ("LB", "DB", "DL")
_PLAYER_PATTERN = re.compile(r"(.*?)\n.*\n.*?([A-Z]{1,3}),\s*([A-Z]{2,3})", re.S)


def scrape_numberfire(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape projections from numberfire.com."""
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    positions = list(pos)

    cached = cached_positions("NumberFire Scrape", "numberfire_scrape.rds", positions)
    if cached is not None:
        return ScrapeResult(cached, season=season, week=week)

    print("\nThe numberFire scrape uses a 2 second delay between pages")

    # One request covers every IDP position.
    if any(position in _IDP for position in positions):
        site_positions = [p for p in positions if p not in _IDP] + ["LB"]
    else:
        site_positions = list(positions)

    session = Session("https://www.numberfire.com/nfl/fantasy/fantasy-football-projections")

    def scrape_one(position: str) -> pd.DataFrame:
        slug = _SITE_POSITIONS[position]
        if week in (0, "ros"):
            url = f"https://www.numberfire.com/nfl/fantasy/remaining-projections/{slug}"
        else:
            url = f"https://www.numberfire.com/nfl/fantasy/fantasy-football-projections/{slug}"

        rate_limit()
        print(f"Scraping {position} projections from\n  {url}")
        page = session.read_html(url)

        site_ids = [
            posixpath.basename(a.get("href", ""))
            for a in page.cssselect("td[class='player'] a")
        ]

        tables = page.cssselect("table.projection-table")
        if len(tables) < 2:
            return pd.DataFrame()

        players = html_table(tables[0], header=True, trim=False)
        players = players.iloc[1:].reset_index(drop=True)
        extracted = players.iloc[:, 0].str.extract(_PLAYER_PATTERN)
        players = pd.DataFrame(
            {
                "Player": extracted[0].str.strip(),
                "position": extracted[1],
                "team": extracted[2],
            }
        )

        stats = html_table(tables[1], header=True)
        # NumberFire stacks a second header row inside the body.
        stats.columns = [
            " ".join(f"{name} {value}".split())
            for name, value in zip(stats.columns, stats.iloc[0])
        ]
        stats = stats.iloc[1:].reset_index(drop=True)

        if position not in ("LB", "DB"):
            ci_column = next(
                (c for c in stats.columns if "numberFire CI" in c), None
            )
            if ci_column is not None:
                split = stats[ci_column].str.replace(
                    r"(\d|\.)-", r"\1,", regex=True
                ).str.split(",", n=1, expand=True)
                stats = stats.drop(columns=ci_column)
                stats["Lower"], stats["Upper"] = split[0], split[1]

        if position == "QB":
            ca_column = next((c for c in stats.columns if "Passing C/A" in c), None)
            if ca_column is not None:
                split = stats[ca_column].str.split("/", n=1, expand=True)
                stats = stats.drop(columns=ca_column)
                stats["pass_comp"], stats["pass_att"] = split[0], split[1]

        for column in [c for c in stats.columns if c.startswith("Ranks")]:
            stats[column] = stats[column].str.replace("#", "", regex=False)

        rows = min(len(players), len(stats))
        frame = pd.concat(
            [players.iloc[:rows].reset_index(drop=True),
             stats.iloc[:rows].reset_index(drop=True)],
            axis=1,
        )
        frame["src_id"] = site_ids[:rows]
        frame["data_src"] = "NumberFire"
        frame["id"] = get_mfl_id(
            frame["src_id"],
            id_col_name="numfire_id",
            player_name=frame["Player"],
            pos=frame["position"],
            team=frame["team"],
        ).to_numpy()

        mapping = numberfire_idp_columns if position in _IDP else numberfire_columns
        frame.columns = rename_vec(list(frame.columns), mapping)

        frame = frame.replace(r"N/A|\$", "", regex=True)
        frame = convert_types(frame, exclude=("id",))

        if "site_pts" in frame.columns:
            frame = frame[frame["site_pts"] > 0]
        return frame

    frames = lapply_safe(site_positions, scrape_one)
    result = {p: f for p, f in zip(site_positions, frames) if f is not None}

    if any(position in _IDP for position in positions):
        idp_frame = result.pop("LB", None)
        result = {p: f for p, f in result.items() if p not in _IDP}
        if idp_frame is not None and "pos" in idp_frame.columns:
            for position in positions:
                if position in _IDP:
                    subset = idp_frame[idp_frame["pos"] == position]
                    if len(subset):
                        result[position] = subset

    return ScrapeResult(
        {p: result[p] for p in positions if p in result}, season=season, week=week
    )
