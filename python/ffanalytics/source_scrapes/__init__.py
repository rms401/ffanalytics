"""Per-source scrapers, one module each.

``R/source_scrapes.R`` keeps all of these in a single 1700-line file and
dispatches by building the function name from the source name.  Here each
source is its own module and the registry below records what each one supports,
which is what R reads out of the scraper's ``draft``/``weekly`` formals.
"""

from __future__ import annotations

from typing import Callable, NamedTuple

from .cbs import scrape_cbs
from .espn import scrape_espn
from .fanduel import scrape_fanduel
from .fantasypros import scrape_fantasypros
from .fantasysharks import scrape_fantasysharks
from .fftoday import scrape_fftoday
from .fleaflicker import scrape_fleaflicker
from .nfl import scrape_nfl
from .numberfire import scrape_numberfire
from .rtsports import scrape_rtsports
from .stubs import scrape_fantasydata, scrape_fantasyfootballnerd, scrape_yahoo
from .walterfootball import scrape_walterfootball

__all__ = [
    "SOURCES",
    "Source",
    "scrape_cbs",
    "scrape_espn",
    "scrape_fanduel",
    "scrape_fantasydata",
    "scrape_fantasyfootballnerd",
    "scrape_fantasypros",
    "scrape_fantasysharks",
    "scrape_fftoday",
    "scrape_fleaflicker",
    "scrape_nfl",
    "scrape_numberfire",
    "scrape_rtsports",
    "scrape_walterfootball",
    "scrape_yahoo",
]


class Source(NamedTuple):
    """What one projection source supports."""

    scrape: Callable
    positions: tuple[str, ...]
    draft: bool
    weekly: bool


_ALL = ("QB", "RB", "WR", "TE", "K", "DST")
_WITH_IDP = _ALL + ("DL", "LB", "DB")

#: Source name -> capabilities.  Names match the R package's ``src`` argument.
SOURCES: dict[str, Source] = {
    "CBS": Source(scrape_cbs, _ALL, draft=True, weekly=True),
    "ESPN": Source(scrape_espn, _WITH_IDP, draft=True, weekly=True),
    "FantasyData": Source(scrape_fantasydata, (), draft=True, weekly=True),
    "FantasyPros": Source(scrape_fantasypros, _ALL, draft=True, weekly=True),
    "FantasySharks": Source(scrape_fantasysharks, _WITH_IDP, draft=True, weekly=True),
    "FFToday": Source(scrape_fftoday, _WITH_IDP, draft=True, weekly=True),
    "FleaFlicker": Source(scrape_fleaflicker, _WITH_IDP, draft=False, weekly=True),
    "NumberFire": Source(
        scrape_numberfire, _ALL + ("LB", "DB", "DL"), draft=True, weekly=True
    ),
    "Yahoo": Source(scrape_yahoo, (), draft=True, weekly=True),
    "FantasyFootballNerd": Source(scrape_fantasyfootballnerd, (), draft=True, weekly=True),
    "NFL": Source(scrape_nfl, _ALL, draft=True, weekly=True),
    "RTSports": Source(scrape_rtsports, _ALL, draft=True, weekly=False),
    "Walterfootball": Source(
        scrape_walterfootball, ("QB", "RB", "WR", "TE", "K"), draft=True, weekly=False
    ),
    "FanDuel": Source(scrape_fanduel, _ALL, draft=True, weekly=True),
}
