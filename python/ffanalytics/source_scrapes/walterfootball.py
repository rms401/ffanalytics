"""WalterFootball projections (``R/source_scrapes.R:568-676``).

Season-long only, published as a spreadsheet with one sheet per position.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

import pandas as pd
import requests

from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import walterfootball_columns
from ._common import USER_AGENT, cached_positions, store_scrape

__all__ = ["scrape_walterfootball"]

DRAFT = True
WEEKLY = False
POSITIONS = ("QB", "RB", "WR", "TE", "K")

_SHEETS = {"QB": "QBs", "RB": "RBs", "WR": "WRs", "TE": "TEs", "K": "Ks"}
_KEEP_PATTERN = re.compile(
    r"^Pass|^Rush|^Catch|^Rec|^Reg TD$|^Int|^FG|^XP|name$|^player|^Team$|^Pos|^Bye",
    re.IGNORECASE,
)


def scrape_walterfootball(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape season-long projections from walterfootball.com."""
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    positions = list(pos)

    cached = cached_positions(
        "WalterFootball Scrape", "walterfootball_scrape.rds", positions
    )
    if cached is not None:
        return ScrapeResult(cached, season=season, week=week)

    url = f"http://walterfootball.com/fantasy{season}rankingsexcel.xlsx"
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as handle:
        handle.write(response.content)
        workbook_path = Path(handle.name)

    def scrape_one(position: str) -> pd.DataFrame:
        print(f"Scraping {position} projections from\n  {url}")
        frame = pd.read_excel(workbook_path, sheet_name=_SHEETS[position])

        if position in ("QB", "WR"):
            frame["First Name"] = frame["First Name"].replace("Marcua", "Marcus")

        frame.insert(
            0, "Player",
            frame["First Name"].astype(str) + " " + frame["Last Name"].astype(str),
        )
        frame = frame.loc[:, frame.notna().any()]
        frame = frame[[c for c in frame.columns if _KEEP_PATTERN.search(str(c))]]
        frame = frame.rename(
            columns={"Last Name": "last_name", "First Name": "first_name",
                     "Pos": "position", "BYE": "Bye"}
        )

        # NOTE: R passes the literal string "fantasypro_id" as the id values
        # here (R/source_scrapes.R:642), so its crosswalk lookup finds nothing
        # and the name cascade does the work. Reproduced by passing no id.
        frame["id"] = get_mfl_id(
            player_name=frame["Player"], pos=frame.get("position")
        ).to_numpy()
        frame["data_src"] = "WalterFootball"
        frame = frame.drop(columns=["last_name", "first_name"], errors="ignore")

        frame.columns = rename_vec(list(frame.columns), walterfootball_columns)
        names = list(frame.columns)

        # WalterFootball reports one combined TD number; split it in proportion
        # to rushing and receiving yardage.
        if "reg_tds" in names:
            if "rush_yds" in names and "rec_yds" in names:
                total = frame["rush_yds"] + frame["rec_yds"]
                frame["rush_tds"] = (frame["rush_yds"] / total * frame["reg_tds"]).where(
                    total != 0, 0
                )
                frame["rec_tds"] = (frame["rec_yds"] / total * frame["reg_tds"]).where(
                    total != 0, 0
                )
                frame = frame.drop(columns="reg_tds")
            elif "rush_yds" in names or "rec_yds" in names:
                yards = "rush_yds" if "rush_yds" in names else "rec_yds"
                frame = frame.rename(
                    columns={"reg_tds": yards.replace("_yds", "_tds")}
                )

        leading = [c for c in ("id", "player", "pos", "team") if c in frame.columns]
        return frame[leading + [c for c in frame.columns if c not in leading]]

    frames = lapply_safe(positions, scrape_one)
    result = {p: f for p, f in zip(positions, frames) if f is not None}
    store_scrape(result, "walterfootball_scrape.rds")
    return ScrapeResult(result, season=season, week=week)
