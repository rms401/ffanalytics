"""Imputation of stats that some sources leave out."""

import numpy as np
import pandas as pd
import pytest

from ffanalytics.custom_scoring import make_scoring_tables
from ffanalytics.impute_funcs import (
    derive_from_mean,
    derive_from_rate,
    impute_bonus_cols,
    impute_fun_list,
    impute_via_rates_and_mean,
)
from ffanalytics.results import ScrapeResult
from ffanalytics.scoring_rules import scoring


def test_derive_from_mean_fills_gaps_with_the_column_mean():
    out = derive_from_mean(pd.Series([10.0, np.nan, 20.0]))
    assert out.tolist() == pytest.approx([10.0, 15.0, 20.0])


def test_derive_from_mean_leaves_an_all_missing_column_alone():
    assert derive_from_mean(pd.Series([np.nan, np.nan])).isna().all()


def test_derive_from_rate_scales_by_the_reference_column():
    # rate = mean(need/ref) over the observed rows = (20/200 + 30/300)/2 = 0.1
    need = pd.Series([20.0, 30.0, np.nan])
    ref = pd.Series([200.0, 300.0, 400.0])
    assert derive_from_rate(need, ref).tolist() == pytest.approx([20.0, 30.0, 40.0])


def test_derive_from_rate_falls_back_to_the_mean_when_a_reference_is_zero():
    need = pd.Series([20.0, 0.0, np.nan])
    ref = pd.Series([200.0, 0.0, 400.0])
    assert derive_from_rate(need, ref).tolist() == pytest.approx([20.0, 0.0, 10.0])


def test_rec_tgt_rule_wins_over_the_shadowed_duplicate():
    """R's list has two `rec_tgt` entries; `[[` returns the first."""
    assert "rec_tgt" in impute_fun_list
    assert "rec" not in impute_fun_list  # the second entry never takes effect

    frame = pd.DataFrame({"rec_tgt": [80.0, np.nan], "rec": [50.0, 60.0]})
    out = impute_fun_list["rec_tgt"](frame)
    assert out.tolist() == pytest.approx([80.0, 96.0])


def test_imputation_pools_sources_for_the_same_player():
    scrape = ScrapeResult(
        {
            "QB": pd.DataFrame(
                {
                    "id": ["0001", "0001", "0001"],
                    "pos": ["QB"] * 3,
                    "data_src": ["CBS", "ESPN", "NFL"],
                    "pass_yds": [4000.0, 4200.0, 4100.0],
                    "pass_tds": [30.0, 32.0, np.nan],
                    "pass_comp": [350.0, 360.0, 355.0],
                }
            )
        },
        season=2026,
        week=1,
    )
    out = impute_via_rates_and_mean(scrape, make_scoring_tables(scoring))
    filled = out["QB"]["pass_tds"]
    assert filled.notna().all()
    # pass_tds is rate-derived from pass_comp: mean(30/350, 32/360) * 355
    expected = (30 / 350 + 32 / 360) / 2 * 355
    assert filled.iloc[2] == pytest.approx(expected)


def test_bonus_columns_are_synthesised_from_yardage():
    scrape = ScrapeResult(
        {
            "QB": pd.DataFrame(
                {
                    "id": ["0001"],
                    "pos": ["QB"],
                    "data_src": ["CBS"],
                    "pass_yds": [4000.0],
                }
            )
        },
        season=2026,
        week=1,
    )
    out = impute_bonus_cols(scrape, make_scoring_tables(scoring)["scoring_tables"])
    frame = out["QB"]

    assert "pass_300_yds" in frame.columns
    assert frame["pass_300_yds"].iloc[0] > 0
    # nested thresholds roll upward: 300+ includes the 350+ and 400+ games
    assert frame["pass_300_yds"].iloc[0] >= frame["pass_350_yds"].iloc[0]
    assert frame["pass_350_yds"].iloc[0] >= frame["pass_400_yds"].iloc[0]


def test_bonus_columns_are_never_negative():
    scrape = ScrapeResult(
        {"QB": pd.DataFrame({"id": ["0001"], "pos": ["QB"], "data_src": ["CBS"],
                             "pass_yds": [5.0]})},
        season=2026,
        week=1,
    )
    frame = impute_bonus_cols(scrape, make_scoring_tables(scoring)["scoring_tables"])["QB"]
    assert (frame["pass_300_yds"] >= 0).all()


def test_kicker_totals_are_reconciled_from_the_distance_buckets():
    scrape = ScrapeResult(
        {
            "K": pd.DataFrame(
                {
                    "id": ["0001", "0001"],
                    "pos": ["K", "K"],
                    "data_src": ["CBS", "ESPN"],
                    "fg": [np.nan, 30.0],
                    "fg_0019": [1.0, 1.0],
                    "fg_2029": [10.0, 9.0],
                    "fg_3039": [8.0, 9.0],
                    "fg_4049": [7.0, 8.0],
                    "fg_50": [3.0, 3.0],
                    "xp": [40.0, 41.0],
                }
            )
        },
        season=2026,
        week=1,
    )
    out = impute_via_rates_and_mean(scrape, make_scoring_tables(scoring))
    # the missing total is filled from the per-distance makes: 1+10+8+7+3
    assert out["K"]["fg"].iloc[0] == pytest.approx(29.0)
