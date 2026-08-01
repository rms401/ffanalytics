"""Serving the draft board locally.

The heavy lifting happened before draft night: a full run wrote the ranked
projections, the league's slots and scoring, and Sleeper's player crosswalk to
one SQLite file.  This server adds only the live part -- a background thread
looping :func:`ffanalytics.db.refresh_picks` so the ``ownership`` table tracks
the draft, and two read-only endpoints the page polls:

* ``GET /api/state`` -- the board (weighted projections overlaid with who has
  been taken), the picks so far, the league's slots, and freshness stamps.
* ``GET /api/player/<id>`` -- everything known about one player: the combined
  projection under each averaging method and what every site said raw.

Everything is read from the database on each request, so edits to the file
from outside (a manual ``--refresh-picks``, a re-scrape) show up on the next
poll without restarting the server.

FastAPI and uvicorn are the only extra dependencies, installed with
``pip install "ffanalytics[web]"``.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from ..db import refresh_picks
from ..sleeper import STARTING_SLOTS, _fetch_managers

__all__ = ["create_app", "serve"]

_STATIC = Path(__file__).parent / "static"

#: Board columns sent to the page, in display order.  Columns a run did not
#: produce (``--no-adp``, ``--no-ecr``) are simply absent from the rows.
_BOARD_COLUMNS = (
    "rank", "pos_rank", "tier", "player", "pos", "team", "points",
    "points_vor", "sd_pts", "floor", "ceiling", "dropoff", "uncertainty",
    "pos_ecr", "adp", "adp_diff", "injury_status", "sources", "id",
    "sleeper_id",
)

#: Identity columns of ``source_projections`` -- everything else is a stat.
_SOURCE_ID_COLUMNS = frozenset(
    {"id", "data_src", "player", "pos", "team", "season", "week",
     "src_id", "site_pts"}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class _RefreshLoop:
    """Re-fetch the draft into the database every ``poll_seconds``.

    Failures are held, not raised: Sleeper flaking mid-draft should leave a
    stale-but-working board, and the page shows the error and the time of the
    last good refresh instead of dying.
    """

    def __init__(self, db_path: Path, league_id: str | int,
                 poll_seconds: float):
        self.db_path = db_path
        self.league_id = league_id
        self.poll_seconds = poll_seconds
        self.last_attempt: str | None = None
        self.last_success: str | None = None
        self.error: str | None = None
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ffanalytics-refresh")

    def start(self) -> None:
        if self.league_id:
            self._thread.start()

    def _run(self) -> None:
        while True:
            self.last_attempt = _now()
            try:
                refresh_picks(self.db_path, self.league_id)
            except Exception as error:  # noqa: BLE001 - shown, not fatal
                self.error = f"{type(error).__name__}: {error}"
            else:
                self.last_success = _now()
                self.error = None
            time.sleep(self.poll_seconds)

    def status(self) -> dict:
        return {
            "polling": self._thread.is_alive(),
            "poll_seconds": self.poll_seconds,
            "last_attempt": self.last_attempt,
            "last_success": self.last_success,
            "error": self.error,
        }


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    return connection


def _rows(connection: sqlite3.Connection, query: str,
          params: tuple = ()) -> list[dict]:
    try:
        return [dict(row) for row in connection.execute(query, params)]
    except sqlite3.OperationalError:
        return []


def _avg_type(connection: sqlite3.Connection) -> str | None:
    kinds = [row["avg_type"] for row in
             _rows(connection, "SELECT DISTINCT avg_type FROM projections")]
    if not kinds:
        return None
    return "weighted" if "weighted" in kinds else kinds[0]


def _board(connection: sqlite3.Connection, avg_type: str | None) -> list[dict]:
    if avg_type:
        rows = _rows(connection,
                     "SELECT * FROM projections WHERE avg_type = ? "
                     "ORDER BY rank", (avg_type,))
    else:
        rows = _rows(connection, "SELECT * FROM projections ORDER BY rank")

    ownership = {
        str(row["sleeper_id"]): row
        for row in _rows(connection, "SELECT * FROM ownership")
        if row.get("sleeper_id") is not None
    }

    board = []
    for row in rows:
        entry = {key: row[key] for key in _BOARD_COLUMNS if key in row}
        held = ownership.get(str(row.get("sleeper_id")))
        if held is not None:
            entry["drafted_by"] = held.get("team_name") or held.get("manager")
            entry["drafted_manager"] = held.get("manager")
            entry["draft_round"] = held.get("draft_round")
            entry["draft_pick"] = held.get("draft_pick")
        board.append(entry)
    return board


def _picks(connection: sqlite3.Connection) -> list[dict]:
    return _rows(connection,
                 "SELECT sleeper_id, id, player, team_name, manager, "
                 "draft_round, draft_pick FROM ownership "
                 "WHERE draft_pick IS NOT NULL ORDER BY draft_pick")


def _managers(connection: sqlite3.Connection) -> list[dict]:
    return _rows(connection,
                 "SELECT DISTINCT team_name, manager FROM ownership "
                 "WHERE team_name IS NOT NULL OR manager IS NOT NULL "
                 "ORDER BY manager, team_name")


def create_app(db_path: str | Path, league_id: str | int | None = None,
               poll_seconds: float = 5.0):
    """Build the FastAPI app over ``db_path``.

    ``league_id`` turns on the background pick-refresh loop; without it the
    page still works but only reflects the database as-is.
    """
    from fastapi import FastAPI, HTTPException
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    db_path = Path(db_path)
    refresher = _RefreshLoop(db_path, league_id, poll_seconds)

    # Before the first pick the ownership table is empty, but you still need
    # to mark which team is yours -- so the league's member list is fetched
    # from Sleeper once (and only once; a failure just leaves the dropdown
    # to fill as picks arrive).
    members: dict = {"tried": False, "rows": []}
    members_lock = threading.Lock()

    def league_members() -> list[dict]:
        if not league_id:
            return []
        with members_lock:
            if not members["tried"]:
                members["tried"] = True
                try:
                    frame = _fetch_managers(str(league_id))
                    members["rows"] = [
                        {"team_name": row.get("team_name"),
                         "manager": row.get("manager")}
                        for row in frame.to_dict("records")
                    ]
                except Exception:  # noqa: BLE001 - optional nicety only
                    pass
        return members["rows"]

    app = FastAPI(title="ffanalytics draft board", docs_url=None,
                  redoc_url=None)
    app.state.refresher = refresher

    @app.on_event("startup")
    def _start_refresh() -> None:
        refresher.start()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/state")
    def state() -> dict:
        with _connect(db_path) as connection:
            avg_type = _avg_type(connection)
            meta = _rows(connection, "SELECT * FROM meta LIMIT 1")
            managers = _managers(connection)
            seen = {row.get("manager") or row.get("team_name")
                    for row in managers}
            managers += [row for row in league_members()
                         if (row.get("manager") or row.get("team_name"))
                         not in seen]
            return {
                "meta": meta[0] if meta else {},
                "avg_type": avg_type,
                "board": _board(connection, avg_type),
                "picks": _picks(connection),
                "managers": managers,
                "slots": _rows(connection,
                               "SELECT kind, name, count FROM slots"),
                "slot_positions": {slot: list(positions)
                                   for slot, positions
                                   in STARTING_SLOTS.items()},
                "unprojected": _rows(connection,
                                     "SELECT stat, points FROM scoring "
                                     "WHERE projected = 0"),
                "refresh": refresher.status(),
            }

    @app.get("/api/player/{player_id}")
    def player(player_id: str) -> dict:
        with _connect(db_path) as connection:
            combined = _rows(connection,
                             "SELECT * FROM projections WHERE id = ?",
                             (player_id,))
            if not combined:
                raise HTTPException(404, f"No projection for id {player_id}")
            sources = _rows(connection,
                            "SELECT * FROM source_projections WHERE id = ?",
                            (player_id,))

        # A source row carries every stat column any position uses; keep only
        # the stats at least one site actually projected for this player.
        live = [key for key in (sources[0] if sources else {})
                if key not in _SOURCE_ID_COLUMNS
                and any(row.get(key) is not None for row in sources)]
        return {
            "avg_types": combined,
            "sources": [
                {"data_src": row.get("data_src"),
                 "stats": {key: row.get(key) for key in live}}
                for row in sources
            ],
        }

    app.mount("/static", StaticFiles(directory=_STATIC), name="static")
    return app


def serve(db_path: str | Path, league_id: str | int | None,
          port: int = 8000, poll_seconds: float = 5.0) -> None:
    """Run the board at ``http://127.0.0.1:<port>`` until interrupted."""
    import uvicorn

    app = create_app(db_path, league_id, poll_seconds=poll_seconds)
    print(f"Draft board at http://127.0.0.1:{port}  (Ctrl-C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
