"""Filling in stats that some sources do not report.

Ported from ``R/impute_funcs.R``.  Imputation happens *across sources* for the
same player: rows are grouped by ``id``, so a source that omits a column
inherits either a rate-derived estimate or the mean of the sources that do
report it.
"""

from __future__ import annotations

from typing import Callable, Mapping

import numpy as np
import pandas as pd

from .results import ScrapeResult
from .sysdata import bonus_col_coefs, bonus_col_sets

__all__ = [
    "derive_from_rate",
    "derive_from_mean",
    "impute_fun_list",
    "impute_via_rates_and_mean",
    "impute_bonus_cols",
]


def derive_from_mean(need: pd.Series) -> pd.Series:
    """Fill missing values with the mean of the values that are present."""
    values = pd.to_numeric(need, errors="coerce")
    missing = values.isna()
    if missing.all():
        return values
    return values.fillna(values.mean())


def derive_from_rate(need: pd.Series, ref: pd.Series) -> pd.Series:
    """Fill missing values from the average ``need/ref`` rate times ``ref``.

    Falls back to :func:`derive_from_mean` when any reference value backing a
    known observation is zero, since the rate would be undefined.
    """
    values = pd.to_numeric(need, errors="coerce")
    reference = pd.to_numeric(ref, errors="coerce")
    missing = values.isna()

    if missing.all():
        return values
    if (reference[~missing] == 0).any():
        return derive_from_mean(values)

    with np.errstate(invalid="ignore", divide="ignore"):
        rate = (values / reference).sum(skipna=True) / (~missing).sum()
    return values.mask(missing, reference * rate)


def _rate(need_col: str, ref_col: str) -> Callable[[pd.DataFrame], pd.Series]:
    def impute(frame: pd.DataFrame) -> pd.Series:
        return derive_from_rate(frame[need_col], frame[ref_col])

    return impute


def _impute_fg_0019(frame: pd.DataFrame) -> pd.Series:
    """``fg_0019`` from ``R/impute_funcs.R:66-72``.

    The R version starts with a branch that folds ``fg_1019`` into ``fg_0019``,
    but that branch is unreachable: it tests ``names(df)`` where ``df`` is not
    an argument, so R resolves it lexically to ``stats::df`` (a function) whose
    ``names()`` is NULL, making the condition always false.  Lazy evaluation
    also means ``fg_1019`` is never forced, so it need not exist.  Only the
    rate derivation actually runs.
    """
    return derive_from_rate(frame["fg_0019"], frame["fg"])


def _impute_fg_miss(frame: pd.DataFrame) -> pd.Series:
    """Missed field goals: ``fg_att - fg`` where absent, then rate-derived."""
    fg_miss = pd.to_numeric(frame["fg_miss"], errors="coerce")
    fg = pd.to_numeric(frame["fg"], errors="coerce")
    fg_att = pd.to_numeric(frame["fg_att"], errors="coerce")
    fg_miss = fg_miss.mask(fg_miss.isna(), fg_att - fg)
    return derive_from_rate(fg_miss, fg)


#: Column -> rate rule.  ``R/impute_funcs.R:32-90``.
#:
#: ``rec_tgt`` is defined twice in R; ``[[`` returns the *first* match, so the
#: ``rec_tgt ~ rec`` rule is the live one and the second entry (evidently
#: intended for ``rec``) is unreachable.  ``rec`` therefore falls through to
#: the plain column mean.
impute_fun_list: Mapping[str, Callable[[pd.DataFrame], pd.Series]] = {
    "pass_att": _rate("pass_att", "pass_yds"),
    "pass_comp": _rate("pass_comp", "pass_yds"),
    "pass_tds": _rate("pass_tds", "pass_comp"),
    "pass_int": _rate("pass_int", "pass_att"),
    "rush_att": _rate("rush_att", "rush_yds"),
    "rush_tds": _rate("rush_tds", "rush_yds"),
    "rec_tgt": _rate("rec_tgt", "rec"),
    "rec_tds": _rate("rec_tds", "rec_yds"),
    "xp_att": _rate("xp_att", "xp"),
    "fg_att": _rate("fg_att", "fg"),
    "fg_0019": _impute_fg_0019,
    "fg_2029": _rate("fg_2029", "fg"),
    "fg_3039": _rate("fg_3039", "fg"),
    "fg_4049": _rate("fg_4049", "fg"),
    "fg_50": _rate("fg_50", "fg"),
    "fg_miss": _impute_fg_miss,
}


