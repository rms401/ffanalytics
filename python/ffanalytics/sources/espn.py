"""ESPN projections, read from the JSON API their fantasy site uses."""

from __future__ import annotations

import pandas as pd

from ..players import resolve_ids
from ..stats import to_numeric_frame
from ._http import USER_AGENT, Session, for_each_position, polite_pause
from .columns import ESPN, ESPN_IDP, ESPN_TEAM_ABBREVIATIONS

__all__ = ["scrape_espn"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")
DRAFT = True
WEEKLY = True

_SLOTS = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "K": 17, "DST": 16, "LB": 10, "DL": 11, "DB": 14}
_LIMITS = {
    "QB": 60, "RB": 120, "WR": 160, "TE": 80, "K": 40, "DST": 32,
    "DL": 90, "LB": 60, "DB": 60,
}
_IDP = ("DL", "LB", "DB")

_PUBLIC = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
    "{season}/segments/0/leaguedefaults/3?scoringPeriodId=0&view=kona_player_info"
)
_LEAGUE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
    "{season}/segments/0/leagues/{league}?scoringPeriodId={week}&view=kona_player_info"
)


def _filter_header(slot: int, limit: int, season: int, week: int) -> str:
    """ESPN takes its query as a JSON blob in a request header."""
    split = 0 if week == 0 else 1
    return (
        '{"players":{'
        f'"filterSlotIds":{{"value":[{slot}]}},'
        '"filterStatsForSourceIds":{"value":[1]},'
        f'"filterStatsForSplitTypeIds":{{"value":[{split}]}},'
        '"sortAppliedStatTotal":{"sortAsc":false,"sortPriority":3,'
        f'"value":"11{season}{week}"}},'
        '"sortDraftRanks":{"sortPriority":2,"sortAsc":true,"value":"PPR"},'
        '"sortPercOwned":{"sortAsc":false,"sortPriority":4},'
        f'"limit":{limit},"offset":0,'
        '"filterRanksForScoringPeriodIds":{"value":[2]},'
        '"filterRanksForRankTypes":{"value":["PPR"]},'
        '"filterRanksForSlotIds":{"value":[0,2,4,6,17,16,15]},'
        '"filterStatsForTopScoringPeriodIds":{"value":2,'
        f'"additionalValue":["00{season}","10{season}","11{season}{week}","02{season}"]}}}}}}'
    )


def scrape_espn(positions=POSITIONS, season=None, week=0, espn_league_id=None,
                **_) -> dict[str, pd.DataFrame]:
    positions = list(positions)
    if espn_league_id is None:
        skipped = [p for p in positions if p in _IDP]
        if skipped:
            print(f"  ESPN: skipping {', '.join(skipped)} (needs espn_league_id)")
        positions = [p for p in positions if p not in _IDP]

    session = Session()

    def scrape_one(position: str) -> pd.DataFrame:
        idp = position in _IDP
        url = (
            _LEAGUE.format(season=season, league=espn_league_id, week=week) if idp
            else _PUBLIC.format(season=season)
        )
        print(f"  ESPN {position}")
        payload = session.json(url, headers={
            "Accept": "application/json",
            "X-Fantasy-Source": "kona",
            "X-Fantasy-Filter": _filter_header(_SLOTS[position], _LIMITS[position],
                                               season, week),
            "User-Agent": USER_AGENT,
        })["players"]
        polite_pause()

        stat_names = ESPN_IDP if idp else ESPN
        keep = (lambda v: not v.startswith("dst_")) if idp else (lambda v: not v.startswith("idp_"))
        stat_names = {k: v for k, v in stat_names.items() if keep(v)}

        rows = []
        for entry in payload:
            player = entry.get("player") or {}
            stats = player.get("stats") or []
            if not stats:  # a bye week comes back with no stat block
                continue
            row = {
                stat_names[key]: value
                for key, value in (stats[0].get("stats") or {}).items()
                if key in stat_names
            }
            row["src_id"] = str(entry.get("id"))
            row["player"] = player.get("fullName")
            row["team"] = ESPN_TEAM_ABBREVIATIONS.get(str(player.get("proTeamId")))
            row["pos"] = position
            rows.append(row)

        frame = pd.DataFrame(rows)
        if frame.empty:
            return frame
        frame["data_src"] = "ESPN"

        if position == "DST":
            # ESPN has served negative ids for team defenses; match on team.
            frame["id"] = resolve_ids(pos="DST", team=frame["team"]).to_numpy()
        else:
            frame["id"] = resolve_ids(
                frame["src_id"], "espn_id",
                name=frame["player"], pos=frame["pos"], team=frame["team"],
            ).to_numpy()
        return to_numeric_frame(frame, exclude=("id", "src_id"))

    return for_each_position(positions, scrape_one)
