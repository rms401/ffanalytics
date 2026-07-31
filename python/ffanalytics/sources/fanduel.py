"""FanDuel Research projections (what used to be NumberFire).

One GraphQL request per position group.  FanDuel takes its season-long
projections down out of season, in which case this returns nothing.
"""

from __future__ import annotations

import pandas as pd

from ..players import resolve_ids
from ..stats import to_numeric_frame
from ._http import USER_AGENT, Session
from .columns import FANDUEL, rename

__all__ = ["scrape_fanduel"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
DRAFT = True
WEEKLY = True

_ENDPOINT = "https://fdresearch-api.fanduel.com/graphql"
_GROUPS = {"NFL_SKILL": ("QB", "RB", "WR", "TE"), "NFL_KICKER": ("K",),
           "NFL_D_ST": ("DST",)}

_IDENTITY = """
        player { numberFireId name position }
        team { numberFireId name abbreviation }
        gameInfo { homeTeam { abbreviation } awayTeam { abbreviation } gameTime }
"""

_QUERY = """
  query GetProjections($input: ProjectionsInput!) {
    getProjections(input: $input) {
      ... on NflSkill {
%(identity)s
        completionsAttempts passingYards passingTouchdowns interceptionsThrown
        rushingAttempts rushingYards rushingTouchdowns
        receptions targets receivingYards receivingTouchdowns
        fantasy positionRank overallRank
      }
      ... on NflKicker {
%(identity)s
        extraPointsAttempted extraPointsMade
        fieldGoalsAttempted fieldGoalsMade
        fieldGoalsMade0To19 fieldGoalsMade20To29 fieldGoalsMade30To39
        fieldGoalsMade40To49 fieldGoalsMade50Plus
        fantasy positionRank
      }
      ... on NflDefenseSt {
%(identity)s
        pointsAllowed yardsAllowed sacks interceptions fumblesRecovered
        touchdowns fantasy positionRank
      }
    }
  }
""" % {"identity": _IDENTITY}


def _flatten(record: dict, prefix: str = "") -> dict:
    out = {}
    for key, value in record.items():
        name = f"{prefix}_{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, name))
        else:
            out[name] = value
    return out


def scrape_fanduel(positions=POSITIONS, season=None, week=0,
                   **_) -> dict[str, pd.DataFrame]:
    positions = list(positions)
    projection_type = "WEEKLY" if week > 0 else "YEARLY"
    session = Session()
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://www.fanduel.com",
        "User-Agent": USER_AGENT,
    }

    collected = []
    for group, members in _GROUPS.items():
        if not any(position in members for position in positions):
            continue
        print(f"  FanDuel {'/'.join(members)}")
        payload = session.post_json(_ENDPOINT, {
            "query": _QUERY,
            "operationName": "GetProjections",
            "variables": {"input": {"type": projection_type, "position": group,
                                    "sport": "NFL"}},
        }, headers=headers)

        records = (payload.get("data") or {}).get("getProjections") or []
        if not records:
            continue

        frame = pd.DataFrame([_flatten(record) for record in records])
        frame.columns = rename(frame.columns, FANDUEL)
        frame = to_numeric_frame(frame, exclude=("src_id",))
        frame["data_src"] = "FanDuel"
        frame["pos"] = frame["pos"].replace("D", "DST")
        frame["src_id"] = frame["src_id"].astype(str)
        frame["id"] = resolve_ids(
            frame["src_id"], "numfire_id",
            name=frame.get("player"), pos=frame["pos"], team=frame.get("team"),
        ).to_numpy()

        if "completionsAttempts" in frame.columns:
            split = frame.pop("completionsAttempts").astype(str).str.split("/", n=1,
                                                                          expand=True)
            frame["pass_comp"] = pd.to_numeric(split[0], errors="coerce")
            frame["pass_att"] = pd.to_numeric(split[1], errors="coerce")

        collected.append(frame[frame["pos"].isin(members)])

    if not collected:
        print("  FanDuel: no projections published for this period")
        return {}

    combined = pd.concat(collected, ignore_index=True)
    combined = combined.drop(columns=["salary", "value"], errors="ignore")
    return {
        position: group.reset_index(drop=True)
        for position, group in combined.groupby("pos")
        if position in positions
    }
