"""R-semantics primitives, pinned against values R produces.

The RNG vectors are R's canonical published output for ``set.seed()``; if they
drift, the seeded DST Monte Carlo in ``score_dst_pts_allowed`` cannot be
trusted, so these are the gate for the whole port.
"""

import numpy as np
import pandas as pd
import pytest

from ffanalytics.rcompat import stats as st
from ffanalytics.rcompat.rng import RRandom, qnorm


# --------------------------------------------------------------------------
# RNG
# --------------------------------------------------------------------------

def test_runif_matches_r_for_seed_1():
    # R: set.seed(1); runif(5)
    assert RRandom(1).runif(5) == pytest.approx(
        [0.2655087, 0.3721239, 0.5728534, 0.9082078, 0.2016819], abs=1e-7
    )


def test_rnorm_matches_r_for_seed_1():
    # R: set.seed(1); rnorm(5)
    assert RRandom(1).rnorm(5) == pytest.approx(
        [-0.6264538, 0.1836433, -0.8356286, 1.5952808, 0.3295078], abs=1e-7
    )


def test_runif_matches_r_for_seed_42():
    # R: set.seed(42); runif(3)
    assert RRandom(42).runif(3) == pytest.approx(
        [0.9148060, 0.9370754, 0.2861395], abs=1e-7
    )


def test_rnorm_matches_r_for_seed_123():
    # R: set.seed(123); rnorm(3)
    assert RRandom(123).rnorm(3) == pytest.approx(
        [-0.5604756, -0.2301775, 1.5587083], abs=1e-7
    )


def test_rnorm_with_mean_and_sd_matches_r():
    # R: set.seed(1); rnorm(3, 20, 5)
    assert RRandom(1).rnorm(3, 20, 5) == pytest.approx(
        [16.867731, 20.918217, 15.821857], abs=1e-6
    )


def test_qnorm_matches_r():
    # R: qnorm(c(0.025, 0.5, 0.975))
    assert [qnorm(p) for p in (0.025, 0.5, 0.975)] == pytest.approx(
        [-1.959964, 0.0, 1.959964], abs=1e-6
    )
    # far tail, exercising the r > 5 branch
    assert qnorm(1e-20) == pytest.approx(-9.262340, abs=1e-6)


# --------------------------------------------------------------------------
# Ranks
# --------------------------------------------------------------------------

def test_dense_rank_leaves_no_gaps():
    assert list(st.dense_rank([10, 20, 20, 30])) == [1, 2, 2, 3]


def test_min_rank_leaves_gaps():
    assert list(st.min_rank([10, 20, 20, 30])) == [1, 2, 2, 4]


def test_percent_rank_uses_min_rank():
    assert list(st.percent_rank([10, 20, 20, 30])) == pytest.approx(
        [0.0, 1 / 3, 1 / 3, 1.0]
    )


def test_ranks_keep_missing_values_missing():
    assert pd.isna(st.dense_rank([1.0, np.nan, 3.0])[1])


# --------------------------------------------------------------------------
# Location and spread
# --------------------------------------------------------------------------

def test_quantile_uses_type_7():
    # R: quantile(1:10, c(.05, .95))
    assert st.quantile_type7(range(1, 11), [0.05, 0.95]) == pytest.approx([1.45, 9.55])


def test_sd_uses_n_minus_1():
    assert st.r_sd([1, 2, 3, 4, 5]) == pytest.approx(1.5811388)
    assert np.isnan(st.r_sd([1]))


def test_mad_applies_the_1_4826_constant():
    # R: mad(c(1, 2, 3, 4, 100))
    assert st.r_mad([1, 2, 3, 4, 100]) == pytest.approx(1.4826)


def test_mad2_returns_na_for_short_vectors():
    assert np.isnan(st.mad2([]))
    assert np.isnan(st.mad2([5.0]))


def test_mad2_inherits_rs_na_centre_behaviour():
    """R evaluates ``center = median(x)`` before ``mad()`` strips NAs."""
    assert np.isnan(st.mad2([1.0, 2.0, np.nan, 4.0], na_rm=True))


def test_mad2_accepts_and_ignores_weights():
    assert st.mad2([1, 2, 3, 4, 100], w=[1, 1, 1, 1, 1]) == pytest.approx(1.4826)


def test_weighted_mean_matches_r():
    # R: weighted.mean(c(1, 2, 3), c(1, 1, 2))
    assert st.weighted_mean([1, 2, 3], [1, 1, 2]) == pytest.approx(2.25)


