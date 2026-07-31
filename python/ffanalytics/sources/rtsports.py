"""RTSports projections, served as JSON by freedraftguide.com.  Season-long only."""

from __future__ import annotations

import pandas as pd

from ..players import resolve_ids
from ..stats import to_numeric_frame
from ._http import Session, for_each_position, polite_pause
from .columns import RTSPORTS, RTSPORTS_POSITION_IDS, rename

__all__ = ["scrape_rtsports"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
DRAFT = True
WEEKLY = False

_URL = "https://www.freedraftguide.com/football/draft-guide-rankings-provider.php"
_PAUSE = 5.0


def _players(payload):
    """Yield each player object out of the nested response."""
    if isinstance(payload, dict):
        if "player_id" in payload and "stats" in payload:
            yield payload
            return
        for value in payload.values():
            yield from _players(value)
    elif isinstance(payload, list):
        for value in payload:
            yield from _players(value)


def scrape_rtsports(positions=POSITIONS, season=None, week=0, **_) -> dict[str, pd.DataFrame]:
    if week > 0:
        print("  RTSports: season-long projections only, skipping")
        return {}
    session = Session()

    def scrape_one(position: str) -> pd.DataFrame:
        url = f"{_URL}?POS={RTSPORTS_POSITION_IDS[position]}"
        print(f"  RTSports {position}: {url}")
        payload = session.json(url)
        polite_pause(_PAUSE)

        info, stats = [], []
        for player in _players(payload):
            info.append({
                "player_id": player.get("player_id"),
                "stats_id": player.get("stats_id"),
                "name": player.get("name"),
                "nfl_team": player.get("nfl_team"),
            })
            stats.append(player.get("stats") or {})

        if not info:
            return pd.DataFrame()

        stat_frame = pd.DataFrame(stats)
        # The stats block repeats name and team as empty strings; dropping the
        # columns that never vary removes them before they clobber the real ones.
        constant = [c for c in stat_frame.columns if stat_frame[c].nunique(dropna=True) <= 1]
        stat_frame = stat_frame.drop(columns=constant)

        frame = pd.concat(
            [pd.DataFrame(info).reset_index(drop=True), stat_frame.reset_index(drop=True)],
            axis=1,
        )
        if (position in ("RB", "WR", "TE")
                and "pass_yds" in frame.columns
                and "pass_atts" not in frame.columns):
            frame["pass_atts"] = 0

        frame.columns = rename(frame.columns, RTSPORTS)
        frame = to_numeric_frame(frame, exclude=("src_id", "stats_id"))

        if position != "DST" and "site_pts" in frame.columns:
            frame = frame[frame["site_pts"] > 0]

        frame["pos"] = position
        frame["data_src"] = "RTSports"
        frame["id"] = resolve_ids(
            frame["stats_id"].astype(str), "stats_id",
            name=frame.get("player"), pos=frame["pos"], team=frame.get("team"),
        ).to_numpy()
        frame["src_id"] = frame["src_id"].astype(str)
        return frame.drop(columns="stats_id", errors="ignore")

    return for_each_position(positions, scrape_one)
