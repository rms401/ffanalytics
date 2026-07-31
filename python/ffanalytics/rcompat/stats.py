"""R statistical primitives, reproduced exactly.

Every function here exists because the obvious NumPy/pandas equivalent differs
from R in a way that changes results: rank flavours, quantile type, the MAD
constant and its lazily-evaluated centre, weighted estimators, and the
first-match lookup R does on vectors with duplicate names.
"""

from __future__ import annotations

import math
from itertools import combinations
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd
from scipy.special import betainc

__all__ = [
    "NamedVec",
    "rename_vec",
    "dense_rank",
    "min_rank",
    "percent_rank",
    "quantile_type7",
    "r_sd",
    "r_mad",
    "mad2",
    "r_scale",
    "weighted_mean",
    "weighted_sd",
    "whdquantile",
    "wilcox_loc",
    "row_sd",
    "type_convert",
    "type_convert_frame",
]


# --------------------------------------------------------------------------
# Named vectors
# --------------------------------------------------------------------------

class NamedVec:
    """An ordered R-style named vector.

    R's ``c()`` permits repeated names and ``x["k"]`` / ``match()`` return the
    **first** match; a Python ``dict`` keeps the **last**.  Six of this
    package's lookup tables rely on that -- ``espn_columns`` maps stat id
    ``109`` to both ``dst_tackles`` and ``idp_solo``, and
    ``scoring_type_for_cols["all_pos"]`` must resolve to ``"rush"`` rather than
    ``"idp"`` -- so they are stored here instead of in dicts.
    """

    __slots__ = ("_pairs",)

    def __init__(self, pairs: Iterable[tuple[str, Any]] | dict) -> None:
        if isinstance(pairs, dict):
            pairs = pairs.items()
        self._pairs: list[tuple[str, Any]] = [(str(k), v) for k, v in pairs]

    @property
    def names(self) -> list[str]:
        return [k for k, _ in self._pairs]

    @property
    def values(self) -> list[Any]:
        return [v for _, v in self._pairs]

    def items(self) -> list[tuple[str, Any]]:
        return list(self._pairs)

    def get(self, name: str, default: Any = None) -> Any:
        """First value whose name is ``name`` -- R's lookup semantics."""
        for key, value in self._pairs:
            if key == name:
                return value
        return default

    def __getitem__(self, name: str) -> Any:
        value = self.get(name, _MISSING)
        if value is _MISSING:
            raise KeyError(name)
        return value

    def __contains__(self, name: object) -> bool:
        return any(key == name for key, _ in self._pairs)

    def __len__(self) -> int:
        return len(self._pairs)

    def __iter__(self):
        return iter(self._pairs)

    def filter_values(self, predicate: Callable[[Any], bool]) -> "NamedVec":
        """Subset by value, as ``x[!grepl("^dst_", x)]`` does in R."""
        return NamedVec([(k, v) for k, v in self._pairs if predicate(v)])

    def filter_names(self, predicate: Callable[[str], bool]) -> "NamedVec":
        return NamedVec([(k, v) for k, v in self._pairs if predicate(k)])

    def to_dict(self) -> dict[str, Any]:
        """First-match dict (later duplicates dropped, matching R lookups)."""
        out: dict[str, Any] = {}
        for key, value in self._pairs:
            out.setdefault(key, value)
        return out

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"NamedVec({self._pairs!r})"


_MISSING = object()


def rename_vec(x: Sequence[str], new_names: NamedVec | dict) -> list[str]:
    """R's ``rename_vec`` (``R/helper_funcs.R:129-145``).

    Replaces each element of ``x`` that matches one of ``new_names``' *names*
    with the corresponding value, leaving everything else untouched.  Matching
    is first-occurrence, like R's ``match()``.
    """
    lookup = new_names.to_dict() if isinstance(new_names, NamedVec) else dict(new_names)
    return [lookup.get(value, value) for value in x]


# --------------------------------------------------------------------------
# Ranks
# --------------------------------------------------------------------------

def dense_rank(x) -> pd.Series:
    """``dplyr::dense_rank`` -- ties share a rank and no ranks are skipped."""
    series = pd.Series(x)
    return series.rank(method="dense", na_option="keep")


def min_rank(x) -> pd.Series:
    """``dplyr::min_rank`` -- ties take the lowest rank, leaving gaps."""
    series = pd.Series(x)
    return series.rank(method="min", na_option="keep")


def percent_rank(x) -> pd.Series:
    """``dplyr::percent_rank`` = ``(min_rank(x) - 1) / (n_non_na - 1)``."""
    series = pd.Series(x)
    n = series.notna().sum()
    if n <= 1:
        return pd.Series(np.nan, index=series.index)
    return (min_rank(series) - 1) / (n - 1)


# --------------------------------------------------------------------------
# Location / spread
# --------------------------------------------------------------------------

def _clean(x, na_rm: bool) -> np.ndarray:
    values = np.asarray(pd.Series(x), dtype=float)
    if na_rm:
        values = values[~np.isnan(values)]
    return values


