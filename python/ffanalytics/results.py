"""Containers that carry the season/week/league-type context.

R attaches ``season``, ``week`` and ``lg_type`` as attributes and every
``add_*`` function re-attaches them by hand, because dplyr drops attributes on
almost every operation.  ``DataFrame.attrs`` is just as fragile, so the port
makes the contract explicit with two small wrappers instead.
"""

from __future__ import annotations

from typing import Any, Iterator, Mapping

import pandas as pd

__all__ = ["ScrapeResult", "ProjectionsTable"]


class ScrapeResult(Mapping[str, pd.DataFrame]):
    """Scraped projections: one :class:`~pandas.DataFrame` per position.

    Behaves like a dict of position -> tibble, and additionally carries the
    ``season`` and ``week`` the scrape was for.
    """

    def __init__(self, data: Mapping[str, pd.DataFrame], season: int, week: int) -> None:
        self._data: dict[str, pd.DataFrame] = dict(data)
        self.season = season
        self.week = week

    def __getitem__(self, position: str) -> pd.DataFrame:
        return self._data[position]

    def __setitem__(self, position: str, frame: pd.DataFrame) -> None:
        self._data[position] = frame

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        shapes = ", ".join(f"{k}: {v.shape[0]}x{v.shape[1]}" for k, v in self._data.items())
        return f"<ScrapeResult season={self.season} week={self.week} [{shapes}]>"

    def copy_with(self, data: Mapping[str, pd.DataFrame]) -> "ScrapeResult":
        return ScrapeResult(data, season=self.season, week=self.week)

    def sources(self) -> list[str]:
        """Every distinct ``data_src`` across all positions."""
        found: list[str] = []
        for frame in self._data.values():
            if "data_src" in frame.columns:
                for source in frame["data_src"].dropna().unique():
                    if source not in found:
                        found.append(source)
        return found


class ProjectionsTable:
    """The aggregated projections table plus its season/week/league context.

    Attribute access falls through to the underlying frame, so this can be used
    much like a :class:`~pandas.DataFrame`; use :attr:`df` when you need the
    frame itself.
    """

    def __init__(self, df: pd.DataFrame, season: int, week: int,
                 lg_type: Mapping[str, str] | None = None) -> None:
        self.df = df
        self.season = season
        self.week = week
        self.lg_type = dict(lg_type or {})

    def with_df(self, df: pd.DataFrame) -> "ProjectionsTable":
        """A new table carrying the same context."""
        return ProjectionsTable(df, season=self.season, week=self.week, lg_type=self.lg_type)

    def __getattr__(self, name: str) -> Any:
        # only reached when normal attribute lookup fails
        return getattr(object.__getattribute__(self, "df"), name)

    def __getitem__(self, key: Any) -> Any:
        return self.df[key]

    def __len__(self) -> int:
        return len(self.df)

    def __repr__(self) -> str:
        return (
            f"<ProjectionsTable season={self.season} week={self.week} "
            f"rows={len(self.df)}>\n{self.df!r}"
        )

    def _repr_html_(self) -> str:  # pragma: no cover - notebook display
        return self.df._repr_html_()
