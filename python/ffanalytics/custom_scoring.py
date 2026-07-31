"""Building scoring rules and flattening them into per-position tables.

Ported from ``R/custom_scoring.R``.
"""

from __future__ import annotations

import copy
from typing import Any

import pandas as pd

from .scoring_rules import scoring as default_scoring
from .scoring_rules import scoring_empty, scoring_type_for_cols

__all__ = ["custom_scoring", "make_scoring_tables"]

_POSITIONS = ["QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB"]
_SCORING_POSITIONS = ("QB", "RB", "WR", "TE", "DL", "LB", "DB")
_ALL_POS_CATEGORIES = ("pass", "rush", "rec", "misc", "ret", "idp")


def _flatten(prefix: str, value: Any) -> list[tuple[str, Any]]:
    """``unlist(x, use.names = TRUE)`` -- dotted paths to scalar leaves."""
    if isinstance(value, dict):
        out: list[tuple[str, Any]] = []
        for key, child in value.items():
            out.extend(_flatten(f"{prefix}.{key}" if prefix else str(key), child))
        return out
    return [(prefix, value)]


def _prune(node: Any) -> Any:
    """``rrapply(..., how = "prune")`` -- drop None leaves and empty branches."""
    if not isinstance(node, dict):
        return node
    out = {}
    for key, value in node.items():
        if isinstance(value, dict):
            pruned = _prune(value)
            if pruned:
                out[key] = pruned
        elif value is not None:
            out[key] = value
    return out


def _assign(tree: dict, path: list[str], value: Any) -> None:
    node = tree
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value


def custom_scoring(**kwargs: Any) -> dict:
    """Build a scoring rules object from stat values and per-position overrides.

    Pass stat values directly, and per-position overrides as a dict keyed by
    position.  Passing, kicking and DST scoring cannot vary by position, so
    never nest those.

    The result still needs a ``pts_bracket`` entry before it can score DSTs --
    same as the R function.  See the R vignette ``scoring_settings``.

    >>> rules = custom_scoring(pass_yds=0.04, pass_tds=4, rec=1, rec_yds=0.1)
    >>> rules["rec"]["all_pos"]
    True
    >>> te_premium = custom_scoring(
    ...     RB={"rec": 1, "rec_yds": 0.1},
    ...     TE={"rec": 1.5, "rec_yds": 0.1},
    ... )
    >>> te_premium["rec"]["all_pos"]
    False
    """
    obj = copy.deepcopy(scoring_empty)

    for dotted, value in _flatten("", kwargs):
        path = dotted.split(".")
        stat = path[-1]
        category = scoring_type_for_cols.get(stat)
        if category is None:
            raise ValueError(
                f"{stat!r} is not a scoring variable. See ffanalytics.scoring "
                "for the available names."
            )
        _assign(obj, [category, *path], value)

    obj = _prune(obj)

    # A category scores every position unless it names positions explicitly.
    for category in [c for c in obj if c in _ALL_POS_CATEGORIES]:
        all_pos = not any(key in _SCORING_POSITIONS for key in obj[category])
        obj[category] = {"all_pos": all_pos, **{k: v for k, v in obj[category].items()
                                                if k != "all_pos"}}
    return obj


def make_scoring_tables(scoring_rules: dict | None = None) -> dict:
    """Flatten scoring rules into one ``(category, column, val)`` table per position.

    Returns ``{"pts_bracket": [...], "scoring_tables": {position: DataFrame}}``.
    Ported from ``R/custom_scoring.R:71-126``.
    """
    if scoring_rules is None:
        scoring_rules = default_scoring

    rules = copy.deepcopy(scoring_rules)
    pts_bracket = [
        {key: float(value) for key, value in entry.items()}
        for entry in rules.pop("pts_bracket", [])
    ]

    # Categories without an `all_pos` element contribute nothing here, matching
    # R's `unlist(lapply(scoring_rules, `[[`, "all_pos"))`.
    all_pos_flags = {
        category: body["all_pos"]
        for category, body in rules.items()
        if isinstance(body, dict) and "all_pos" in body
    }

    tables: dict[str, pd.DataFrame] = {}

    if all(all_pos_flags.values()):
        table = _scoring_frame(rules)
        for position in _POSITIONS:
            if position == "DST":
                # R rebinds `scoring_table` here, so every position from DST
                # onward (DST, DL, LB, DB) inherits the pts_bracket row.
                table = _with_pts_bracket(table)
            tables[position] = table.copy()
    else:
        custom_categories = [c for c, flag in all_pos_flags.items() if not flag]
        for position in _POSITIONS:
            position_rules = copy.deepcopy(rules)
            for category in custom_categories:
                if position in position_rules[category]:
                    position_rules[category] = position_rules[category][position]
                else:
                    del position_rules[category]

            table = _scoring_frame(position_rules)
            if position == "DST":
                table = _with_pts_bracket(table)
            tables[position] = table

    return {"pts_bracket": pts_bracket, "scoring_tables": tables}


def _scoring_frame(rules: dict) -> pd.DataFrame:
    """One tidy ``(category, column, val)`` table for a scoring list."""
    categories: list[str] = []
    columns: list[str] = []
    values: list[float] = []
    for category, body in rules.items():
        for dotted, value in _flatten("", body):
            categories.append(category)
            # R keeps only the segment after the first dot, so a per-position
            # entry such as `rec.TE.rec` reduces to `TE.rec` -- but by this
            # point positions have already been substituted away.
            columns.append(dotted.split(".")[-1])
            values.append(float(value))
    return pd.DataFrame({"category": categories, "column": columns, "val": values})


def _with_pts_bracket(table: pd.DataFrame) -> pd.DataFrame:
    extra = pd.DataFrame({"category": ["dst"], "column": ["pts_bracket"], "val": [1.0]})
    return pd.concat([table, extra], ignore_index=True)