def quantile_type7(x, probs, na_rm: bool = True) -> np.ndarray:
    """``stats::quantile`` with its default ``type = 7``."""
    values = _clean(x, na_rm)
    probs = np.atleast_1d(np.asarray(probs, dtype=float))
    n = values.size
    if n == 0:
        return np.full(probs.shape, np.nan)
    if n == 1:
        return np.full(probs.shape, values[0])

    values = np.sort(values)
    index = (n - 1) * probs
    lo = np.floor(index).astype(int)
    hi = np.ceil(index).astype(int)
    frac = index - lo
    return values[lo] * (1 - frac) + values[hi] * frac


def r_sd(x, na_rm: bool = True) -> float:
    """``stats::sd`` -- the n-1 denominator, NaN for fewer than 2 values."""
    values = _clean(x, na_rm)
    if values.size < 2:
        return float("nan")
    return float(np.std(values, ddof=1))


def r_mad(x, center: float | None = None, constant: float = 1.4826,
          na_rm: bool = False) -> float:
    """``stats::mad`` -- median absolute deviation."""
    values = np.asarray(pd.Series(x), dtype=float)
    if center is None:
        center = float(np.median(values)) if values.size else float("nan")
    if na_rm:
        values = values[~np.isnan(values)]
    if values.size == 0:
        return float("nan")
    return constant * float(np.median(np.abs(values - center)))


def mad2(x, center: float | None = None, constant: float = 1.4826,
         na_rm: bool = False, w=None) -> float:
    """``mad2`` from ``R/calc_projections.R:26-33`` -- MAD, NA for n <= 1.

    Two R behaviours are reproduced deliberately:

    * ``w`` is accepted and ignored, so it can stand in for ``weighted.sd`` in
      the estimator triples.
    * The default ``center = median(x)`` is a promise evaluated in ``mad2``'s
      frame, i.e. against the vector *before* ``mad()`` strips NAs.  So when
      ``x`` contains any NA the centre is NA and the result is NA, even with
      ``na.rm = TRUE``.
    """
    values = np.asarray(pd.Series(x), dtype=float)
    if values.size in (0, 1):
        return float("nan")
    if center is None:
        center = float(np.median(values))  # NaN if x holds any NaN, as in R
    return r_mad(values, center=center, constant=constant, na_rm=na_rm)


def r_scale(x) -> np.ndarray:
    """``base::scale`` -- centre by the mean, divide by the sd (both na.rm)."""
    values = np.asarray(pd.Series(x), dtype=float)
    centered = values - np.nanmean(values)
    n = np.count_nonzero(~np.isnan(values))
    if n < 2:
        return np.full(values.shape, np.nan)
    scale = math.sqrt(np.nansum(centered ** 2) / (n - 1))
    if scale == 0:
        return np.full(values.shape, np.nan)
    return centered / scale


def weighted_mean(x, w=None, na_rm: bool = False) -> float:
    """``stats::weighted.mean`` -- ``sum((x*w)[w != 0]) / sum(w)``."""
    values = np.asarray(pd.Series(x), dtype=float)
    if w is None:
        return float(np.nanmean(values)) if na_rm else float(np.mean(values))
    weights = np.asarray(pd.Series(w), dtype=float)
    if na_rm:
        keep = ~np.isnan(values)
        values, weights = values[keep], weights[keep]
    if values.size == 0:
        return float("nan")
    nonzero = weights != 0
    return float(np.sum((values * weights)[nonzero]) / np.sum(weights))


def weighted_sd(x, w, na_rm: bool = False) -> float:
    """``weighted.sd`` from ``R/calc_projections.R:7-21``."""
    values = np.asarray(pd.Series(x), dtype=float)
    weights = np.asarray(pd.Series(w), dtype=float)

    with np.errstate(invalid="ignore"):
        keep = ~((weights <= 0) | np.isnan(weights)) & ~np.isnan(values)
    values, weights = values[keep], weights[keep]

    if values.size <= 1:
        return float("nan")

    sum_w = np.nansum(weights) if na_rm else np.sum(weights)
    sum_w2 = np.nansum(weights ** 2) if na_rm else np.sum(weights ** 2)
    mean_w = (np.nansum(values * weights) if na_rm else np.sum(values * weights)) / sum_w
    denom = sum_w ** 2 - sum_w2
    if denom == 0:
        return float("nan")
    return float(math.sqrt((sum_w / denom) * np.sum(weights * (values - mean_w) ** 2)))


