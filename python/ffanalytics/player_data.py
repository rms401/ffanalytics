"""The canonical player universe.

The R package downloads this table from S3 in ``.onLoad`` and keeps it in the
namespace (``R/ffanalytics.R:6-19``).  Here it is fetched on first use and
memoised, so importing the package does no network I/O.

Every projection row is keyed by ``id``, the MyFantasyLeague player id, and
this table is what :func:`ffanalytics.helper_funcs.get_mfl_id` resolves names
against.
"""

from __future__ import annotations

import os
from functools import lru_cache

import pandas as pd

__all__ = ["PLAYER_TABLE_URL", "player_table", "reset_player_table"]

PLAYER_TABLE_URL = (
    "https://s3.us-east-2.amazonaws.com/ffanalytics/packagedata/player_table.csv"
)

# Column types from R's `fread(colClasses = ...)` call.  `id` and the various
# source ids must stay character: they are zero-padded and would lose their
# leading zeros as integers.
_DTYPES = {
    "id": "string",
    "last_name": "string",
    "first_name": "string",
    "position": "string",
    "team": "string",
    "weight": "Int64",
    "draft_year": "Int64",
    "draft_team": "string",
    "draft_round": "Int64",
    "draft_pick": "Int64",
    "age": "Int64",
    "exp": "Int64",
}

_COLUMNS = [
    "id", "last_name", "first_name", "position", "team", "weight", "draft_year",
    "draft_team", "draft_round", "draft_pick", "birthdate", "age", "exp",
]


@lru_cache(maxsize=1)
def player_table() -> pd.DataFrame:
    """Player names, teams, positions, age and experience, keyed by MFL id."""
    url = os.environ.get("FFANALYTICS_PLAYER_TABLE", PLAYER_TABLE_URL)
    frame = pd.read_csv(
        url,
        dtype={k: v for k, v in _DTYPES.items()},
        parse_dates=["birthdate"],
        na_values=["NA", ""],
        keep_default_na=True,
    )
    frame = frame[_COLUMNS]
    return frame


def reset_player_table() -> None:
    """Drop the memoised table so the next call re-downloads it."""
    player_table.cache_clear()
