"""Filling in the stats a source did not report.

Sites publish different stat lines: one gives targets, another does not; only
some break field goals out by distance; almost none report how many 100-yard
games a back will have.  Scoring a source on its missing columns as zero would
quietly punish it, so before anything is scored the gaps are filled *across
sources for the same player*:

* a column with a natural denominator is estimated from the rate the other
  sources imply (targets per reception, rushing scores per rushing yard);
* anything else takes the mean of the sources that did report it;
* the milestone bonus columns, which no site reports, come from a regression
  on the matching yardage column fitted against historical play-by-play.
"""

from __future__ import annotations

import functools
import json
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from .players import DATA_DIR

__all__ = [
    "impute_missing_stats",
    "impute_bonus_columns",
    "impute_first_downs",
    "from_rate",
    "from_mean",
]


@functools.lru_cache(maxsize=1)
def _bonus_models() -> tuple[dict[str, tuple[float, str, float]], dict[str, list[str]]]:
    """``{column: (intercept, source column, slope)}`` and the nesting rollups."""
    payload = json.loads((DATA_DIR / "bonus_cols.json").read_text())
    coefficients = {
        name: (float(intercept), str(column), float(slope))
        for name, (intercept, column, slope) in payload["bonus_col_coefs"].items()
    }
    return coefficients, payload["bonus_col_sets"]


def from_mean(values: pd.Series) -> pd.Series:
    """Fill gaps with the mean of the sources that did report the stat."""
    numbers = pd.to_numeric(values, errors="coerce")
    if numbers.isna().all():
        return numbers
    return numbers.fillna(numbers.mean())


def from_rate(values: pd.Series, reference: pd.Series) -> pd.Series:
    """Fill gaps with the average ``values / reference`` rate times ``reference``.

    Falls back to the plain mean when a reference behind a known value is zero,
    since the rate would be meaningless.
    """
    numbers = pd.to_numeric(values, errors="coerce")
    denominator = pd.to_numeric(reference, errors="coerce")
    missing = numbers.isna()

    if missing.all() or not missing.any():
        return numbers
    known = ~missing
    if (denominator[known] == 0).any() or denominator[known].isna().all():
        return from_mean(numbers)

    with np.errstate(invalid="ignore", divide="ignore"):
        rate = (numbers[known] / denominator[known]).mean()
    if not np.isfinite(rate):
        return from_mean(numbers)
    return numbers.mask(missing, denominator * rate)


def _rate_rule(column: str, reference: str) -> Callable[[pd.DataFrame], pd.Series]:
    def rule(frame: pd.DataFrame) -> pd.Series:
        if reference not in frame.columns:
            return from_mean(frame[column])
        return from_rate(frame[column], frame[reference])

    return rule


def _field_goals_missed(frame: pd.DataFrame) -> pd.Series:
    """Misses are attempts minus makes where a site reports both."""
    missed = pd.to_numeric(frame["fg_miss"], errors="coerce")
    made = pd.to_numeric(frame.get("fg"), errors="coerce")
    attempted = pd.to_numeric(frame.get("fg_att"), errors="coerce")
    if attempted is not None and made is not None:
        missed = missed.mask(missed.isna(), attempted - made)
    return from_rate(missed, made) if made is not None else from_mean(missed)


