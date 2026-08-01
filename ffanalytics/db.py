"""Writing a run out to SQLite.

A run produces more than a ranked list: the projections themselves (one row
per player per averaging method), what every site said before they were
combined, the league's scoring and slots, who holds whom, and Sleeper's
player crosswalk.  All of it goes into one file so the numbers can be queried
directly.

    python -m ffanalytics --league 1234567890 --db draft.sqlite

    sqlite3 draft.sqlite \\
      "select player, pos, points, points_vor from projections
       where avg_type = 'weighted' and rostered_by is null
       order by points_vor desc limit 20"

The file holds the *current* picture, not a history: every write replaces what
was there.  During a draft, :func:`refresh_picks` re-fetches only the picks --
rewriting ``ownership`` and the meta row's ``written_at`` while leaving the
projections untouched.
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from .sleeper import draft_picks, draft_state, fetch_league, sleeper_player_map

if TYPE_CHECKING:  # pragma: no cover - import cycle at runtime
    from .league import LeagueProjections
    from .sleeper import SleeperLeague

__all__ = ["write_sqlite", "refresh_picks", "TABLES"]

#: Every table a full run produces.  All are replaced on each run.
TABLES = ("projections", "source_projections", "scoring", "slots", "ownership",
          "draft", "players", "meta")

#: Indexes to (re)create after the tables are written.  Replacing a table drops
#: its indexes with it, so these are rebuilt every time.
_INDEXES = (
    ("projections", "projections_rank", "(avg_type, rank)"),
    ("source_projections", "source_src", "(data_src)"),
    ("source_projections", "source_id", "(id)"),
    ("ownership", "ownership_sleeper", "(sleeper_id)"),
)


#: Tables earlier versions wrote.  Dropped on write so an existing file does
#: not keep a schema this version no longer produces.
_LEGACY_TABLES = ("runs", "run", "league", "rosters", "unscored_settings")

_OWNERSHIP_COLUMNS = ["sleeper_id", "id", "player", "team_name", "manager",
                      "draft_round", "draft_pick", "is_starter"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _draft_table(league: "SleeperLeague") -> pd.DataFrame:
    """The draft's slot order with manager names attached, one row per slot.

    Fetched fresh each call so the order appears the moment Sleeper assigns
    it.  A league with no draft yet yields an empty (but typed) frame.
    """
    try:
        state = draft_state(league.league_id)
    except Exception as error:  # noqa: BLE001 - a draft may not exist yet
        print(f"  (no draft order: {type(error).__name__}: {error})")
        return pd.DataFrame()
    managers = league.managers
    if len(state) and managers is not None and len(managers):
        state = state.merge(
            managers[["owner_id", "manager", "team_name"]],
            left_on="user_id", right_on="owner_id", how="left",
        ).drop(columns=["owner_id"])
    return state


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


def _scoring_table(result: "LeagueProjections") -> pd.DataFrame:
    """One row per scoring rule: global as 'ALL', overlays per position, and
    the league's unprojectable settings with ``projected`` 0."""
    rows = [
        {"position": "ALL", "stat": stat, "points": value, "projected": 1}
        for stat, value in sorted(result.scoring.stats.items())
    ]
    rows += [
        {"position": position, "stat": stat, "points": value, "projected": 1}
        for position, overlay in sorted(result.scoring.by_pos.items())
        for stat, value in sorted(overlay.items())
    ]
    rows += [
        {"position": "ALL", "stat": stat, "points": value, "projected": 0}
        for stat, value in sorted(result.unscored_settings.items())
    ]
    return pd.DataFrame(rows, columns=["position", "stat", "points", "projected"])


def _slots_table(result: "LeagueProjections") -> pd.DataFrame:
    """The league's starting slots (counted) and the replacement ranks."""
    rows = [
        {"kind": "slot", "name": name, "count": count}
        for name, count in Counter(result.league.starting_slots).items()
    ]
    rows += [
        {"kind": "replacement", "name": position, "count": rank}
        for position, rank in sorted(result.replacement_ranks.items())
    ]
    return pd.DataFrame(rows, columns=["kind", "name", "count"])


