"""NFL.com projections."""

from __future__ import annotations

import re

import pandas as pd

from ..players import resolve_ids
from ..stats import to_numeric_frame
from ._http import Session, for_each_position, html_table, polite_pause
from .columns import NFL, NFL_POSITION_IDS, rename

__all__ = ["scrape_nfl"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
DRAFT = True
WEEKLY = True

_ROW_COUNTS = {"QB": 42, "RB": 100, "WR": 150, "TE": 60, "K": 64, "DST": 32}
_PLAYER = re.compile(r"(.*?)\s+\b(QB|RB|WR|TE|K)\b.*?([A-Z]{2,3})")


def scrape_nfl(positions=POSITIONS, season=None, week=0, **_) -> dict[str, pd.DataFrame]:
    session = Session()

    def scrape_one(position: str) -> pd.DataFrame:
        query = (
            f"position={NFL_POSITION_IDS[position]}&count={_ROW_COUNTS[position]}"
            f"&sort=projectedPts&statCategory=projectedStats&statSeason={season}"
        )
        query += (
            "&statType=seasonProjectedStats" if week == 0
            else f"&statType=weekProjectedStats&statWeek={week}"
        )
        url = f"https://fantasy.nfl.com/research/projections?{query}"
        print(f"  NFL {position}: {url}")
        page = session.html(url)
        polite_pause()

        head = html_table(page.cssselect("table > thead")[0], header=False)
        names = rename(
            [" ".join(f"{a} {b}".split()) for a, b in zip(head.iloc[0], head.iloc[1])],
            NFL,
        )
        frame = html_table(page.cssselect("table > tbody")[0], header=False)
        frame.columns = names[: frame.shape[1]]

        site_ids = [
            re.sub(r".*=", "", link.get("href", ""))
            for link in page.cssselect("table td:first-child a.playerName")
        ]

        if position == "DST":
            frame["team"] = frame["team"].str.replace(r"\s+DEF$", "", regex=True)
            frame["pos"] = "DST"
        else:
            parts = frame["player"].str.extract(_PLAYER)
            frame["player"], frame["pos"], frame["team"] = parts[0], parts[1], parts[2]
            # NFL.com prints an interceptions column for every position.
            if position in ("RB", "WR", "TE"):
                frame = frame.drop(columns="pass_int", errors="ignore")

        frame["src_id"] = [str(value) for value in site_ids[: len(frame)]]
        frame["data_src"] = "NFL"
        frame = frame.drop(columns="opp", errors="ignore").replace("-", pd.NA)
        frame = to_numeric_frame(frame, exclude=("id", "src_id"))
        frame = frame[frame["site_pts"].notna() & (frame["site_pts"] > 0)]

        frame["id"] = resolve_ids(
            frame["src_id"], "nfl_id",
            name=None if position == "DST" else frame["player"],
            pos=frame["pos"], team=frame["team"],
        ).to_numpy()
        return frame

    return for_each_position(positions, scrape_one)
