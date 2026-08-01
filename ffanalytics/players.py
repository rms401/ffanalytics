"""The player universe and the identity resolver everything keys off.

Every projection row, every roster entry and every ranking is keyed by ``id``,
the MyFantasyLeague player id.  Sources publish their own ids and their own
spellings of a player's name, so this module does two things:

* looks an id up in the bundled crosswalk (``data/player_ids.csv``), which
  covers the ids the projection sites and Sleeper use; and
* falls back to progressively looser name matching against the player table
  when a source id is unknown.

Ambiguous candidates are dropped at every step, so an uncertain match yields
``NA`` rather than the wrong player.
"""

from __future__ import annotations

import functools
import os
import re
import string
from pathlib import Path

import pandas as pd

__all__ = [
    "player_table",
    "player_ids",
    "resolve_ids",
    "ids_from_source",
    "normalize",
    "TEAMS",
    "TEAM_CORRECTIONS",
    "POSITION_CORRECTIONS",
]

DATA_DIR = Path(__file__).resolve().parent / "data"

PLAYER_TABLE_URL = (
    "https://s3.us-east-2.amazonaws.com/ffanalytics/packagedata/player_table.csv"
)

#: Spellings the sources use for a position, mapped onto ours.
POSITION_CORRECTIONS = {
    "DEF": "DST", "DEF.": "DST", "D": "DST", "D/ST": "DST", "DST": "DST",
    "PK": "K",
    "CB": "DB", "S": "DB", "FS": "DB", "SS": "DB",
    "DE": "DL", "DT": "DL",
    "ILB": "LB", "OLB": "LB",
    "FB": "RB",
}

#: Team abbreviations the sources use, mapped onto ours.
TEAM_CORRECTIONS = {
    "KCC": "KC", "SFO": "SF", "TBB": "TB", "NEP": "NE", "NWE": "NE",
    "RAM": "LAR", "LA": "LAR", "STL": "LAR",
    "SDC": "LAC", "SD": "LAC",
    "ARZ": "ARI", "NOR": "NO", "NOS": "NO", "GBP": "GB",
    "JAX": "JAC", "WSH": "WAS", "HST": "HOU", "CLV": "CLE", "BLT": "BAL",
    "LVR": "LV", "OAK": "LV",
}

#: The 32 team abbreviations, in the spelling this package uses.
TEAMS = (
    "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE", "DAL", "DEN",
    "DET", "GB", "HOU", "IND", "JAC", "KC", "LAC", "LAR", "LV", "MIA",
    "MIN", "NE", "NO", "NYG", "NYJ", "PHI", "PIT", "SEA", "SF", "TB",
    "TEN", "WAS",
)

_SUFFIX = re.compile(r"\s+(defense|jr|sr|[iv]+)\.?$")
_PUNCT_OR_SPACE = re.compile(f"[{re.escape(string.punctuation)}]+|\\s+")

_PLAYER_DTYPES = {
    "id": "string", "last_name": "string", "first_name": "string",
    "position": "string", "team": "string", "draft_team": "string",
    "weight": "Int64", "draft_year": "Int64", "draft_round": "Int64",
    "draft_pick": "Int64", "age": "Int64", "exp": "Int64",
}


@functools.lru_cache(maxsize=1)
def player_table() -> pd.DataFrame:
    """Names, teams, positions, age and experience for every known player."""
    url = os.environ.get("FFANALYTICS_PLAYER_TABLE", PLAYER_TABLE_URL)
    frame = pd.read_csv(url, dtype=_PLAYER_DTYPES, na_values=["NA", ""])
    return frame[[
        "id", "first_name", "last_name", "position", "team", "age", "exp",
        "draft_year", "draft_round", "draft_pick", "birthdate",
    ]]


@functools.lru_cache(maxsize=1)
def player_ids() -> pd.DataFrame:
    """Crosswalk from each source's player id (and Sleeper's) to the MFL id."""
    return pd.read_csv(DATA_DIR / "player_ids.csv", dtype="string")


def normalize(values) -> pd.Series:
    """Reduce a name, position or team to a comparable key.

    Upper-cases, applies the position and team corrections, then strips a
    trailing "Jr"/"III"/"Defense" and every remaining space and punctuation
    mark, so "A.J. Brown", "AJ Brown" and "aj brown" all collapse together.
    """
    series = pd.Series(values, dtype="object").astype("string")
    upper = series.str.upper()
    corrected = upper.replace(POSITION_CORRECTIONS).replace(TEAM_CORRECTIONS)
    lowered = corrected.str.lower()
    return lowered.str.replace(_SUFFIX, "", regex=True).str.replace(
        _PUNCT_OR_SPACE, "", regex=True
    )