def _ownership(league: "SleeperLeague", picks: pd.DataFrame | None,
               player_map: pd.DataFrame | None) -> pd.DataFrame:
    """Who holds whom: from the draft when one has happened, else the rosters.

    Pre-draft Sleeper returns rosters with ``players: null`` -- that means
    nothing is drafted yet, so the result is a table with columns and no rows,
    never an error.
    """
    rosters = league.rosters
    if picks is not None and len(picks):
        latest = picks.sort_values("season").drop_duplicates("sleeper_id",
                                                             keep="last")
        frame = latest[["sleeper_id", "draft_round", "draft_pick",
                        "drafted_by"]].copy()
        if len(league.managers):
            frame = frame.merge(
                league.managers.rename(columns={"owner_id": "drafted_by"}),
                on="drafted_by", how="left")
        frame = frame.drop(columns="drafted_by")
        if len(rosters):
            frame = frame.merge(
                rosters.drop_duplicates("sleeper_id")[["sleeper_id", "is_starter"]],
                on="sleeper_id", how="left")
    elif len(rosters):
        frame = rosters.drop_duplicates("sleeper_id")[
            [c for c in ("sleeper_id", "team_name", "manager", "is_starter")
             if c in rosters.columns]].copy()
    else:
        return pd.DataFrame(columns=_OWNERSHIP_COLUMNS)

    frame["sleeper_id"] = frame["sleeper_id"].astype(str)
    if player_map is not None and len(player_map):
        names = player_map.drop_duplicates("sleeper_id")[
            ["sleeper_id", "id", "sleeper_name"]]
        names = names.assign(sleeper_id=names["sleeper_id"].astype(str))
        frame = frame.merge(names.rename(columns={"sleeper_name": "player"}),
                            on="sleeper_id", how="left")
    return frame.reindex(columns=_OWNERSHIP_COLUMNS)


def write_sqlite(result: "LeagueProjections", path: str | Path) -> dict[str, int]:
    """Write a run to ``path`` and return the row count per table.

    Replaces the file's contents: the database always describes the latest
    scrape, never an accumulation of past ones.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    league = result.league
    scrape = result.scrape

    projections = result.table.drop(columns=["draft_round", "draft_pick"],
                                    errors="ignore")

    if scrape is not None and len(scrape):
        per_source = pd.concat(
            [frame.assign(pos=position) for position, frame in scrape.items()],
            ignore_index=True,
        )
    else:
        per_source = None

    player_map = result.player_map
    fetched_at = result.player_map_fetched_at
    if player_map is None:
        player_map = sleeper_player_map()
        fetched_at = _now()
    players = player_map.assign(fetched_at=fetched_at)

    try:
        picks = draft_picks(league.league_id)
    except Exception as error:  # noqa: BLE001 - a draft may not exist yet
        print(f"  (no draft data: {type(error).__name__}: {error})")
        picks = pd.DataFrame()

    projected = len(projections)
    if projected and "avg_type" in projections.columns:
        projected = int(projections["avg_type"].value_counts().max())

    written: dict[str, int] = {}
    with sqlite3.connect(path) as connection:
        for stale in _LEGACY_TABLES:
            connection.execute(f"DROP TABLE IF EXISTS {stale}")

        written["projections"] = _replace(connection, "projections", projections)
        written["source_projections"] = _replace(
            connection, "source_projections", per_source
        )
        written["scoring"] = _replace(connection, "scoring",
                                      _scoring_table(result))
        written["slots"] = _replace(connection, "slots", _slots_table(result))
        written["players"] = _replace(connection, "players", players)
        written["ownership"] = _replace(connection, "ownership",
                                        _ownership(league, picks, player_map))
        written["draft"] = _replace(connection, "draft", _draft_table(league))

        # written_at marks when the ownership picture was last completed.
        meta = pd.DataFrame([{
            "league_id": league.league_id,
            "name": league.name,
            "season": league.season,
            "teams": league.teams,
            "superflex": int(league.is_superflex),
            "week": scrape.week if scrape is not None else None,
            "sources": ", ".join(scrape.sources()) if scrape is not None else None,
            "players_projected": projected,
            "written_at": _now(),
        }])
        written["meta"] = _replace(connection, "meta", meta)

        for table, index, columns in _INDEXES:
            if written.get(table):
                connection.execute(f"CREATE INDEX {index} ON {table} {columns}")

    return written


def refresh_picks(path: str | Path, league_id: str | int,
                  with_draft: bool = True) -> set[str]:
    """Re-fetch the draft into an existing database -- the fast loop.

    Rewrites only ``ownership``, the ``draft`` order table, and the meta
    row's ``written_at``; the projections are untouched.  Returns the
    sleeper_ids newly held since the previous refresh -- empty (but still
    stamped) before the draft starts.

    ``with_draft=False`` skips the draft-order table, which changes rarely --
    the serve loop passes it on most iterations so the order is polled on a
    slower cadence than the picks.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"No database at {path} -- write one with a full run first"
        )

    league = fetch_league(league_id)
    picks = draft_picks(league_id)

    with sqlite3.connect(path) as connection:
        try:
            player_map = pd.read_sql_query(
                "SELECT sleeper_id, id, sleeper_name FROM players", connection
            )
        except (pd.errors.DatabaseError, sqlite3.OperationalError):
            player_map = None
        try:
            before = {str(row[0]) for row in
                      connection.execute("SELECT sleeper_id FROM ownership")}
        except sqlite3.OperationalError:
            before = set()

        ownership = _ownership(league, picks, player_map)
        if _replace(connection, "ownership", ownership):
            connection.execute(
                "CREATE INDEX ownership_sleeper ON ownership (sleeper_id)"
            )
        if with_draft:
            _replace(connection, "draft", _draft_table(league))
        connection.execute("UPDATE meta SET written_at = ?", (_now(),))

    return set(ownership["sleeper_id"].dropna().astype(str)) - before
