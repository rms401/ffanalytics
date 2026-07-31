"""Filling in the stats a source did not report."""

import numpy as np
import pandas as pd
import pytest

from ffanalytics.impute import (
    _reconcile_kicking,
    from_mean,
    from_rate,
    impute_bonus_columns,
    impute_missing_stats,
)
from ffanalytics.scoring import ScoringRules


def test_from_mean_fills_gaps_with_what_the_others_said():
    filled = from_mean(pd.Series([10.0, 20.0, np.nan]))
    assert filled.tolist() == [10.0, 20.0, 15.0]


def test_from_mean_leaves_an_all_missing_column_alone():
    assert from_mean(pd.Series([np.nan, np.nan])).isna().all()


def test_from_rate_uses_the_ratio_the_other_sources_imply():
    # Two sources say 10 scores per 1000 yards; the third only gave yards.
    filled = from_rate(pd.Series([10.0, 10.0, np.nan]),
                       pd.Series([1000.0, 1000.0, 500.0]))
    assert filled.iloc[2] == pytest.approx(5.0)


def test_from_rate_falls_back_to_the_mean_when_the_denominator_is_zero():
    filled = from_rate(pd.Series([4.0, np.nan]), pd.Series([0.0, 100.0]))
    assert filled.iloc[1] == pytest.approx(4.0)


def test_missing_stats_are_filled_per_player_not_across_the_league():
    frame = pd.DataFrame({
        "id": ["A", "A", "B", "B"],
        "pos": "WR",
        "data_src": ["CBS", "ESPN", "CBS", "ESPN"],
        "rec_yds": [1000.0, 1000.0, 200.0, 200.0],
        "rec_tds": [10.0, np.nan, 1.0, np.nan],
    })
    rules = ScoringRules(stats={"rec_yds": 0.1, "rec_tds": 6}, name="test")
    out = impute_missing_stats({"WR": frame}, rules)["WR"]

    assert out.loc[1, "rec_tds"] == pytest.approx(10.0)
    assert out.loc[3, "rec_tds"] == pytest.approx(1.0)


def test_a_combined_short_field_goal_bucket_is_split_across_the_ranges():
    frame = pd.DataFrame({"fg_0039": [20.0], "fg_4049": [6.0], "fg_50": [4.0]})
    out = _reconcile_kicking(frame)

    assert "fg_0039" not in out.columns
    assert out[["fg_0019", "fg_2029", "fg_3039"]].sum(axis=1).iloc[0] == pytest.approx(20.0)
    assert out["fg"].iloc[0] == pytest.approx(30.0)


def test_field_goal_total_is_derived_from_the_buckets_when_absent():
    frame = pd.DataFrame({"fg_0019": [1.0], "fg_2029": [8.0], "fg_3039": [10.0],
                          "fg_4049": [7.0], "fg_50": [4.0]})
    assert _reconcile_kicking(frame)["fg"].iloc[0] == pytest.approx(30.0)


def test_extra_point_misses_come_from_attempts_minus_makes():
    frame = pd.DataFrame({"xp": [40.0], "xp_att": [43.0]})
    assert _reconcile_kicking(frame)["xp_miss"].iloc[0] == pytest.approx(3.0)


def test_bonus_columns_are_only_added_when_the_league_scores_them():
    frame = pd.DataFrame({
        "id": ["A"], "pos": ["WR"], "data_src": ["CBS"], "rec_yds": [1400.0],
    })
    scored = ScoringRules(stats={"rec_yds": 0.1, "rec_100_yds": 2}, name="scored")
    plain = ScoringRules(stats={"rec_yds": 0.1}, name="plain")

    assert "rec_100_yds" in impute_bonus_columns({"WR": frame}, scored)["WR"].columns
    assert "rec_100_yds" not in impute_bonus_columns({"WR": frame}, plain)["WR"].columns


def test_bonus_estimates_rise_with_yardage_and_never_go_negative():
    frame = pd.DataFrame({
        "id": ["A", "B"], "pos": "WR", "data_src": "CBS",
        "rec_yds": [1600.0, 50.0],
    })
    rules = ScoringRules(stats={"rec_yds": 0.1, "rec_100_yds": 2}, name="test")
    out = impute_bonus_columns({"WR": frame}, rules)["WR"]

    assert out["rec_100_yds"].iloc[0] > out["rec_100_yds"].iloc[1]
    assert (out["rec_100_yds"] >= 0).all()


def test_nested_thresholds_roll_up_into_the_wider_one():
    """A 400-yard passing game is also a 300-yard passing game."""
    frame = pd.DataFrame({
        "id": ["A"], "pos": ["QB"], "data_src": ["CBS"], "pass_yds": [5000.0],
    })
    rules = ScoringRules(
        stats={"pass_yds": 0.04, "pass_300_yds": 1, "pass_350_yds": 1,
               "pass_400_yds": 1},
        name="test",
    )
    out = impute_bonus_columns({"QB": frame}, rules)["QB"]
    assert out["pass_300_yds"].iloc[0] >= out["pass_350_yds"].iloc[0]
    assert out["pass_350_yds"].iloc[0] >= out["pass_400_yds"].iloc[0]
