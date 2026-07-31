"""Scraping every site and stacking the results by position."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Sequence

import pandas as pd

from . import cache
from .season import current_season, current_week
from .sources import DEFAULT_SOURCES, SOURCES

__all__ = ["Scrape", "scrape_data", "POSITIONS"]

#: Every position that can be scraped.
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")


@dataclass
class Scrape:
    """Projections from several sites: one frame per position.

    Each frame holds one row per player *per source*, identified by ``id``
    (the MyFantasyLeague player id) and ``data_src``.
    """

    frames: dict[str, pd.DataFrame] = field(default_factory=dict)
    season: int = 0
    week: int = 0

    def __getitem__(self, position: str) -> pd.DataFrame:
        return self.frames[position]

    def __contains__(self, position: object) -> bool:
        return position in self.frames

    def __iter__(self):
        return iter(self.frames)

    def __len__(self) -> int:
        return len(self.frames)

    def items(self):
        return self.frames.items()

    def keys(self):
        return self.frames.keys()

    def sources(self) -> list[str]:
        """Every distinct ``data_src`` present, in first-seen order."""
        found: list[str] = []
        for frame in self.frames.values():
            for source in frame.get("data_src", pd.Series(dtype=str)).dropna().unique():
                if source not in found:
                    found.append(source)
        return found

    def summary(self) -> pd.DataFrame:
        """Rows scraped per source per position."""
        rows = [
            {"pos": position, "data_src": source, "players": int(count)}
            for position, frame in self.frames.items()
            for source, count in frame["data_src"].value_counts().items()
        ]
        frame = pd.DataFrame(rows, columns=["pos", "data_src", "players"])
        return frame.sort_values(["pos", "data_src"]).reset_index(drop=True)

    def __repr__(self) -> str:
        shapes = ", ".join(f"{k}: {len(v)}" for k, v in self.frames.items())
        return f"<Scrape season={self.season} week={self.week} rows by pos [{shapes}]>"


def scrape_data(
    sources: Sequence[str] = DEFAULT_SOURCES,
    positions: Sequence[str] = ("QB", "RB", "WR", "TE", "K", "DST"),
    season: int | None = None,
    week: int | None = None,
    cache_ttl: float = cache.DEFAULT_TTL,
    **kwargs,
) -> Scrape:
    """Scrape projections and combine them into one frame per position.

    ``week=0`` means season-long ("draft") projections; any other week means
    that week.  A site that is down, blocked, or has not published yet is
    reported and skipped -- you get everything the rest of them had.

    Results are cached per site for ``cache_ttl`` seconds; pass ``0`` to force
    a fresh scrape.
    """
    season = current_season() if season is None else int(season)
    week = current_week() if week is None else int(week)

    chosen = _match(sources, SOURCES, "source")
    wanted = _match(positions, POSITIONS, "position")

    print(f"Scraping {season} " + ("season-long" if week == 0 else f"week {week}")
          + f" projections from {len(chosen)} sources")

    combined: dict[str, list[pd.DataFrame]] = {}
    for name in chosen:
        source = SOURCES[name]

        if not source.covers(week):
            period = "season-long" if week == 0 else "weekly"
            print(f"\n{name}: no {period} projections available")
            continue

        supported = [p for p in wanted if p in source.positions]
        if not supported:
            continue

        key = f"scrape_{name.lower()}_{season}_{week}_{'-'.join(supported)}"
        frames = cache.load(key, cache_ttl)
        if frames is None:
            print(f"\n{name}:")
            try:
                frames = source.scrape(
                    positions=supported, season=season, week=week, **kwargs
                )
            except Exception as error:  # noqa: BLE001 - one dead site must not stop the rest
                print(f"  ! {name} failed: {type(error).__name__}: {error}")
                if source.note:
                    print(f"    ({source.note})")
                continue
            if frames:
                cache.save(key, frames)
        else:
            print(f"\n{name}: using cached scrape")

        if not frames:
            print(f"  {name} returned nothing")
            continue

        for position, frame in frames.items():
            if frame is not None and len(frame):
                combined.setdefault(position, []).append(_drop_empty_columns(frame))

    stacked = {
        position: _one_row_per_source(pd.concat(frames, ignore_index=True))
        for position, frames in combined.items()
    }
    ordered = {p: stacked[p] for p in wanted if p in stacked}
    return Scrape(frames=ordered, season=season, week=week)


def _one_row_per_source(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep a source's first row per player.

    A paginated site can serve the same player twice, and two rows would give
    that source double the say in the average.
    """
    if not {"id", "data_src"} <= set(frame.columns):
        return frame
    known = frame["id"].notna()
    deduplicated = frame[known].drop_duplicates(subset=["id", "data_src"], keep="first")
    return pd.concat([deduplicated, frame[~known]], ignore_index=True)


def _drop_empty_columns(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[[column for column in frame.columns if frame[column].notna().any()]]


def _match(values, choices: Iterable[str], what: str) -> list[str]:
    """Case-insensitive name matching that keeps the caller's order."""
    if isinstance(values, str):
        values = [values]
    lookup = {choice.lower(): choice for choice in choices}
    out: list[str] = []
    for value in values:
        matched = lookup.get(str(value).lower())
        if matched is None:
            raise ValueError(
                f"{value!r} is not a known {what}. Choose from: "
                + ", ".join(lookup.values())
            )
        if matched not in out:
            out.append(matched)
    return out
