"""Working out which NFL week we are in.

Ported from ``R/schedule_data.R``.  R runs these at package load time; here the
schedule is fetched lazily and memoised, so importing does no network I/O.

A week starts the day after the previous week's last game finished; week 1
starts a week before its first kickoff.  Weeks 19-22 (the postseason) are
extrapolated from week 18, exactly as R does.
"""

from __future__ import annotations

import datetime as _dt
from functools import lru_cache

import requests

from .helper_funcs import get_scrape_year

__all__ = ["get_first_last_kickoff", "get_scrape_starts", "get_scrape_week"]

_SCHEDULE_URL = "https://api.myfantasyleague.com/{year}/export?TYPE=nflSchedule&W=ALL&JSON=1"
_USER_AGENT = (
    "ffanalytics R package (https://github.com/FantasyFootballAnalytics/ffanalytics)"
)


@lru_cache(maxsize=4)
def get_first_last_kickoff(year: int | None = None) -> list[tuple[_dt.datetime, _dt.datetime]]:
    """First and last kickoff time for each week of a season."""
    year = year or get_scrape_year()

    response = requests.get(
        _SCHEDULE_URL.format(year=year), headers={"User-Agent": _USER_AGENT}, timeout=60
    )
    response.raise_for_status()
    schedule = response.json()["fullNflSchedule"]["nflSchedule"]

    weeks = []
    for entry in schedule:
        matchups = entry.get("matchup")
        if not matchups:
            continue
        # The Pro Bowl week has a single matchup object rather than a list.
        if isinstance(matchups, dict):
            kickoffs = [int(matchups["kickoff"])]
        else:
            kickoffs = [int(game["kickoff"]) for game in matchups]
        weeks.append(
            (
                _dt.datetime.fromtimestamp(min(kickoffs)),
                _dt.datetime.fromtimestamp(max(kickoffs)),
            )
        )
    return weeks


def get_scrape_starts(first_last_games=None) -> list[_dt.date]:
    """The date each week's scrape window opens."""
    if first_last_games is None:
        first_last_games = get_first_last_kickoff()
    if not first_last_games:
        raise ValueError("No NFL schedule weeks were returned")

    # Each week starts the day after the previous week's last game.
    day_after_last = [last.date() + _dt.timedelta(days=1) for _, last in first_last_games]
    starts = [None] + day_after_last[:-1]
    starts[0] = first_last_games[0][0].date() - _dt.timedelta(days=7)

    # Weeks 19-22 are extrapolated from week 18 (R/schedule_data.R:46).
    if len(starts) >= 18:
        week_18 = starts[17]
        extrapolated = [week_18 + _dt.timedelta(days=days) for days in (7, 14, 21, 35)]
        starts = starts[:18] + extrapolated
    return starts


def get_scrape_week(scrape_start_date=None, today: _dt.date | None = None) -> int:
    """The current week -- 0 before the season, otherwise the week number."""
    if scrape_start_date is None:
        scrape_start_date = get_scrape_starts()
    today = today or _dt.date.today()
    return sum(1 for start in scrape_start_date if start is not None and today >= start)
