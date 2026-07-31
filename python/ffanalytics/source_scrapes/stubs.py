"""Sources the R package declares but does not scrape.

Kept so the dispatcher's source list matches R's, and so asking for one of
these explains itself rather than failing (``R/source_scrapes.R:1427-1452``
and ``:1735-1740``).
"""

from __future__ import annotations

from ..results import ScrapeResult

__all__ = [
    "scrape_fantasyfootballnerd",
    "scrape_fantasydata",
    "scrape_yahoo",
]

DRAFT = True
WEEKLY = True
POSITIONS = ()


def scrape_fantasyfootballnerd(pos=None, season=None, week=None, **kwargs) -> ScrapeResult:
    """Not implemented, as in the R package."""
    print("\nThe FantasyFootballNerd scrape is not implemeted yet--we are working on it")
    return ScrapeResult({}, season=season or 0, week=week or 0)


def scrape_fantasydata(pos=None, season=None, week=None, **kwargs) -> ScrapeResult:
    """Not available -- FantasyData projections are behind a paywall."""
    print("\nThe FantasyData projections are behind a paywall")
    return ScrapeResult({}, season=season or 0, week=week or 0)


def scrape_yahoo(pos=None, season=None, week=None, **kwargs) -> ScrapeResult:
    """No longer supported -- Yahoo publishes FantasyPros projections."""
    print(
        "\nThe Yahoo scrape is no longer supported because they now use "
        "FantasyPros projections"
    )
    return ScrapeResult({}, season=season or 0, week=week or 0)
