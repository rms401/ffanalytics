"""The projection sites, one module each, and what each of them covers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .cbs import scrape_cbs
from .espn import scrape_espn
from .fanduel import scrape_fanduel
from .fantasypros import scrape_fantasypros
from .fantasysharks import scrape_fantasysharks
from .fftoday import scrape_fftoday
from .fleaflicker import scrape_fleaflicker
from .nfl import scrape_nfl
from .rtsports import scrape_rtsports
from .walterfootball import scrape_walterfootball

__all__ = ["Source", "SOURCES", "DEFAULT_SOURCES", "source_names"]


@dataclass(frozen=True)
class Source:
    """One projection site: what it covers and how much to trust it."""

    name: str
    scrape: Callable
    positions: tuple[str, ...]
    draft: bool
    weekly: bool
    weight: float
    note: str = ""

    def covers(self, week: int) -> bool:
        return self.draft if week == 0 else self.weekly


_OFFENSE = ("QB", "RB", "WR", "TE", "K", "DST")
_WITH_IDP = _OFFENSE + ("DL", "LB", "DB")

# Weights are the ffanalytics project's published per-source accuracy weights.
# A weight of zero keeps a source out of the "weighted" average without
# removing it from the plain and robust averages.
SOURCES: dict[str, Source] = {
    "CBS": Source("CBS", scrape_cbs, _OFFENSE, True, True, 0.145),
    "ESPN": Source("ESPN", scrape_espn, _WITH_IDP, True, True, 0.157),
    "FFToday": Source("FFToday", scrape_fftoday, _WITH_IDP, True, True, 0.151),
    "FanDuel": Source("FanDuel", scrape_fanduel, _OFFENSE, True, True, 0.142),
    "FantasySharks": Source(
        "FantasySharks", scrape_fantasysharks, _WITH_IDP, True, True, 0.142,
        "blocks datacenter IP ranges; may return nothing from a server",
    ),
    "NFL": Source("NFL", scrape_nfl, _OFFENSE, True, True, 0.140),
    "RTSports": Source("RTSports", scrape_rtsports, _OFFENSE, True, False, 0.123),
    "WalterFootball": Source(
        "WalterFootball", scrape_walterfootball, ("QB", "RB", "WR", "TE", "K"),
        True, False, 0.130,
    ),
    "FleaFlicker": Source(
        "FleaFlicker", scrape_fleaflicker, _WITH_IDP, False, True, 0.0,
        "weekly only; no published accuracy weight, so it sits out the "
        "weighted average",
    ),
    "FantasyPros": Source(
        "FantasyPros", scrape_fantasypros, _OFFENSE, True, True, 0.0,
        "a consensus of the other sites, and its public table is capped at ten "
        "rows a position; not scraped by default",
    ),
}

#: Scraped unless you ask for something else.  FantasyPros is left out because
#: it aggregates the others.
DEFAULT_SOURCES: tuple[str, ...] = tuple(
    name for name, source in SOURCES.items() if name != "FantasyPros"
)


def source_names() -> tuple[str, ...]:
    return tuple(SOURCES)
