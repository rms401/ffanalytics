"""Access to the R package's internal data (``R/sysdata.rda``).

``R/sysdata.rda`` holds four objects that the projections pipeline needs:

``player_ids``
    Crosswalk from each source's own player id to the canonical MyFantasyLeague
    id.  Used by :func:`ffanalytics.helper_funcs.get_mfl_id`.
``bonus_col_coefs``
    ``(Intercept, slope)`` regression coefficients used to synthesise the
    milestone-bonus columns for sources that do not report them.
``bonus_col_sets``
    Nested-threshold rollups (e.g. ``pass_300_yds`` counts also include the
    350 and 400 yard games).
``pts_bracket_coefs``
    Per-team coefficients for the DST points-allowed standard deviation model.

They are read straight out of the R file rather than transcribed, because
``bonus_col_coefs`` and ``pts_bracket_coefs`` come from models fitted in
``data-raw/`` against ``nflfastR`` play-by-play and cannot be reproduced
without R.  Set ``FFANALYTICS_SYSDATA`` to point somewhere else if the R
sources are not alongside this package.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import pandas as pd

from .rdata import read_rdata

__all__ = [
    "sysdata_path",
    "player_ids",
    "bonus_col_coefs",
    "bonus_col_sets",
    "pts_bracket_coefs",
]

_DEFAULT_PATH = Path(__file__).resolve().parents[2] / "R" / "sysdata.rda"


def sysdata_path() -> Path:
    """Location of ``sysdata.rda`` (``FFANALYTICS_SYSDATA`` overrides)."""
    override = os.environ.get("FFANALYTICS_SYSDATA")
    return Path(override) if override else _DEFAULT_PATH


@lru_cache(maxsize=1)
def _sysdata() -> dict:
    path = sysdata_path()
    if not path.exists():
        raise FileNotFoundError(
            f"Could not find the R package's internal data at {path}. "
            "Set FFANALYTICS_SYSDATA to the location of sysdata.rda."
        )
    return read_rdata(path)


def _as_frame(obj: dict) -> pd.DataFrame:
    return pd.DataFrame(obj["columns"], columns=obj["names"])


@lru_cache(maxsize=1)
def player_ids() -> pd.DataFrame:
    """Source-id -> MFL-id crosswalk (5681 rows x 15 id columns)."""
    return _as_frame(_sysdata()["player_ids"])


@lru_cache(maxsize=1)
def pts_bracket_coefs() -> pd.DataFrame:
    """Per-team intercept/slope for the DST points-allowed SD model."""
    return _as_frame(_sysdata()["pts_bracket_coefs"])


@lru_cache(maxsize=1)
def bonus_col_coefs() -> dict[str, tuple[float, str, float]]:
    """``{bonus column: (intercept, reference column, slope)}``.

    R stores each element as a two-element named numeric vector whose second
    name is the yardage column to regress on -- see ``R/impute_funcs.R:189``,
    which evaluates ``col_coef[1] + df[[names(col_coef)[2]]] * col_coef[2]``.
    """
    out: dict[str, tuple[float, str, float]] = {}
    for name, coefs in _sysdata()["bonus_col_coefs"]:
        (_, intercept), (ref_col, slope) = coefs
        out[name] = (intercept, ref_col, slope)
    return out


@lru_cache(maxsize=1)
def bonus_col_sets() -> dict[str, list[str]]:
    """``{column: columns to sum into it}`` for the nested yardage thresholds."""
    return {name: list(cols) for name, cols in _sysdata()["bonus_col_sets"]}