@functools.lru_cache(maxsize=1)
def _match_table() -> pd.DataFrame:
    """The player table reduced to the keys :func:`resolve_ids` matches on."""
    players = player_table()
    first = players["first_name"].fillna("")
    last = players["last_name"].fillna("")
    return pd.DataFrame({
        "id": players["id"].astype("string"),
        "name": normalize(first + " " + last),
        "first": normalize(first),
        "last": normalize(last),
        "pos": normalize(players["position"]),
        "team": normalize(players["team"]),
    })


#: Key combinations tried in turn; the first unambiguous hit wins.
_MATCH_ORDER = (
    ("name", "pos", "team"),
    ("last", "pos", "team"),
    ("name", "team"),
    ("name", "pos"),
    ("first", "pos", "team"),
)


def ids_from_source(source_ids, id_column: str) -> pd.Series:
    """Look source ids up in the crosswalk, returning ``NA`` where unknown."""
    keys = pd.Series(source_ids, dtype="object").astype("string")
    crosswalk = player_ids()
    if id_column not in crosswalk.columns:
        raise KeyError(
            f"{id_column!r} is not a crosswalk column. Available: "
            + ", ".join(c for c in crosswalk.columns if c != "id")
        )
    lookup = (
        crosswalk[[id_column, "id"]]
        .dropna(subset=[id_column])
        .drop_duplicates(subset=[id_column], keep="first")
        .set_index(id_column)["id"]
    )
    return keys.map(lookup).astype("string").reset_index(drop=True)


def resolve_ids(
    source_ids=None,
    id_column: str | None = None,
    *,
    name=None,
    first=None,
    last=None,
    pos=None,
    team=None,
) -> pd.Series:
    """Resolve MFL player ids from a source's ids and/or the names it prints.

    Pass whatever a source gives you.  The crosswalk is consulted first when
    ``source_ids``/``id_column`` are supplied; anything it cannot place falls
    through to name matching, which needs at least ``name`` (or ``last``) plus
    a position or team to be useful.
    """
    columns = {"name": name, "first": first, "last": last, "pos": pos, "team": team}
    columns = {key: value for key, value in columns.items() if value is not None}

    length = _common_length(columns, source_ids)
    keys = {key: normalize(_recycle(value, length)) for key, value in columns.items()}

    # Split a full name so the looser first/last passes have something to use.
    if name is not None:
        raw = pd.Series(_recycle(name, length), dtype="object").astype("string")
        keys.setdefault("first", normalize(raw.str.replace(r"\s+.*$", "", regex=True)))
        keys.setdefault("last", normalize(raw.str.replace(r"^\S+\s+", "", regex=True)))

    ids = pd.Series(pd.NA, index=range(length), dtype="string")

    if source_ids is not None and id_column:
        ids = ids_from_source(_recycle(source_ids, length), id_column)
        if not ids.isna().any():
            return ids

    reference = _match_table()

    # Team defenses are one per team, so the team alone identifies them.
    if "pos" in keys and "team" in keys:
        by_team = (
            reference[reference["pos"] == "dst"]
            .drop_duplicates(subset=["team"], keep="first")
            .set_index("team")["id"]
        )
        is_dst = keys["pos"] == "dst"
        ids = ids.mask(is_dst & ids.isna(), keys["team"].map(by_team).astype("string"))

    for combination in _MATCH_ORDER:
        missing = ids.isna()
        if not missing.any():
            break
        if not all(column in keys for column in combination):
            continue

        reference_keys = _join(reference, combination)
        unambiguous = ~reference_keys.duplicated(keep=False)
        lookup = pd.Series(
            reference.loc[unambiguous, "id"].to_numpy(),
            index=reference_keys[unambiguous].to_numpy(),
        )
        wanted = _join(keys, combination)[missing]
        ids.loc[missing] = wanted.map(lookup).astype("string")

    return ids


def _join(columns, names) -> pd.Series:
    """Concatenate several key columns into one lookup key."""
    pieces = [pd.Series(columns[name]).fillna("~").reset_index(drop=True) for name in names]
    joined = pieces[0].astype(str)
    for piece in pieces[1:]:
        joined = joined + "|" + piece.astype(str)
    return joined


def _common_length(columns, source_ids) -> int:
    lengths = [len(pd.Series(value)) for value in columns.values() if not _is_scalar(value)]
    if source_ids is not None and not _is_scalar(source_ids):
        lengths.append(len(pd.Series(source_ids)))
    return max(lengths) if lengths else 1


def _is_scalar(value) -> bool:
    return isinstance(value, str) or not hasattr(value, "__len__")


def _recycle(value, length: int):
    if _is_scalar(value):
        return pd.Series([value] * length)
    series = pd.Series(value).reset_index(drop=True)
    if len(series) == 1 and length > 1:
        return pd.Series([series.iloc[0]] * length)
    return series
