"""Working out replacement level from a league's roster settings."""

import pandas as pd
import pytest

from ffanalytics.league import replacement_ranks
from ffanalytics.sleeper import SleeperLeague

TWELVE_TEAM = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"] + ["BN"] * 6
SUPERFLEX = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "SUPER_FLEX", "K"] + ["BN"] * 6


def league(slots, teams=12) -> SleeperLeague:
    return SleeperLeague(league_id="1", name="test", season=2026, teams=teams,
                         roster_slots=slots, scoring_settings={})


def projections(**counts) -> pd.DataFrame:
    """A pool of players per position, each worth a bit less than the last."""
    rows = []
    for position, (count, top) in counts.items():
        for index in range(count):
            rows.append({"id": f"{position}{index}", "pos": position,
                         "points": top - index * 2.0})
    return pd.DataFrame(rows)


def test_dedicated_slots_scale_with_the_number_of_teams():
    ranks = replacement_ranks(league(TWELVE_TEAM), projections(
        QB=(40, 400), RB=(80, 300), WR=(80, 300), TE=(40, 250), K=(32, 150),
        DST=(32, 130),
    ))
    assert ranks["QB"] == 12   # one apiece
    assert ranks["K"] == 12
    assert ranks["DST"] == 12
    assert ranks["TE"] >= 12   # twelve dedicated, plus any flex


def test_the_flex_goes_to_whichever_position_is_worth_more():
    """Running backs priced above receivers should take the flex slots."""
    ranks = replacement_ranks(league(TWELVE_TEAM), projections(
        QB=(40, 400), RB=(80, 400), WR=(80, 200), TE=(40, 150), K=(32, 150),
        DST=(32, 130),
    ))
    assert ranks["RB"] > 24    # 24 dedicated plus flex
    assert ranks["WR"] == 24   # 24 dedicated, no flex
    assert ranks["RB"] + ranks["WR"] + ranks["TE"] == 24 + 24 + 12 + 12


def test_a_superflex_league_starts_far_more_quarterbacks():
    pool = projections(QB=(60, 400), RB=(80, 300), WR=(80, 300), TE=(40, 250),
                       K=(32, 150))
    standard = replacement_ranks(league(TWELVE_TEAM), pool)
    superflex = replacement_ranks(league(SUPERFLEX), pool)
    assert superflex["QB"] > standard["QB"]


def test_positions_the_league_cannot_start_are_left_out():
    ranks = replacement_ranks(league(SUPERFLEX), projections(
        QB=(60, 400), RB=(80, 300), WR=(80, 300), TE=(40, 250), K=(32, 150),
    ))
    assert "DST" not in ranks


def test_every_starting_slot_is_accounted_for():
    ranks = replacement_ranks(league(SUPERFLEX), projections(
        QB=(60, 400), RB=(80, 300), WR=(80, 300), TE=(40, 250), K=(32, 150),
    ))
    starters_per_team = len([s for s in SUPERFLEX if s != "BN"])
    assert sum(ranks.values()) == starters_per_team * 12


def test_replacement_level_still_works_without_projections():
    ranks = replacement_ranks(league(TWELVE_TEAM))
    assert ranks["QB"] == 12
    assert ranks["RB"] > 24  # the flex is shared out rather than allocated


def test_a_shallow_pool_does_not_start_players_that_do_not_exist():
    """Only five kickers projected means the sixth is not a starter."""
    ranks = replacement_ranks(league(["QB", "FLEX", "K"] + ["BN"] * 2, teams=12),
                              projections(QB=(20, 400), RB=(5, 300), WR=(5, 280),
                                          TE=(5, 200), K=(5, 150)))
    assert ranks["QB"] == 12
    assert ranks["RB"] + ranks["WR"] + ranks["TE"] <= 15 + 12