#: Stat -> the stat whose rate it is estimated from.
_RATE_RULES: Mapping[str, Callable[[pd.DataFrame], pd.Series]] = {
    "pass_att": _rate_rule("pass_att", "pass_yds"),
    "pass_comp": _rate_rule("pass_comp", "pass_yds"),
    "pass_tds": _rate_rule("pass_tds", "pass_comp"),
    "pass_int": _rate_rule("pass_int", "pass_att"),
    "rush_att": _rate_rule("rush_att", "rush_yds"),
    "rush_tds": _rate_rule("rush_tds", "rush_yds"),
    "rec": _rate_rule("rec", "rec_yds"),
    "rec_tgt": _rate_rule("rec_tgt", "rec"),
    "rec_tds": _rate_rule("rec_tds", "rec_yds"),
    "xp_att": _rate_rule("xp_att", "xp"),
    "xp_miss": _rate_rule("xp_miss", "xp"),
    "fg_att": _rate_rule("fg_att", "fg"),
    "fg_0019": _rate_rule("fg_0019", "fg"),
    "fg_2029": _rate_rule("fg_2029", "fg"),
    "fg_3039": _rate_rule("fg_3039", "fg"),
    "fg_4049": _rate_rule("fg_4049", "fg"),
    "fg_50": _rate_rule("fg_50", "fg"),
    "fg_miss": _field_goals_missed,
}


def _reconcile_kicking(frame: pd.DataFrame) -> pd.DataFrame:
    """Derive the kicking totals the sites report inconsistently.

    Some publish only a total, some only the distance buckets, some a combined
    1-39 bucket, and a couple report a percentage instead of attempts.
    """
    frame = frame.copy()
    columns = set(frame.columns)

    def numeric(name: str) -> pd.Series:
        return pd.to_numeric(frame[name], errors="coerce") if name in columns \
            else pd.Series(np.nan, index=frame.index)

    # A combined "1-39" bucket splits across the three buckets it covers, in
    # proportion to how often kickers actually attempt from each range.
    if "fg_0039" in columns:
        combined = numeric("fg_0039")
        for name, share in (("fg_0019", 0.04), ("fg_2029", 0.38), ("fg_3039", 0.58)):
            estimate = combined * share
            frame[name] = numeric(name).fillna(estimate) if name in columns else estimate
        frame = frame.drop(columns="fg_0039")
        columns = set(frame.columns)

    buckets = [c for c in ("fg_0019", "fg_2029", "fg_3039", "fg_4049", "fg_50")
               if c in columns]
    if buckets:
        total = frame[buckets].apply(pd.to_numeric, errors="coerce").sum(
            axis=1, skipna=True
        )
        frame["fg"] = numeric("fg").fillna(total) if "fg" in columns else total

    missed = [c for c in ("fg_miss_0019", "fg_miss_2029", "fg_miss_3039",
                          "fg_miss_4049", "fg_miss_50") if c in columns]
    if "fg_miss" not in columns and missed:
        frame["fg_miss"] = frame[missed].apply(pd.to_numeric, errors="coerce").sum(
            axis=1, skipna=True
        )
    if "fg_miss" not in frame.columns:
        frame["fg_miss"] = np.nan

    if "fg_att" not in frame.columns:
        frame["fg_att"] = numeric("fg") + pd.to_numeric(frame["fg_miss"], errors="coerce")
    elif "fg_pct" in columns:
        percentage = numeric("fg_pct")
        with np.errstate(invalid="ignore", divide="ignore"):
            implied = numeric("fg") / (percentage * 0.01)
        frame["fg_att"] = numeric("fg_att").fillna(implied)

    if "xp_miss" not in frame.columns:
        frame["xp_miss"] = (
            numeric("xp_att") - numeric("xp") if "xp_att" in columns else np.nan
        )
    if "xp_att" not in frame.columns:
        frame["xp_att"] = numeric("xp") + pd.to_numeric(frame["xp_miss"], errors="coerce")

    return frame


