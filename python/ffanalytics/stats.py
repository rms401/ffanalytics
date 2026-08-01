"""Small numeric helpers used when aggregating several sources into one number.

Nothing here is exotic; the weighted estimators are the only pieces without a
one-line NumPy equivalent.  Every function ignores missing values.
"""

from __future__ import annotations

import math
from itertools import combinations

import numpy as np
import pandas as pd
from scipy.special import betainc

__all__ = [
    "mean",
    "sd",
    "quantiles",
    "mad",
    "wilcox_location",
    "weighted_mean",
    "weighted_sd",
    "weighted_quantiles",
    "dense_rank",
    "percentile",
    "standardize",
    "to_numeric_frame",
]


def _clean(values) -> np.ndarray:
    array = np.asarray(pd.Series(values), dtype=float)
    return array[~np.isnan(array)]


def mean(values) -> float:
    clean = _clean(values)
    return float(clean.mean()) if clean.size else math.nan


def sd(values) -> float:
    """Sample standard deviation; undefined for fewer than two values."""
    clean = _clean(values)
    return float(np.std(clean, ddof=1)) if clean.size > 1 else math.nan


def quantiles(values, probs=(0.05, 0.95)) -> np.ndarray:
    clean = _clean(values)
    probs = np.atleast_1d(np.asarray(probs, dtype=float))
    if clean.size == 0:
        return np.full(probs.shape, np.nan)
    return np.quantile(clean, probs)


def mad(values, constant: float = 1.4826) -> float:
    """Median absolute deviation, scaled to estimate a normal sd."""
    clean = _clean(values)
    if clean.size < 2:
        return math.nan
    return constant * float(np.median(np.abs(clean - np.median(clean))))


def wilcox_location(values) -> float:
    """Median of the values together with every pairwise average.

    A robust centre that, unlike the plain median, still moves when a single
    source disagrees -- which is the point when there are only a handful of
    sources per player.
    """
    clean = _clean(values)
    if clean.size <= 2:
        return float(clean.mean()) if clean.size else math.nan
    pairs = [(a + b) / 2 for a, b in combinations(clean, 2)]
    return float(np.median(np.concatenate([clean, np.asarray(pairs)])))


def _paired(values, weights) -> tuple[np.ndarray, np.ndarray]:
    """Drop entries with a missing value or a non-positive weight."""
    x = np.asarray(pd.Series(values), dtype=float)
    w = (
        np.ones(x.shape)
        if weights is None
        else np.asarray(pd.Series(weights), dtype=float)
    )
    if w.size == 1 and x.size != 1:
        w = np.repeat(w, x.size)
    if w.size != x.size:
        return np.empty(0), np.empty(0)
    with np.errstate(invalid="ignore"):
        keep = ~np.isnan(x) & ~np.isnan(w) & (w > 0)
    return x[keep], w[keep]


def weighted_mean(values, weights=None) -> float:
    x, w = _paired(values, weights)
    if x.size == 0:
        return math.nan
    return float(np.sum(x * w) / np.sum(w))


def weighted_sd(values, weights=None) -> float:
    """Reliability-weighted sample standard deviation."""
    x, w = _paired(values, weights)
    if x.size <= 1:
        return math.nan
    sum_w, sum_w2 = np.sum(w), np.sum(w**2)
    denominator = sum_w**2 - sum_w2
    if denominator == 0:
        return math.nan
    centre = np.sum(x * w) / sum_w
    return float(math.sqrt((sum_w / denominator) * np.sum(w * (x - centre) ** 2)))


def weighted_quantiles(values, weights=None, probs=(0.05, 0.95)) -> np.ndarray:
    """Weighted Harrell-Davis quantiles (Akinshin 2023, arXiv:2304.07265).

    With only a handful of sources per player an order statistic is a very
    noisy 5th percentile, so this smooths over the whole sample using Kish's
    effective sample size.
    """
    x, w = _paired(values, weights)
    probs = np.atleast_1d(np.asarray(probs, dtype=float))
    if x.size <= 1:
        return np.full(probs.shape, np.nan)

    effective_n = np.sum(w) ** 2 / np.sum(w**2)

    order = np.argsort(x, kind="stable")
    x, w = x[order], w[order] / np.sum(w)
    cumulative = np.concatenate(([0.0], np.cumsum(w)))

    out = np.empty(probs.shape)
    for i, p in enumerate(probs):
        a, b = (effective_n + 1) * p, (effective_n + 1) * (1 - p)
        cdf = betainc(a, b, np.clip(cumulative, 0.0, 1.0))
        out[i] = np.sum(np.diff(cdf) * x)
    return out


def dense_rank(values) -> pd.Series:
    """Ranks where ties share a number and no numbers are skipped."""
    return pd.Series(values).rank(method="dense", na_option="keep")


def percentile(values) -> pd.Series:
    """Fraction of the sample each value is greater than, in ``[0, 1]``."""
    series = pd.Series(values)
    n = series.notna().sum()
    if n <= 1:
        return pd.Series(np.nan, index=series.index)
    return (series.rank(method="min", na_option="keep") - 1) / (n - 1)


def standardize(values) -> np.ndarray:
    """Centre on the mean and divide by the sd (z-scores)."""
    array = np.asarray(pd.Series(values), dtype=float)
    spread = sd(array)
    if not np.isfinite(spread) or spread == 0:
        return np.full(array.shape, np.nan)
    return (array - np.nanmean(array)) / spread


_NA_STRINGS = {"", "-", "--", "—", "–", "NA", "N/A", "None", "null"}


def to_numeric_frame(frame: pd.DataFrame, exclude=()) -> pd.DataFrame:
    """Turn scraped text columns into numbers where every value looks numeric.

    Scrapers hand back strings; this is the one place that decides what is a
    number.  Columns in ``exclude`` stay text -- ids are zero-padded and must
    keep their leading zeros.
    """
    out = frame.copy()
    for column in out.columns:
        if column in exclude:
            continue
        series = out[column]
        if not (series.dtype == object or pd.api.types.is_string_dtype(series)):
            continue

        cleaned = series.astype(object).map(_blank_to_na)
        present = cleaned.dropna()
        if present.empty:
            out[column] = cleaned
            continue

        numbers = pd.to_numeric(
            present.astype(str).str.replace(",", "", regex=False), errors="coerce"
        )
        if numbers.isna().any():
            out[column] = cleaned
        else:
            out[column] = pd.to_numeric(cleaned.astype(str).str.replace(",", "", regex=False),
                                        errors="coerce")
    return out


def _blank_to_na(value):
    if value is None:
        return np.nan
    if isinstance(value, str):
        stripped = value.strip()
        return np.nan if stripped in _NA_STRINGS else stripped
    return value