def whdquantile(x, w=None, probs=(0.05, 0.95), na_rm: bool = False) -> np.ndarray:
    """Weighted Harrell-Davis quantile estimator.

    ``R/calc_projections.R:38-80``, after Akinshin (2023) "Weighted quantile
    estimators" (arXiv:2304.07265).  Uses Kish's effective sample size and the
    Beta CDF; ``na_rm`` is accepted and ignored, as in R.
    """
    values = np.asarray(pd.Series(x), dtype=float)
    weights = (
        np.full(values.shape, np.nan) if w is None
        else np.asarray(pd.Series(w), dtype=float)
    )
    if weights.size == 1 and values.size != 1:
        weights = np.repeat(weights, values.size)

    with np.errstate(invalid="ignore"):
        keep = ~((weights <= 0) | np.isnan(weights)) & ~np.isnan(values)
    values, weights = values[keep], weights[keep]

    probs = np.atleast_1d(np.asarray(probs, dtype=float))
    if values.size <= 1:
        return np.full(probs.shape, np.nan)
    if weights.size == 0:
        weights = np.ones(values.size)
    if values.size != weights.size:
        return np.full(probs.shape, np.nan)

    nw = np.sum(weights) ** 2 / np.sum(weights ** 2)  # Kish's effective sample size

    order = np.argsort(values, kind="stable")
    values, weights = values[order], weights[order]
    weights = weights / np.sum(weights)
    cdf_probs = np.concatenate(([0.0], np.cumsum(weights)))

    out = np.empty(probs.shape)
    for i, p in enumerate(probs):
        a, b = (nw + 1) * p, (nw + 1) * (1 - p)
        cdf = betainc(a, b, np.clip(cdf_probs, 0.0, 1.0))
        out[i] = np.sum(np.diff(cdf) * values)
    return out


def wilcox_loc(x, na_rm: bool = False, w=None) -> float:
    """Wilcox's location estimator (``R/calc_projections.R:85-93``).

    The median of the original values together with every pairwise average.
    Pairs are formed *before* NA removal and summed with ``na.rm``, so a pair
    containing one NA contributes half the non-missing value -- reproduced here
    because it affects the "robust" estimator's output.
    """
    values = np.asarray(pd.Series(x), dtype=float)
    if values.size <= 2:
        return float(np.nanmean(values)) if na_rm else float(np.mean(values))

    pair_sums = [
        np.nansum([a, b]) / 2 if na_rm else (a + b) / 2
        for a, b in combinations(values, 2)
    ]
    combined = np.concatenate([values, np.asarray(pair_sums, dtype=float)])
    combined = combined[~np.isnan(combined)]  # R's sort() drops NAs
    if combined.size == 0:
        return float("nan")
    return float(np.median(combined))


def row_sd(frame, na_rm: bool = False) -> np.ndarray:
    """``row_sd`` from ``R/helper_funcs.R:151-169`` -- NaN when n-1 <= 1."""
    values = np.asarray(pd.DataFrame(frame), dtype=float)
    n_cols = values.shape[1]

    if na_rm:
        n_minus_1 = n_cols - np.sum(np.isnan(values), axis=1) - 1
    else:
        n_minus_1 = np.full(values.shape[0], n_cols - 1)

    with np.errstate(invalid="ignore", divide="ignore"):
        row_mean = np.nanmean(values, axis=1) if na_rm else np.mean(values, axis=1)
        deviations = (values - row_mean[:, None]) ** 2 / n_minus_1[:, None]
        variance = np.nansum(deviations, axis=1) if na_rm else np.sum(deviations, axis=1)
        sd = np.sqrt(variance)

    sd = np.asarray(sd, dtype=float)
    sd[n_minus_1 <= 1] = np.nan
    return sd


# --------------------------------------------------------------------------
# Coercion
# --------------------------------------------------------------------------

_NA_STRINGS = {"", "NA", "N/A", "-", "--", "—", "–"}


def type_convert(series: pd.Series) -> pd.Series:
    """``utils::type.convert(as.is = TRUE)`` for one column.

    Tries integer, then double, then logical, and otherwise leaves the column
    as character -- the same order R uses.
    """
    if not (series.dtype == object or pd.api.types.is_string_dtype(series)):
        return series

    cleaned = series.astype(object).map(
        lambda v: np.nan if (v is None or (isinstance(v, str) and v.strip() in _NA_STRINGS))
        else (v.strip() if isinstance(v, str) else v)
    )
    present = cleaned.dropna()
    if present.empty:
        return cleaned

    texts = present.astype(str).str.replace(",", "", regex=False)

    try:
        as_int = texts.astype("int64")
        out = pd.Series(np.nan, index=series.index, dtype="float64")
        out.loc[as_int.index] = as_int.astype("float64")
        return out.astype("Int64") if out.isna().any() else out.astype("int64")
    except (ValueError, TypeError, OverflowError):
        pass

    try:
        as_float = texts.astype("float64")
        out = pd.Series(np.nan, index=series.index, dtype="float64")
        out.loc[as_float.index] = as_float
        return out
    except (ValueError, TypeError):
        pass

    lowered = texts.str.upper()
    if lowered.isin({"TRUE", "FALSE", "T", "F"}).all():
        out = pd.Series(pd.NA, index=series.index, dtype="boolean")
        out.loc[lowered.index] = lowered.isin({"TRUE", "T"})
        return out

    return cleaned


def type_convert_frame(frame: pd.DataFrame, exclude: Sequence[str] = ()) -> pd.DataFrame:
    """Apply :func:`type_convert` to every column except ``exclude``."""
    out = frame.copy()
    for column in out.columns:
        if column not in exclude:
            out[column] = type_convert(out[column])
    return out
