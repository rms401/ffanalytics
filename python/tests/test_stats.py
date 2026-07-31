"""The numeric helpers behind the multi-source averages."""

import numpy as np
import pandas as pd
import pytest

from ffanalytics import stats as st


def test_mean_and_sd_ignore_missing_values():
    assert st.mean([1, 2, np.nan, 3]) == pytest.approx(2.0)
    assert st.sd([2, 4, 4, 4, 5, 5, 7, 9]) == pytest.approx(2.13809, rel=1e-4)


def test_sd_is_undefined_for_a_single_source():
    assert np.isnan(st.sd([5]))
    assert np.isnan(st.sd([np.nan, np.nan]))


def test_quantiles_bracket_the_sample():
    low, high = st.quantiles([1, 2, 3, 4, 5], probs=(0.05, 0.95))
    assert low == pytest.approx(1.2)
    assert high == pytest.approx(4.8)


def test_mad_is_unmoved_by_one_wild_source():
    assert st.mad([10, 11, 12, 13, 400]) == pytest.approx(st.mad([10, 11, 12, 13, 14]))


def test_wilcox_location_sits_between_mean_and_median():
    values = [10, 11, 12, 13, 40]
    location = st.wilcox_location(values)
    assert np.median(values) <= location <= np.mean(values)


def test_weighted_mean_ignores_zero_weighted_sources():
    assert st.weighted_mean([10, 100], [1, 0]) == pytest.approx(10.0)
    assert st.weighted_mean([10, 20], [1, 3]) == pytest.approx(17.5)


def test_weighted_sd_needs_two_usable_sources():
    assert np.isnan(st.weighted_sd([10, 100], [1, 0]))
    assert st.weighted_sd([10, 20, 30], [1, 1, 1]) == pytest.approx(10.0)


def test_weighted_quantiles_stay_inside_the_sample():
    low, high = st.weighted_quantiles([10, 20, 30, 40], [1, 1, 1, 1])
    assert 10 <= low < high <= 40


def test_dense_rank_does_not_skip_numbers():
    assert list(st.dense_rank([10, 20, 20, 30])) == [1, 2, 2, 3]


def test_percentile_spans_zero_to_one():
    assert list(st.percentile([1, 2, 3])) == [0.0, 0.5, 1.0]


def test_standardize_centres_on_zero():
    values = st.standardize([1, 2, 3, 4, 5])
    assert values.mean() == pytest.approx(0.0)
    assert np.std(values, ddof=1) == pytest.approx(1.0)


def test_standardize_is_undefined_when_every_value_is_the_same():
    assert np.isnan(st.standardize([7, 7, 7])).all()


def test_to_numeric_frame_converts_only_fully_numeric_columns():
    frame = pd.DataFrame({
        "id": ["0501", "0502"],
        "yards": ["1,200", "980"],
        "player": ["Josh Allen", "Lamar Jackson"],
        "blank": ["-", "—"],
    })
    out = st.to_numeric_frame(frame, exclude=("id",))

    assert out["id"].tolist() == ["0501", "0502"]  # leading zeros survive
    assert out["yards"].tolist() == [1200, 980]
    assert out["player"].tolist() == ["Josh Allen", "Lamar Jackson"]
    assert out["blank"].isna().all()
