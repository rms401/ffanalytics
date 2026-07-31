"""Writing a run out to SQLite.

A run produces more than a ranked list: the projections themselves, what every
site said before they were combined, the league they were scored for, who holds
whom, and which of the league's scoring settings nothing could be projected
for.  All of it goes into one file so the numbers can be queried directly.

    python -m ffanalytics --league 1234567890 --db draft.sqlite

    sqlite3 draft.sqlite \\
      "select player, pos, points, points_vor from projections
       where rostered_by is null order by points_vor desc limit 20"

The file holds the *current* picture, not a history: every write replaces what
was there, so the database always reflects the most recent scrape.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from .league import LeagueProjections

__all__ = ["write_sqlite", "TABLES"]

#: Every table a write produces.  All are replaced on each run.
TABLES = ("run", "projections", "source_projections", "league", "rosters",
          "unscored_settings")

#: Indexes to (re)create after the tables are written.  Replacing a table drops
#: its indexes with it, so these are rebuilt every time.
_INDEXES = (
    ("projections", "projections_rank", "(rank)"),
    ("projections", "projections_pos", "(pos, pos_rank)"),
    ("source_projections", "source_src", "(data_src)"),
    ("source_projections", "source_id", "(id)"),
    ("rosters", "rosters_owner", "(owner_id)"),
)


#: Tables earlier versions wrote.  Dropped on write so an existing file does
#: not keep a schema this version no longer produces.
_LEGACY_TABLES = ("runs",)


def _sqlite_safe(frame: pd.DataFrame) -> pd.DataFrame:
    """Coerce to something sqlite3 will accept as parameters."""
    out = frame.copy()
    out.columns = [str(column) for column in out.columns]
    for column in out.columns:
        values = out[column]
        if pd.api.types.is_bool_dtype(values):
            out[column] = values.astype("Int64")
        elif not (pd.api.types.is_numeric_dtype(values)
                  or pd.api.types.is_datetime64_any_dtype(values)):
            out[column] = values.astype(object).where(values.notna(), None)
    return out.astype(object).where(pd.notna(out), None)


def _replace(connection: sqlite3.Connection, table: str,
             frame: pd.DataFrame | None) -> int:
    """Write ``frame`` as the whole of ``table``, discarding what was there.

    A frame with columns but no rows still creates the table, so querying it
    before a draft has happened returns nothing rather than failing.
    """
    connection.execute(f"DROP TABLE IF EXISTS {table}")
    if frame is None or not len(frame.columns):
        return 0
    _sqlite_safe(frame).to_sql(table, connection, if_exists="replace", index=False)
    return len(frame)


def write_sqlite(result: "LeagueProjections", path: str | Path,
                 avg_type: str | None = None) -> dict[str, int]:
    """Write a run to ``path`` and return the row count per table.

    Replaces the file's contents: the database always describes the latest
    scrape, never an accumulation of past ones.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc)
    league = result.league
    scrape = result.scrape

    if scrape is not None and len(scrape):
        per_source = pd.concat(
            [frame.assign(pos=position) for position, frame in scrape.items()],
            ignore_index=True,
        )
    else:
        per_source = None

    run = pd.DataFrame([{
        "written_at": stamp.isoformat(timespec="seconds"),
        "league_id": league.league_id,
        "league_name": league.name,
        "season": league.season,
        "week": scrape.week if scrape is not None else None,
        "avg_type": avg_type,
        "sources": ", ".join(scrape.sources()) if scrape is not None else None,
        "players": len(result.table),
    }])

    league_row = pd.DataFrame([{
        "league_id": league.league_id,
        "name": league.name,
        "season": league.season,
        "teams": league.teams,
        "starting_slots": ", ".join(league.starting_slots),
        "bench": league.bench_size,
        "superflex": int(league.is_superflex),
        "scoring": str(result.scoring),
        "replacement_ranks": ", ".join(
            f"{position}{rank}"
            for position, rank in sorted(result.replacement_ranks.items())
        ),
    }])

    unscored = pd.DataFrame(
        sorted(result.unscored_settings.items()), columns=["setting", "points"]
    )

    written: dict[str, int] = {}
    with sqlite3.connect(path) as connection:
        for stale in _LEGACY_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {stale}")

        written["projections"] = _replace(connection, "projections", result.table)
        written["source_projections"] = _replace(
            connection, "source_projections", per_source
        )
        written["league"] = _replace(connection, "league", league_row)
        written["rosters"] = _replace(connection, "rosters", league.rosters)
        written["unscored_settings"] = _replace(
            connection, "unscored_settings", unscored
        )
        written["run"] = _replace(connection, "run", run)

        for table, index, columns in _INDEXES:
            if written.get(table):
                connection.execute(f"CREATE INDEX {index} ON {table} {columns}")

    return written
