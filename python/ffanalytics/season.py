"""What season and week is it right now.

Sleeper publishes the answer at a public, unauthenticated endpoint, so there is
no need to reconstruct it from a kickoff schedule.  ``week`` is 0 in the
off-season and the pre-season, which is exactly the package's convention for
season-long ("draft") projections.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

import requests

__all__ = ["current_season", "current_week", "nfl_state"]

_STATE_URL = "https://api.sleeper.app/v1/state/nfl"


@lru_cache(maxsize=1)
def nfl_state() -> dict:
    """Sleeper's view of the NFL calendar, or ``{}`` if it cannot be reached."""
    try:
        response = requests.get(_STATE_URL, timeout=30)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError):
        return {}


def current_season() -> int:
    """The season projections should be scraped for."""
    season = nfl_state().get("season")
    if season:
        return int(season)
    # January to March still belongs to the season that just finished.
    today = dt.date.today()
    return today.year - 1 if today.month <= 3 else today.year


def current_week() -> int:
    """The current week, or 0 outside the regular season."""
    state = nfl_state()
    if state.get("season_type") == "regular":
        return int(state.get("week") or 0)
    if state:
        return 0
    return 0
