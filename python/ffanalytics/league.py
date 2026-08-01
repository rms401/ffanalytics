"""Putting the scraped projections and your Sleeper league together.

Three things change once the league is known, and all three matter more than
the projections themselves:

* **scoring** -- a quarterback is worth a different amount at 4 points a
  passing touchdown than at 3, and a tight end is a different asset with a
  reception bonus than without;
* **replacement level** -- "value over replacement" is meaningless until you
  know how many of each position twelve teams actually start, which is what
  turns a superflex league into a quarterback league;
* **availability** -- a projection for a player somebody else already owns is
  not a decision you can act on.

:func:`build_league_projections` does all three and hands back one table.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from .adp import get_adp
from .players import TEAM_CORRECTIONS
from .projections import (
    add_ecr,
    add_player_info,
    add_uncertainty,
    projections_table,
)
from .scrape import Scrape, scrape_data
from .scoring import ScoringRules
from .sleeper import STARTING_SLOTS, SleeperLeague, fetch_league, \
    sleeper_player_map
from .sources import DEFAULT_SOURCES

__all__ = [
    "LeagueProjections",
    "build_league_projections",
    "replacement_ranks",
    "attach_league_context",
]


@dataclass
class LeagueProjections:
    """Projections scored, ranked and annotated for one Sleeper league."""

    league: SleeperLeague
    scoring: ScoringRules
    table: pd.DataFrame
    replacement_ranks: Mapping[str, int]
    unscored_settings: Mapping[str, float] = field(default_factory=dict)
    scrape: Scrape | None = None
    player_map: pd.DataFrame | None = None
    player_map_fetched_at: str | None = None

    @property
    def available(self) -> pd.DataFrame:
        """Only the players nobody in the league holds."""
        if "rostered_by" not in self.table.columns:
            return self.table
        return self.table[self.table["rostered_by"].isna()]

    def top(self, n: int = 25, position: str | None = None) -> pd.DataFrame:
        """The most valuable players, by points over replacement."""
        frame = _one_avg_type(self.table)
        if position:
            frame = frame[frame["pos"] == position]
        columns = [c for c in (
            "rank", "pos", "pos_rank", "tier", "player", "team", "points",
            "points_vor", "floor", "ceiling", "dropoff", "uncertainty", "adp",
            "rostered_by",
        ) if c in frame.columns]
        return frame.nlargest(n, "points_vor")[columns].reset_index(drop=True)

    def report(self) -> str:
        """A short plain-text summary of what was built."""
        lines = [
            self.league.describe(),
            f"  scoring: {_scoring_summary(self.scoring, self.league)}",
            "  replacement level: " + ", ".join(
                f"{position}{rank}" for position, rank
                in sorted(self.replacement_ranks.items())
            ),
            f"  players projected: {len(_one_avg_type(self.table))}",
        ]
        if self.scrape is not None:
            lines.append("  sources used: " + ", ".join(self.scrape.sources()))
        if self.unscored_settings:
            lines.append(
                "  scoring settings with no projectable stat (points your "
                "league awards that no source projects):"
            )
            for key, value in sorted(self.unscored_settings.items()):
                lines.append(f"    {key} = {value:g}")
        return "\n".join(lines)


def _scoring_summary(scoring: ScoringRules, league: SleeperLeague) -> str:
    parts = [f"{scoring.points_per_reception('WR'):g} PPR"]
    if scoring.points_per_reception("TE") != scoring.points_per_reception("WR"):
        parts.append(f"TE premium ({scoring.points_per_reception('TE'):g})")
    parts.append(f"{scoring.stats.get('pass_tds', 0):g} pt pass TD")
    parts.append(f"{scoring.stats.get('pass_yds', 0):g}/pass yd")
    if league.is_superflex:
        parts.append("superflex")
    return ", ".join(parts)


def _one_avg_type(table: pd.DataFrame) -> pd.DataFrame:
    """One row per player when the table holds several averaging methods."""
    if len(table) and "avg_type" in table.columns:
        kinds = table["avg_type"].unique()
        if len(kinds) > 1:
            pick = "weighted" if "weighted" in kinds else kinds[0]
            return table[table["avg_type"] == pick]
    return table


# ---------------------------------------------------------------------------
# Replacement level
# ---------------------------------------------------------------------------

def replacement_ranks(league: SleeperLeague,
                      table: pd.DataFrame | None = None) -> dict[str, int]:
    """How deep each position is drafted before it stops being scarce.

    Dedicated slots are easy -- twelve teams starting one tight end each means
    the thirteenth tight end is replaceable.  Flex slots are not: whether a
    superflex slot holds a quarterback or a running back depends on who is
    actually worth more.  So the flex slots are filled greedily from the
    projections themselves, best player first, and each position's replacement
    rank is however many of them ended up starting.
    """
    if table is not None:
        table = _one_avg_type(table)
    slots = league.starting_slots
    teams = max(league.teams, 1)

    dedicated: dict[str, int] = {}
    flexes: list[tuple[str, ...]] = []
    for slot in slots:
        eligible = STARTING_SLOTS.get(slot, ())
        if len(eligible) == 1:
            dedicated[eligible[0]] = dedicated.get(eligible[0], 0) + teams
        elif eligible:
            flexes.extend([eligible] * teams)

    started = dict(dedicated)

    if table is not None and len(table) and flexes:
        ranked = {
            position: group.sort_values("points", ascending=False)["points"].to_numpy()
            for position, group in table.groupby("pos")
        }
        taken = {position: dedicated.get(position, 0) for position in ranked}

        def next_points(position: str) -> float:
            pool = ranked.get(position)
            index = taken.get(position, 0)
            if pool is None or index >= len(pool):
                return -np.inf
            return float(pool[index])

        for eligible in flexes:
            best = max(eligible, key=next_points)
            if next_points(best) == -np.inf:
                continue
            taken[best] = taken.get(best, 0) + 1
        started = taken
    elif flexes:
        # No projections to allocate with: spread each flex slot evenly.
        for eligible in flexes:
            for position in eligible:
                started[position] = started.get(position, 0) + 1 / len(eligible)

    return {
        position: max(int(round(count)), 1)
        for position, count in started.items()
        if count
    }


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------

def attach_league_context(table: pd.DataFrame, league: SleeperLeague,
                          players: pd.DataFrame | None = None) -> pd.DataFrame:
    """Add Sleeper identity, injury status and who holds each player."""
    if players is None:
        players = sleeper_player_map()
    crosswalk = (
        players[players["id"].notna()]
        [["id", "sleeper_id", "sleeper_team", "injury_status", "sleeper_pos"]]
        .drop_duplicates("id")
    )
    merged = table.merge(crosswalk, on="id", how="left")

    rosters = league.rosters
    if len(rosters):
        owned = rosters[["sleeper_id", "team_name", "manager", "is_starter"]]
        merged = merged.merge(owned.drop_duplicates("sleeper_id"),
                              on="sleeper_id", how="left")
        merged = merged.rename(columns={"team_name": "rostered_by",
                                        "is_starter": "starting"})
    else:
        merged["rostered_by"] = pd.NA
        merged["manager"] = pd.NA
        merged["starting"] = pd.NA

    # The player table spells teams the MyFantasyLeague way (SFO, NEP, KCC);
    # print the abbreviations everyone else uses.
    merged["team"] = merged.get("team", pd.Series(pd.NA, index=merged.index))
    merged["team"] = (
        merged["team"].astype("string").fillna(merged["sleeper_team"].astype("string"))
        .replace(TEAM_CORRECTIONS)
    )
    merged.attrs.update(table.attrs)
    return merged.drop(columns=["sleeper_team", "sleeper_pos"], errors="ignore")


# ---------------------------------------------------------------------------
# The whole pipeline
# ---------------------------------------------------------------------------

def build_league_projections(
    league_id: str | int,
    week: int | None = None,
    season: int | None = None,
    sources: Sequence[str] = DEFAULT_SOURCES,
    positions: Sequence[str] | None = None,
    avg_type: str = "all",
    with_ecr: bool = True,
    with_adp: bool = True,
    **scrape_kwargs,
) -> LeagueProjections:
    """Scrape, score under your league's rules, rank, and annotate.

    ``positions`` defaults to whatever your league can actually start -- there
    is no point ranking defenses in a league with no defense slot.
    """
    print(f"Reading Sleeper league {league_id}")
    league = fetch_league(league_id)
    scoring, unscored = league.scoring_rules()
    print(f"  {league.name}: {_scoring_summary(scoring, league)}")

    if positions is None:
        positions = league.rostered_positions or ("QB", "RB", "WR", "TE", "K", "DST")
    if season is None and league.season:
        season = league.season

    scrape_options = dict(scrape_kwargs)

    scrape = scrape_data(sources=sources, positions=positions, season=season,
                         week=week, **scrape_options)
    if not len(scrape):
        raise RuntimeError(
            "No source returned projections. The sites may not have published "
            f"{season} numbers yet -- try a season that has already started."
        )

    print("\nScoring sources under the league's rules")
    provisional = projections_table(scrape, scoring, avg_type=avg_type)
    baselines = replacement_ranks(league, provisional)
    print("  replacement level: " + ", ".join(
        f"{position} {rank}" for position, rank in sorted(baselines.items())
    ))

    table = projections_table(scrape, scoring, avg_type=avg_type,
                              replacement_ranks=baselines)
    table = add_player_info(table)

    if with_ecr:
        print("\nAdding expert consensus rankings")
        table = add_ecr(table, scoring, week=scrape.week)
    table = add_uncertainty(table)

    if with_adp and scrape.week == 0:
        print("\nAdding average draft position")
        adp = get_adp()
        if len(adp):
            table = table.merge(adp[["id", "adp", "adp_sd"]], on="id", how="left")
            table["adp_diff"] = table["rank"] - table["adp"]

    print("\nMatching players to Sleeper rosters")
    player_map = sleeper_player_map()
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    table = attach_league_context(table, league, players=player_map)

    if "avg_type" in table.columns:
        table = table.sort_values(["avg_type", "points_vor"],
                                  ascending=[True, False])
    else:
        table = table.sort_values("points_vor", ascending=False)
    return LeagueProjections(
        league=league,
        scoring=scoring,
        table=_order_columns(table.reset_index(drop=True)),
        replacement_ranks=baselines,
        unscored_settings=unscored,
        scrape=scrape,
        player_map=player_map,
        player_map_fetched_at=fetched_at,
    )


_COLUMN_ORDER = [
    "rank", "pos_rank", "tier", "player", "pos", "team", "points", "points_vor",
    "sd_pts", "floor", "ceiling", "floor_vor", "ceiling_vor", "dropoff",
    "uncertainty", "pos_ecr", "sd_ecr", "adp", "adp_sd", "adp_diff",
    "rostered_by", "manager", "starting",
    "injury_status", "age", "exp", "sources", "id", "sleeper_id", "avg_type",
]


def _order_columns(table: pd.DataFrame) -> pd.DataFrame:
    leading = [column for column in _COLUMN_ORDER if column in table.columns]
    return table[leading + [c for c in table.columns if c not in leading]]
