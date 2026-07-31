"""ESPN projections (``R/source_scrapes.R:1271-1424``).

Reads the same JSON API the ESPN fantasy site uses, driven by an
``X-Fantasy-Filter`` header.  Individual defensive players need a league id
because they are not in the public default league.
"""

from __future__ import annotations

import pandas as pd

from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import espn_columns, espn_team_nums
from ._common import USER_AGENT, Session, convert_types, rate_limit

__all__ = ["scrape_espn"]

DRAFT = True
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")

_SLOT_NUMBERS = {
    "QB": 0, "RB": 2, "WR": 4, "TE": 6, "K": 17, "DST": 16,
    "DT": 8, "DE": 9, "LB": 10, "DL": 11, "CB": 12, "DB": 14,
}
_LIMITS = {
    "QB": 42, "RB": 100, "WR": 150, "TE": 60, "K": 35, "DST": 32,
    "DL": 90, "DB": 60, "LB": 60,
}
_IDP = ("DL", "DB", "LB")


def _fantasy_filter(pos_idx: int, limit: int, season: int, week: int) -> str:
    split_id = 0 if week == 0 else 1
    return (
        '{"players":{'
        f'"filterSlotIds":{{"value":[{pos_idx}]}},'
        '"filterStatsForSourceIds":{"value":[1]},'
        f'"filterStatsForSplitTypeIds":{{"value":[{split_id}]}},'
        '"sortAppliedStatTotal":{"sortAsc":false,"sortPriority":3,'
        f'"value":"11{season}{week}"}},'
        '"sortDraftRanks":{"sortPriority":2,"sortAsc":true,"value":"PPR"},'
        '"sortPercOwned":{"sortAsc":false,"sortPriority":4},'
        f'"limit":{limit},'
        '"offset":0,'
        '"filterRanksForScoringPeriodIds":{"value":[2]},'
        '"filterRanksForRankTypes":{"value":["PPR"]},'
        '"filterRanksForSlotIds":{"value":[0,2,4,6,17,16,15]},'
        '"filterStatsForTopScoringPeriodIds":{"value":2,'
        f'"additionalValue":["00{season}","10{season}","11{season}{week}",'
        f'"02{season}"]}}}}}}'
    )


def scrape_espn(pos=POSITIONS, season=None, week=None, espn_league_id=None,
                **kwargs) -> ScrapeResult:
    """Scrape projections from ESPN's fantasy API."""
    print("\nThe ESPN scrape uses a 2 second delay between pages")
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    positions = list(pos)

    if any(position in _IDP for position in positions) and espn_league_id is None:
        print("Must provide a valid espn_league_id to get DL, LB, and DB")
        positions = [p for p in positions if p not in _IDP]

    session = Session()

    def scrape_one(position: str) -> pd.DataFrame:
        if position != positions[0]:
            rate_limit()

        pos_idx = _SLOT_NUMBERS[position]
        limit = _LIMITS[position]

        if position in _IDP:
            url = (
                "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
                f"{season}/segments/0/leagues/{espn_league_id}"
                f"?scoringPeriodId={week}&view=kona_player_info"
            )
            pos_cols = espn_columns.filter_values(lambda v: not v.startswith("dst_"))
        else:
            url = (
                "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
                f"{season}/segments/0/leaguedefaults/3"
                "?scoringPeriodId=0&view=kona_player_info"
            )
            pos_cols = espn_columns.filter_values(lambda v: not v.startswith("idp_"))

        print(f"Scraping {position} projections from\n  "
              "https://fantasy.espn.com/football/players/projections")

        payload = session.get_json(
            url,
            headers={
                "Accept": "application/json",
                "X-Fantasy-Source": "kona",
                "X-Fantasy-Filter": _fantasy_filter(pos_idx, limit, season, week),
                "User-Agent": USER_AGENT,
            },
        )["players"]

        lookup = pos_cols.to_dict()
        rows = []
        for entry in payload:
            stats = entry.get("player", {}).get("stats") or []
            if not stats:  # bye weeks come back without stats
                continue
            values = stats[0].get("stats", {})
            row = {
                lookup[key]: round(value)
                for key, value in values.items()
                if key in lookup
            }
            row["espn_id"] = entry.get("id")
            row["player_name"] = entry.get("player", {}).get("fullName")
            row["team"] = espn_team_nums.get(str(entry.get("player", {}).get("proTeamId")))
            row["position"] = position
            rows.append(row)

        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        frame["data_src"] = "ESPN"

        if position == "DST":
            # ESPN has served negative ids for DSTs, so match on team instead.
            frame["id"] = get_mfl_id(
                team=frame["team"], pos=frame["position"]
            ).to_numpy()
        else:
            frame["id"] = get_mfl_id(
                frame["espn_id"].astype(str),
                id_col_name="espn_id",
                player_name=frame["player_name"],
                pos=frame["position"],
                team=frame["team"],
            ).to_numpy()

        frame = frame.rename(
            columns={"espn_id": "src_id", "position": "pos", "player_name": "player"}
        )
        frame["src_id"] = frame["src_id"].astype(str)
        leading = ["id", "src_id", "pos", "player", "team"]
        frame = frame[leading + [c for c in frame.columns if c not in leading]]
        return convert_types(frame)

    frames = lapply_safe(positions, scrape_one)
    result = {p: f for p, f in zip(positions, frames) if f is not None}
    return ScrapeResult(result, season=season, week=week)
