"""Shared helpers, chiefly the player-identity resolver.

Ported from ``R/helper_funcs.R``.
"""

from __future__ import annotations

import datetime as _dt
import re
import string
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd

from .player_data import player_table
from .rcompat.stats import rename_vec
from .recode_vars import pos_corrections, team_corrections

__all__ = ["get_mfl_id", "get_scrape_year", "lapply_safe", "normalize_key"]

_SUFFIX_RE = re.compile(r"\s+(defense|jr|sr|[iv]+)\.?$")
_PUNCT_OR_SPACE_RE = re.compile(f"[{re.escape(string.punctuation)}]+|\\s+")

#: Fallback match orders, tried in turn (``R/helper_funcs.R:77-84``).
_COLUMN_COMBOS = (
    ("player_name", "pos", "team"),
    ("last", "pos", "team"),
    ("player_name", "team"),
    ("player_name", "pos"),
    ("first", "pos", "team"),
)


def get_scrape_year(date: _dt.date | None = None) -> int:
    """The season a date belongs to -- January to March belong to the prior season."""
    date = date or _dt.date.today()
    return date.year - 1 if date.month in (1, 2, 3) else date.year


def lapply_safe(items: Iterable[Any], func: Callable[[Any], Any], if_error: Any = None) -> list:
    """``lapply`` that swallows and reports per-element errors, as R's does."""
    out = []
    for item in items:
        try:
            out.append(func(item))
        except Exception as error:  # noqa: BLE001 - mirrors R's tryCatch(print(e))
            print(error)
            out.append(if_error)
    return out


def _strip_suffix_and_punct(values: pd.Series) -> pd.Series:
    lowered = values.str.lower()
    stripped = lowered.str.replace(_SUFFIX_RE, "", regex=True)
    return stripped.str.replace(_PUNCT_OR_SPACE_RE, "", regex=True)


def normalize_key(values) -> pd.Series:
    """Normalise a name/position/team column the way ``get_mfl_id`` does.

    Uppercase, apply the position then team corrections, lowercase, drop a
    trailing "defense"/"jr"/"sr"/roman numeral, then remove all punctuation and
    whitespace (``R/helper_funcs.R:33-39``).
    """
    series = pd.Series(values, dtype="object").astype("string")
    upper = series.str.upper().tolist()
    corrected = rename_vec(rename_vec(upper, pos_corrections), team_corrections)
    corrected = pd.Series(corrected, index=series.index, dtype="object").astype("string")
    return _strip_suffix_and_punct(corrected)


def _reference_table() -> pd.DataFrame:
    """``player_table`` normalised for matching (``R/helper_funcs.R:53-66``).

    R lowercases every character column first, so the name is assembled from
    already-lowercased parts.  Row order is preserved because the cascade takes
    the first match, and the DST rows come first in the source table.
    """
    players = player_table()
    lower = players.copy()
    for column in ("id", "last_name", "first_name", "position", "team", "draft_team"):
        lower[column] = lower[column].astype("string").str.lower()

    full_name = lower["first_name"].fillna("") + " " + lower["last_name"].fillna("")

    return pd.DataFrame(
        {
            "id": players["id"].astype("string"),
            "player_name": _strip_suffix_and_punct(full_name),
            "last": _strip_suffix_and_punct(lower["last_name"]),
            "first": _strip_suffix_and_punct(lower["first_name"]),
            "pos": normalize_key(lower["position"]),
            "team": normalize_key(lower["team"]),
        }
    )


def _recycle(value, length: int) -> pd.Series:
    series = pd.Series(value, dtype="object") if not isinstance(value, pd.Series) else value
    series = series.reset_index(drop=True)
    if len(series) == 1 and length > 1:
        series = pd.Series(series.iloc[0], index=range(length), dtype="object")
    return series.astype("string")