def test_weighted_sd_matches_the_r_formula():
    # sqrt((sum(w) / (sum(w)^2 - sum(w^2))) * sum(w * (x - mean_w)^2))
    # = sqrt((6 / (36 - 10)) * 6.833333) for x = 1:4, w = c(1, 1, 2, 2)
    assert st.weighted_sd([1, 2, 3, 4], [1, 1, 2, 2]) == pytest.approx(1.2557560)


def test_weighted_sd_drops_non_positive_weights():
    assert st.weighted_sd([1, 2, 3, 99], [1, 1, 2, 0]) == pytest.approx(
        st.weighted_sd([1, 2, 3], [1, 1, 2])
    )


def test_weighted_sd_is_na_below_two_observations():
    assert np.isnan(st.weighted_sd([5], [1]))


def test_wilcox_loc_is_the_median_of_values_and_pairwise_averages():
    assert st.wilcox_loc([1, 2, 3, 4, 100]) == pytest.approx(3.0)


def test_wilcox_loc_falls_back_to_the_mean_for_short_vectors():
    assert st.wilcox_loc([2.0, 4.0]) == pytest.approx(3.0)


def test_whdquantile_is_symmetric_on_symmetric_data():
    low, high = st.whdquantile([1, 2, 3, 4, 5], [1] * 5, [0.05, 0.95])
    assert low + high == pytest.approx(6.0)
    assert 1.0 < low < 2.0 and 4.0 < high < 5.0


def test_whdquantile_is_na_below_two_observations():
    assert np.all(np.isnan(st.whdquantile([5], [1], [0.05, 0.95])))


def test_row_sd_matches_r():
    frame = pd.DataFrame({"a": [1.0, 10.0], "b": [2.0, 20.0], "c": [3.0, 30.0]})
    assert list(st.row_sd(frame)) == pytest.approx([1.0, 10.0])


def test_row_sd_is_na_when_fewer_than_three_values_remain():
    frame = pd.DataFrame({"a": [1.0], "b": [2.0], "c": [np.nan]})
    assert np.isnan(st.row_sd(frame, na_rm=True)[0])


# --------------------------------------------------------------------------
# Named vectors
# --------------------------------------------------------------------------

def test_named_vec_returns_the_first_match_like_r():
    vec = st.NamedVec([("109", "dst_tackles"), ("99", "dst_sacks"), ("109", "idp_solo")])
    assert vec["109"] == "dst_tackles"


def test_named_vec_filter_by_value_mirrors_grepl_subsetting():
    vec = st.NamedVec([("109", "dst_tackles"), ("109", "idp_solo")])
    offense_only = vec.filter_values(lambda v: not v.startswith("idp_"))
    assert offense_only.items() == [("109", "dst_tackles")]

    idp_only = vec.filter_values(lambda v: not v.startswith("dst_"))
    assert idp_only["109"] == "idp_solo"


def test_rename_vec_replaces_matches_and_keeps_the_rest():
    mapping = st.NamedVec([("Pass Yds", "pass_yds"), ("Pass TD", "pass_tds")])
    assert st.rename_vec(["Pass Yds", "Rush Yds", "Pass TD"], mapping) == [
        "pass_yds",
        "Rush Yds",
        "pass_tds",
    ]


# --------------------------------------------------------------------------
# Coercion
# --------------------------------------------------------------------------

def test_type_convert_prefers_integers_then_floats_then_text():
    assert st.type_convert(pd.Series(["1", "2", "3"])).tolist() == [1, 2, 3]
    assert st.type_convert(pd.Series(["1.5", "2"])).tolist() == pytest.approx([1.5, 2.0])
    assert st.type_convert(pd.Series(["a", "b"])).tolist() == ["a", "b"]


def test_type_convert_treats_r_style_blanks_as_missing():
    converted = st.type_convert(pd.Series(["1", "—", "3"]))
    assert converted.tolist()[0] == 1
    assert pd.isna(converted.tolist()[1])


def test_type_convert_frame_leaves_excluded_columns_alone():
    frame = pd.DataFrame({"id": ["0001", "0002"], "pass_yds": ["100", "200"]})
    out = st.type_convert_frame(frame, exclude=("id",))
    assert out["id"].tolist() == ["0001", "0002"]
    assert out["pass_yds"].tolist() == [100, 200]