def impute_missing_stats(frames: Mapping[str, pd.DataFrame],
                         scoring_rules) -> dict[str, pd.DataFrame]:
    """Fill every scored column that some source left out, player by player."""
    out = {}
    for position, frame in frames.items():
        frame = frame.copy()
        if position == "K":
            frame = _reconcile_kicking(frame)

        scored = {
            stat for stat, value in scoring_rules.for_position(position).items()
            if value
        }
        if position == "DST":
            scored.add("dst_pts_allowed")

        gaps = [
            column for column in frame.columns
            if column in scored and frame[column].isna().any()
        ]
        for column in gaps:
            rule = _RATE_RULES.get(column)
            if rule is None:
                frame[column] = frame.groupby("id", sort=False)[column].transform(
                    from_mean
                )
                continue
            # A rate rule needs several columns at once, so each player's rows
            # go through it together.
            filled = pd.concat(
                [pd.Series(rule(group), index=group.index)
                 for _, group in frame.groupby("id", sort=False)]
            )
            frame[column] = filled.reindex(frame.index)

        out[position] = frame
    return out


#: First downs per yard, by position.
#:
#: No site projects first downs, but they track yardage closely enough to be
#: estimated from it.  Rushing first downs come at one rate regardless of who
#: is carrying; receiving first downs differ by position, because a back's
#: catches are shorter and convert less often than a tight end's.
#:
#: There is deliberately no passing rate here: none was supplied, so a league
#: that scores passing first downs still has that part reported as unscored
#: rather than guessed at.
FIRST_DOWN_RATES: Mapping[str, Mapping[str, float]] = {
    "rush_fd": {"QB": 0.0508, "RB": 0.0508, "WR": 0.0508, "TE": 0.0508},
    "rec_fd": {"RB": 0.0450, "WR": 0.0483, "TE": 0.0503},
}

#: The yardage column each first-down estimate is built from.
_FIRST_DOWN_SOURCE = {"rush_fd": "rush_yds", "rec_fd": "rec_yds"}


def impute_first_downs(frames: Mapping[str, pd.DataFrame],
                       scoring_rules) -> dict[str, pd.DataFrame]:
    """Estimate first downs from yardage, for leagues that score them."""
    out = dict(frames)

    for position, frame in frames.items():
        scored = {
            stat for stat, value in scoring_rules.for_position(position).items()
            if value
        }
        estimates = {
            stat: FIRST_DOWN_RATES[stat][position]
            for stat in ("rush_fd", "rec_fd")
            if stat in scored
            and position in FIRST_DOWN_RATES[stat]
            and _FIRST_DOWN_SOURCE[stat] in frame.columns
        }
        if not estimates:
            continue

        frame = frame.copy()
        for stat, rate in estimates.items():
            yards = pd.to_numeric(frame[_FIRST_DOWN_SOURCE[stat]], errors="coerce")
            estimate = (yards * rate).clip(lower=0)
            frame[stat] = (
                pd.to_numeric(frame[stat], errors="coerce").fillna(estimate)
                if stat in frame.columns else estimate
            )
        out[position] = frame

    return out


def impute_bonus_columns(frames: Mapping[str, pd.DataFrame],
                         scoring_rules) -> dict[str, pd.DataFrame]:
    """Estimate the milestone bonus columns no site publishes.

    Only columns the league actually scores are added, so a league without
    yardage bonuses is untouched.
    """
    coefficients, rollups = _bonus_models()
    out = dict(frames)

    for position, frame in frames.items():
        scored = {
            stat for stat, value in scoring_rules.for_position(position).items()
            if value
        }
        applicable = {
            name: model for name, model in coefficients.items()
            if name in scored and model[1] in frame.columns
        }
        if not applicable:
            continue

        frame = frame.copy()
        for name, (intercept, source_column, slope) in applicable.items():
            estimate = intercept + pd.to_numeric(
                frame[source_column], errors="coerce"
            ) * slope
            estimate = estimate.clip(lower=0)
            frame[name] = (
                pd.to_numeric(frame[name], errors="coerce").fillna(estimate)
                if name in frame.columns else estimate
            )

        # The thresholds nest: a 400-yard game is also a 300-yard game.
        for name in applicable:
            parts = [c for c in rollups.get(name, []) if c in frame.columns]
            if parts:
                frame[name] = frame[parts].sum(axis=1, skipna=True)

        out[position] = frame

    return out
