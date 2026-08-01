"""FantasySharks projections.

The only source that serves a CSV, and the only one whose player ids already
*are* MyFantasyLeague ids.  It addresses a season-week pair by an opaque
"segment" number that increments by one per week, anchored to a table the site
publishes; the anchors below are extrapolated forward for future seasons.
"""

from __future__ import annotations

import io

import pandas as pd
import requests

from ..stats import to_numeric_frame
from ._http import USER_AGENT, for_each_position, local_text, polite_pause
from .columns import FANTASYSHARKS, FANTASYSHARKS_IDP, rename

__all__ = ["scrape_fantasysharks"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")
DRAFT = True
WEEKLY = True

_POSITION_IDS = {"QB": 1, "RB": 2, "WR": 4, "TE": 5, "K": 7, "DST": 6,
                 "DL": 8, "LB": 9, "DB": 10}

#: Segment id of the season-long projections page for a season.  Successive
#: seasons are 32 apart, so later seasons extrapolate from the last known one.
_SEGMENT_ANCHORS = {2017: 586, 2018: 618, 2019: 650, 2020: 682, 2021: 714,
                    2022: 746, 2023: 778, 2024: 810, 2025: 842}
_SEGMENTS_PER_SEASON = 32


def _segment(season: int, week: int) -> int:
    known = _SEGMENT_ANCHORS.get(season)
    if known is None:
        latest = max(_SEGMENT_ANCHORS)
        known = _SEGMENT_ANCHORS[latest] + (season - latest) * _SEGMENTS_PER_SEASON
    return known if week == 0 else known + week + 8


def scrape_fantasysharks(positions=POSITIONS, season=None, week=0,
                         **_) -> dict[str, pd.DataFrame]:
    segment = _segment(season, week)

    def scrape_one(position: str) -> pd.DataFrame:
        saved = local_text(f"fantasysharks-{position.lower()}",
                           f"sharks-{position.lower()}",
                           f"position={_POSITION_IDS[position]}&")
        if saved is not None:
            path, text = saved
            print(f"  FantasySharks {position}: {path} (local copy)")
        else:
            url = (
                "https://www.fantasysharks.com/apps/bert/forecasts/projections.php"
                f"?csv=1&Sort=&League=-1&Position={_POSITION_IDS[position]}"
                f"&scoring=1&Segment={segment}&uid=4"
            )
            print(f"  FantasySharks {position}: {url}")
            response = requests.get(url, headers={"User-Agent": USER_AGENT},
                                    timeout=120)
            response.raise_for_status()
            polite_pause()
            text = response.text

        idp = position in ("DL", "LB", "DB")
        frame = pd.read_csv(io.StringIO(text), dtype=str)
        frame = frame.drop(columns="Rank", errors="ignore")
        frame.columns = rename(frame.columns, FANTASYSHARKS_IDP if idp else FANTASYSHARKS)

        # The site reuses ">= 50yds"/">= 100yds" for rushing and receiving; the
        # second of each pair is the receiving one.
        duplicated = frame.columns.duplicated()
        if duplicated.any():
            names = list(frame.columns)
            for name, index in zip(("rec_50_yds", "rec_100_yds"),
                                   duplicated.nonzero()[0]):
                names[index] = name
            frame.columns = names

        if position == "K":
            frame.columns = ["fg_att" if c == "pass_att" else c for c in frame.columns]
        if idp:
            # Anything still labelled as a team stat belongs to the defender.
            frame.columns = [
                c.replace("dst_", "idp_", 1) if c.startswith("dst_") else c
                for c in frame.columns
            ]
        if position == "DST":
            frame.columns = ["dst_int" if c == "pass_int" else c for c in frame.columns]
            frame["id"] = frame["id"].astype(float).map(lambda v: f"{int(v):04d}")

        frame["id"] = frame["id"].astype(str)
        frame["data_src"] = "FantasySharks"
        frame["pos"] = position
        frame = to_numeric_frame(frame, exclude=("id",))
        return frame[frame["site_pts"] > 0]

    return for_each_position(positions, scrape_one)
