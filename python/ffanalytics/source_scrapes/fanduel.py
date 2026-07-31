"""FanDuel (formerly NumberFire) projections (``R/source_scrapes.R:1454-1732``).

Served by a GraphQL endpoint, one request per position group.
"""

from __future__ import annotations

import pandas as pd

from ..helper_funcs import get_mfl_id, get_scrape_year
from ..rcompat.stats import rename_vec, type_convert_frame
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import fanduel_columns
from ._common import USER_AGENT, Session

__all__ = ["scrape_fanduel"]

DRAFT = True
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")

_ENDPOINT = "https://fdresearch-api.fanduel.com/graphql"

_POSITION_GROUPS = {
    "NFL_SKILL": ("QB", "RB", "WR", "TE"),
    "NFL_KICKER": ("K",),
    "NFL_D_ST": ("DST",),
}

_PLAYER_FIELDS = """
        player { numberFireId name position }
        team { numberFireId name abbreviation }
        gameInfo {
          homeTeam { numberFireId name abbreviation }
          awayTeam { numberFireId name abbreviation }
          gameTime
        }
"""

_QUERY = """
  query GetProjections($input: ProjectionsInput!) {
    getProjections(input: $input) {
      ... on NflSkill {
%(player)s
        salary
        value
        completionsAttempts
        passingYards
        passingTouchdowns
        interceptionsThrown
        rushingAttempts
        rushingYards
        rushingTouchdowns
        receptions
        targets
        receivingYards
        receivingTouchdowns
        fantasy
        positionRank
        overallRank
        opponentDefensiveRank
      }
      ... on NflKicker {
%(player)s
        salary
        value
        extraPointsAttempted
        extraPointsMade
        fieldGoalsAttempted
        fieldGoalsMade
        fieldGoalsMade0To19
        fieldGoalsMade20To29
        fieldGoalsMade30To39
        fieldGoalsMade40To49
        fieldGoalsMade50Plus
        fantasy
        positionRank
        opponentDefensiveRank
      }
      ... on NflDefenseSt {
%(player)s
        salary
        value
        pointsAllowed
        yardsAllowed
        sacks
        interceptions
        fumblesRecovered
        touchdowns
        fantasy
        positionRank
        opponentOffensiveRank
      }
      ... on NflDefensePlayer {
%(player)s
        tackles
        sacks
        interceptions
        touchdowns
        passesDefended
        fumblesRecovered
        opponentOffensiveRank
      }
    }
  }
""" % {"player": _PLAYER_FIELDS}


def _flatten(record: dict, prefix: str = "") -> dict:
    """Flatten nested GraphQL objects into ``parent_child`` columns."""
    out = {}
    for key, value in record.items():
        name = f"{prefix}_{key}" if prefix else key
        if isinstance(value, dict):
            out.update(_flatten(value, name))
        else:
            out[name] = value
    return out


def scrape_fanduel(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape projections from FanDuel's research API."""
    # NOTE: R tests `is.null(week)` when defaulting `season`
    # (R/source_scrapes.R:1457). Reproduced so the season default behaves the
    # same way it does in the R package.
    if week is None:
        season = get_scrape_year() if season is None else season
        week = get_scrape_week()
    elif season is None:
        season = get_scrape_year()

    positions = list(pos) if pos else list(POSITIONS)
    proj_type = "WEEKLY" if week > 0 else "REMAINING"

    print(f"\nScraping FanDuel projections for {', '.join(positions)}...")

    session = Session()
    headers = {
        "Accept": "*/*",
        "Content-Type": "application/json",
        "Origin": "https://www.fanduel.com",
        "Accept-Language": "en-US,en;q=0.9",
        "User-Agent": USER_AGENT,
    }

    groups = [
        group for group, members in _POSITION_GROUPS.items()
        if any(position in members for position in positions)
    ]

    collected = []
    for group in groups:
        payload = {
            "query": _QUERY,
            "variables": {
                "input": {"type": proj_type, "position": group, "sport": "NFL"}
            },
            "operationName": "GetProjections",
        }
        response = session.post_json(_ENDPOINT, payload, headers=headers)
        records = (response.get("data") or {}).get("getProjections") or []
        if not records:
            continue

        frame = pd.DataFrame([_flatten(record) for record in records])
        frame.columns = rename_vec(list(frame.columns), fanduel_columns)
        frame = type_convert_frame(frame, exclude=("src_id",))

        frame["data_src"] = "FanDuel"
        frame["proj_type"] = proj_type
        frame["pos"] = frame["pos"].replace("D", "DST")
        frame["id"] = get_mfl_id(
            frame["src_id"].astype(str),
            id_col_name="numfire_id",
            player_name=frame.get("player"),
            team=frame.get("team"),
            pos=frame["pos"],
        ).to_numpy()
        frame["src_id"] = frame["src_id"].astype(str)

        if "completionsAttempts" in frame.columns:
            split = frame["completionsAttempts"].astype(str).str.split(
                "/", n=1, expand=True
            )
            frame = frame.drop(columns="completionsAttempts")
            frame["pass_comp"] = pd.to_numeric(split[0], errors="coerce")
            frame["pass_att"] = pd.to_numeric(split[1], errors="coerce")

        collected.append(frame[frame["pos"].isin(_POSITION_GROUPS[group])])

    if not collected:
        return ScrapeResult({}, season=season, week=week)

    out = pd.concat(collected, ignore_index=True)
    leading = ["id", "src_id", "player", "pos", "team"]
    out = out[leading + [c for c in out.columns if c not in leading]]

    result = {}
    for position in positions:
        subset = out[out["pos"] == position]
        if subset.empty:
            continue
        subset = subset.drop(columns=["salary", "value"], errors="ignore")
        result[position] = subset[[c for c in subset.columns if subset[c].notna().any()]]

    return ScrapeResult(result, season=season, week=week)