def get_mfl_id(
    id_col=None,
    *,
    id_col_name: str | None = None,
    player_name=None,
    first=None,
    last=None,
    pos=None,
    team=None,
) -> pd.Series:
    """Resolve MyFantasyLeague player ids from source ids and/or names.

    Tries the ``player_ids`` crosswalk first when ``id_col`` is supplied, then
    falls back to progressively looser name matching against ``player_table``.
    Ambiguous reference rows are discarded at every step, so an uncertain match
    yields ``NA`` rather than the wrong player.

    ``id_col_name`` names the crosswalk column to look ``id_col`` up in.  R
    derives it from the calling expression via
    ``deparse(substitute(id_col))`` (``R/helper_funcs.R:42-46``), which Python
    has no equivalent for; callers pass it explicitly instead.  A name that is
    not a crosswalk column makes the fast path a no-op and falls through to the
    name cascade -- which is what happens in R at ``source_scrapes.R:642`` and
    ``:1697`` too.
    """
    supplied = {
        "player_name": player_name,
        "first": first,
        "last": last,
        "pos": pos,
        "team": team,
    }
    supplied = {key: value for key, value in supplied.items() if value is not None}

    lengths = [len(pd.Series(v, dtype="object")) if not np.isscalar(v) else 1
               for v in supplied.values()]
    if id_col is not None:
        lengths.append(len(pd.Series(id_col, dtype="object")) if not np.isscalar(id_col) else 1)
    max_len = max(lengths) if lengths else 1

    info = {key: _recycle(value, max_len) for key, value in supplied.items()}

    # first/last fall out of the full name when not given separately
    if player_name is not None:
        raw_name = _recycle(player_name, max_len)
        if first is None:
            info["first"] = raw_name.str.replace(r"\s+.*$", "", regex=True)
        if last is None:
            info["last"] = raw_name.str.replace(r".*?\s+", "", regex=True)

    info = {key: normalize_key(value) for key, value in info.items()}
    ids = pd.Series(pd.NA, index=range(max_len), dtype="string")

    # 1. direct crosswalk lookup
    if id_col is not None and id_col_name:
        from .sysdata import player_ids as _player_ids

        crosswalk = _player_ids()
        if id_col_name in crosswalk.columns:
            lookup = (
                crosswalk[[id_col_name, "id"]]
                .dropna(subset=[id_col_name])
                .drop_duplicates(subset=[id_col_name], keep="first")
                .set_index(id_col_name)["id"]
            )
            keys = _recycle(id_col, max_len)
            ids = keys.map(lookup).astype("string")
            if not ids.isna().any():
                return ids

    reference = _reference_table()

    # 2. team defenses resolve by team
    if "pos" in info and "team" in info:
        team_to_id = (
            reference.dropna(subset=["team"])
            .drop_duplicates(subset=["team"], keep="first")
            .set_index("team")["id"]
        )
        is_dst = info["pos"] == "dst"
        ids = ids.mask(is_dst, info["team"].map(team_to_id).astype("string"))

    # 3. progressively looser name matching
    for combo in _COLUMN_COMBOS:
        if not all(column in info for column in combo):
            continue
        missing = ids.isna()
        if not missing.any():
            break

        wanted = _paste0([info[column] for column in combo])[missing]
        reference_keys = _paste0([reference[column] for column in combo])

        # Ambiguous reference rows are dropped so they cannot match anything.
        unique_only = ~reference_keys.duplicated(keep=False)
        candidates = reference_keys[unique_only]
        key_to_id = pd.Series(
            reference["id"][unique_only].to_numpy(), index=candidates.to_numpy()
        )

        ids.loc[missing] = wanted.map(key_to_id).astype("string")

    return ids


def _paste0(columns: Sequence[pd.Series]) -> pd.Series:
    """``paste0`` -- concatenate elementwise, rendering NA as the text "NA"."""
    out = None
    for column in columns:
        piece = column.astype("object").where(column.notna(), "NA").astype(str)
        piece = piece.reset_index(drop=True) if out is None else piece.reset_index(drop=True)
        out = piece if out is None else out + piece
    return out if out is not None else pd.Series([], dtype="object")
