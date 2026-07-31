"""RTSports projections (``R/source_scrapes.R:1192-1268``).

Season-long only, served as JSON by freedraftguide.com.
"""

from __future__ import annotations

import pandas as pd

from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec, type_convert_frame
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import rts_columns, rts_pos_idx
from ._common import Session, rate_limit

__all__ = ["scrape_rtsports"]

DRAFT = True
WEEKLY = False
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")

_BASE_URL = "https://www.freedraftguide.com/football/draft-guide-rankings-provider.php"
_RATE_LIMIT_SECONDS = 5


def _iter_players(payload):
    """Yield each player object from the nested response."""
    if isinstance(payload, dict):
        if "player_id" in payload and "stats" in payload:
            yield payload
            return
        for value in payload.values():
            yield from _iter_players(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _iter_players(value)


def scrape_rtsports(pos=POSITIONS, season=None, week=0, **kwargs) -> ScrapeResult:
    """Scrape season-long projections from RTSports."""
    print("\nThe RTSports scrape uses a 5 second delay between pages")
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    if week > 0:
        raise ValueError("RTS Sports projections are only available for week 0")

    positions = list(pos)
    session = Session()

    def scrape_one(position: str) -> pd.DataFrame:
        if position != positions[0]:
            rate_limit(_RATE_LIMIT_SECONDS)

        url = f"{_BASE_URL}?POS={rts_pos_idx[position]}"
        print(f"Scraping {position} projections from\n  {url}")
        payload = session.get_json(url)

        info_rows, stat_rows = [], []
        for player in _iter_players(payload):
            info_rows.append(
                {
                    "player_id": player.get("player_id"),
                    "stats_id": player.get("stats_id"),
                    "name": player.get("name"),
                    "nfl_team": player.get("nfl_team"),
                }
            )
            stat_rows.append(player.get("stats") or {})

        info = pd.DataFrame(info_rows)
        stats = pd.DataFrame(stat_rows)
        if info.empty:
            return info

        # Columns where every value is identical carry no information; R drops
        # them with Filter(f = function(x) any(x[1] != x, na.rm = TRUE)).  This
        # has to happen before the join: the stats block repeats `name` and
        # `team` as empty strings, which would otherwise clobber the real ones.
        constant = [c for c in stats.columns if stats[c].nunique(dropna=True) <= 1]
        stats = stats.drop(columns=constant)

        frame = pd.concat(
            [info.reset_index(drop=True), stats.reset_index(drop=True)], axis=1
        )

        if (position in ("RB", "WR", "TE")
                and "pass_yds" in frame.columns
                and "pass_atts" not in frame.columns):
            frame["pass_atts"] = 0

        frame.columns = rename_vec(list(frame.columns), rts_columns)
        frame = type_convert_frame(frame, exclude=("src_id", "stats_id"))

        if position != "DST" and "site_pts" in frame.columns:
            frame = frame[frame["site_pts"] > 0]

        frame["pos"] = position
        frame["id"] = get_mfl_id(
            frame["stats_id"].astype(str),
            id_col_name="stats_id",
            player_name=frame.get("player"),
            team=frame.get("team"),
            pos=frame["pos"],
        ).to_numpy()
        frame["src_id"] = frame["src_id"].astype(str)
        frame = frame.drop(columns="stats_id", errors="ignore")
        frame["data_src"] = "RTSports"

        leading = ["id", "src_id", "pos", "data_src"]
        return frame[leading + [c for c in frame.columns if c not in leading]]

    frames = lapply_safe(positions, scrape_one)
    result = {p: f for p, f in zip(positions, frames) if f is not None}
    return ScrapeResult(result, season=season, week=week)