def _reconcile_kicker_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive the kicker totals sources report inconsistently."""
    frame = frame.copy()
    columns = set(frame.columns)

    miss_cols = ["fg_miss_0019", "fg_miss_2029", "fg_miss_3039", "fg_miss_4049", "fg_miss_50"]
    fg_cols = ["fg_0019", "fg_2029", "fg_3039", "fg_4049", "fg_50", "fg_0039"]

    if "fg_miss" not in columns and all(col in columns for col in miss_cols):
        frame["fg_miss"] = frame[miss_cols].sum(axis=1, skipna=True)

    if "fg" in columns and frame["fg"].isna().any():
        totals = [col for col in fg_cols if col in columns]
        if totals:
            missing = frame["fg"].isna()
            frame.loc[missing, "fg"] = frame.loc[missing, totals].sum(axis=1, skipna=True)

    if "xp_att" not in columns:
        if "xp_miss" not in columns:
            frame["xp_att"] = np.nan
        else:
            frame["xp_att"] = frame["xp"] + frame["xp_miss"]

    if "fg_miss" not in frame.columns:
        frame["fg_miss"] = np.nan
    elif "fg_att" in frame.columns:
        missing = frame["fg_att"].isna()
        frame.loc[missing, "fg_att"] = frame.loc[missing, "fg"] + frame.loc[missing, "fg_miss"]

    if "fg_pct" in frame.columns and pd.api.types.is_numeric_dtype(frame["fg_pct"]):
        if "fg_att" in frame.columns:
            missing = frame["fg_att"].isna()
            with np.errstate(invalid="ignore", divide="ignore"):
                frame.loc[missing, "fg_att"] = (
                    frame.loc[missing, "fg"] / (frame.loc[missing, "fg_pct"] * 0.01)
                )
    return frame


def impute_via_rates_and_mean(data_result, scoring_objs: dict):
    """Impute every scoring-relevant column that any source left missing.

    Grouped by player ``id`` so estimates are pooled across sources
    (``R/impute_funcs.R:93-169``).
    """
    scoring_tables = scoring_objs["scoring_tables"]
    out = {}

    for position, frame in data_result.items():
        frame = frame.copy()
        if position == "K":
            frame = _reconcile_kicker_columns(frame)

        scoring_table = scoring_tables.get(position)
        if scoring_table is None:
            out[position] = frame
            continue

        scored = scoring_table.loc[scoring_table["val"] != 0, "column"].tolist()
        impute_cols = [column for column in frame.columns if column in scored]
        if position == "DST" and "dst_pts_allowed" not in impute_cols:
            impute_cols.append("dst_pts_allowed")

        impute_cols = [
            column for column in impute_cols
            if column in frame.columns and frame[column].isna().any()
        ]

        for column in impute_cols:
            rule = impute_fun_list.get(column)
            if rule is None:
                frame[column] = (
                    frame.groupby("id", group_keys=False, sort=False)[column]
                    .transform(derive_from_mean)
                )
                continue

            # The rate rules need several columns at once, so each player's
            # rows go through the rule together and the results are stitched
            # back in the original order.
            pieces = [
                pd.Series(rule(group), index=group.index)
                for _, group in frame.groupby("id", sort=False)
            ]
            frame[column] = pd.concat(pieces).reindex(frame.index)
        out[position] = frame

    if isinstance(data_result, ScrapeResult):
        return data_result.copy_with(out)
    return out


def impute_bonus_cols(data_result, scoring_tables: dict | None = None):
    """Synthesise milestone-bonus columns for sources that do not report them.

    Each bonus column is estimated from a regression on the matching yardage
    column, floored at zero, then the nested thresholds are rolled up
    (a 400-yard game also counts as a 300-yard game).

    Reproduces one R behaviour worth knowing about: the mutated frame is only
    written back when at least one rollup applies (``R/impute_funcs.R:200-206``
    returns early otherwise), so a position with no rollup keeps its original
    columns.
    """
    coefs = bonus_col_coefs()
    sets = bonus_col_sets()
    out = dict(data_result)

    for position, frame in data_result.items():
        applicable = {
            name: coef for name, coef in coefs.items() if coef[1] in frame.columns
        }
        if not applicable:
            continue

        frame = frame.copy()
        for name, (intercept, ref_col, slope) in applicable.items():
            estimate = intercept + pd.to_numeric(frame[ref_col], errors="coerce") * slope
            if name in frame.columns:
                frame[name] = pd.to_numeric(frame[name], errors="coerce").fillna(estimate)
            else:
                frame[name] = estimate
            frame[name] = frame[name].mask(frame[name] < 0, 0)

        rollups = [name for name in applicable if name in sets]
        if not rollups:
            # R's `next` discards the frame here, so the bonus columns above
            # are not persisted for this position.
            continue

        for name in rollups:
            frame[name] = frame[sets[name]].sum(axis=1, skipna=True)
        out[position] = frame

    if isinstance(data_result, ScrapeResult):
        return data_result.copy_with(out)
    return out
