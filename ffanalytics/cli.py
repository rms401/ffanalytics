"""Command line entry point: ``python -m ffanalytics``."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from .adp import ADP_SOURCES
from .db import refresh_picks, write_sqlite
from .league import build_league_projections
from .scrape import POSITIONS
from .sleeper import leagues_for_user
from .sources import DEFAULT_SOURCES, SOURCES

__all__ = ["main"]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ffanalytics",
        description=(
            "Scrape fantasy football projections, score them under a Sleeper "
            "league's rules, and rank the result."
        ),
    )
    parser.add_argument("--league", "-l", metavar="LEAGUE_ID",
                        help="Sleeper league id (the number in the league URL)")
    parser.add_argument("--user", "-u", metavar="USERNAME",
                        help="list a Sleeper user's leagues and exit")
    parser.add_argument("--season", type=int, help="season to scrape (default: current)")
    parser.add_argument("--week", type=int,
                        help="week to project; 0 means season-long (default: current)")
    parser.add_argument("--sources", nargs="+", metavar="SOURCE",
                        default=list(DEFAULT_SOURCES),
                        help=f"sites to scrape (default: all of {', '.join(DEFAULT_SOURCES)})")
    parser.add_argument("--positions", nargs="+", metavar="POS",
                        help=f"positions to project (default: what the league "
                             f"starts; any of {', '.join(POSITIONS)})")
    parser.add_argument("--avg-type",
                        choices=("all", "average", "robust", "weighted"),
                        default="all",
                        help="how to combine the sources (default: all three, "
                             "stored side by side; the display shows weighted)")
    parser.add_argument("--top", type=int, default=30,
                        help="how many players to print (default: 30)")
    parser.add_argument("--db", "-d", metavar="PATH", default="ffanalytics.sqlite",
                        help="SQLite file to write the run to "
                             "(default: ffanalytics.sqlite; '-' to skip)")
    parser.add_argument("--available-only", action="store_true",
                        help="print only players nobody in the league has rostered")
    parser.add_argument("--refresh-picks", action="store_true",
                        help="skip scraping; re-fetch the draft into an "
                             "existing --db and print who was picked since "
                             "the last refresh")
    parser.add_argument("--serve", action="store_true",
                        help="skip scraping; serve the draft board web UI "
                             "over an existing --db, refreshing picks in "
                             "the background (needs the [web] extra)")
    parser.add_argument("--port", type=int, default=8000,
                        help="port for --serve (default: 8000)")
    parser.add_argument("--poll", type=float, default=1.0, metavar="SECONDS",
                        help="how often --serve re-fetches the draft picks "
                             "(default: 1)")
    parser.add_argument("--no-ecr", action="store_true",
                        help="skip the expert consensus rankings scrape")
    parser.add_argument("--no-adp", action="store_true",
                        help=f"skip average draft position ({', '.join(ADP_SOURCES)})")
    parser.add_argument("--list-sources", action="store_true",
                        help="show what each site covers and exit")
    return parser


def _print_sources() -> None:
    rows = [
        {
            "source": source.name,
            "season_long": "yes" if source.draft else "no",
            "weekly": "yes" if source.weekly else "no",
            "weight": source.weight,
            "positions": ",".join(source.positions),
            "note": source.note,
        }
        for source in SOURCES.values()
    ]
    with pd.option_context("display.width", 200, "display.max_colwidth", 60):
        print(pd.DataFrame(rows).to_string(index=False))


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    if args.list_sources:
        _print_sources()
        return 0

    if args.user:
        leagues = leagues_for_user(args.user, args.season or _default_season())
        if leagues.empty:
            print(f"No leagues found for {args.user}")
            return 1
        print(leagues.to_string(index=False))
        return 0

    if not args.league:
        _parser().print_help()
        print("\nGive me a league: --league <LEAGUE_ID>, or --user <NAME> to find it.")
        return 2

    if args.refresh_picks:
        return _refresh_picks(args)

    if args.serve:
        return _serve(args)

    result = build_league_projections(
        args.league,
        week=args.week,
        season=args.season,
        sources=args.sources,
        positions=args.positions,
        avg_type=args.avg_type,
        with_ecr=not args.no_ecr,
        with_adp=not args.no_adp,
    )

    print("\n" + result.report())

    table = result.available if args.available_only else result.table
    label = ""
    if args.avg_type == "all" and "avg_type" in table.columns:
        table = table[table["avg_type"] == "weighted"]
        label = " (weighted)"
    print(f"\nTop {args.top} by points over replacement{label}:")
    with pd.option_context("display.width", 220, "display.max_columns", 40):
        print(table.head(args.top).pipe(_printable).to_string(index=False))

    if args.db and args.db != "-":
        written = write_sqlite(result, args.db)
        summary = ", ".join(
            f"{count} {table}" for table, count in written.items() if count
        )
        print(f"\nWrote {summary} to {args.db}")
    return 0


def _refresh_picks(args: argparse.Namespace) -> int:
    """The fast loop: pull the latest picks into an existing database."""
    if not args.db or args.db == "-":
        print("--refresh-picks needs --db pointing at an existing file",
              file=sys.stderr)
        return 2
    if not Path(args.db).exists():
        print(f"No database at {args.db} -- run a full scrape first:\n"
              f"  python -m ffanalytics --league {args.league} --db {args.db}",
              file=sys.stderr)
        return 2

    new_ids = refresh_picks(args.db, args.league)
    if not new_ids:
        print("no new picks")
        return 0

    marks = ",".join("?" * len(new_ids))
    with sqlite3.connect(args.db) as connection:
        rows = connection.execute(
            f"SELECT sleeper_id, player, manager, draft_round, draft_pick "
            f"FROM ownership WHERE sleeper_id IN ({marks}) ORDER BY draft_pick",
            sorted(new_ids),
        ).fetchall()

    print(f"{len(new_ids)} new pick(s):")
    seen = set()
    for sleeper_id, player, manager, draft_round, draft_pick in rows:
        seen.add(sleeper_id)
        parts = [player or sleeper_id]
        if draft_round is not None and draft_pick is not None:
            parts.append(f"(round {draft_round}, pick {draft_pick})")
        if manager:
            parts.append(f"-- {manager}")
        print("  " + " ".join(parts))
    for sleeper_id in sorted(new_ids - seen):
        print(f"  {sleeper_id}")
    return 0


def _serve(args: argparse.Namespace) -> int:
    """Serve the draft board over an existing database."""
    if not args.db or args.db == "-":
        print("--serve needs --db pointing at an existing file",
              file=sys.stderr)
        return 2
    if not Path(args.db).exists():
        print(f"No database at {args.db} -- run a full scrape first:\n"
              f"  python -m ffanalytics --league {args.league} --db {args.db}",
              file=sys.stderr)
        return 2

    try:
        from .web import serve
    except ImportError:
        print("The web UI needs FastAPI and uvicorn:\n"
              "  pip install \"ffanalytics[web]\"", file=sys.stderr)
        return 2

    serve(args.db, args.league, port=args.port, poll_seconds=args.poll)
    return 0


def _printable(table: pd.DataFrame) -> pd.DataFrame:
    columns = [c for c in (
        "rank", "pos", "pos_rank", "tier", "player", "team", "points",
        "points_vor", "floor", "ceiling", "dropoff", "uncertainty", "adp",
        "rostered_by",
    ) if c in table.columns]
    out = table[columns].copy()
    for column in ("points", "points_vor", "floor", "ceiling", "dropoff"):
        if column in out.columns:
            out[column] = out[column].round(1)
    return out



def _default_season() -> int:
    from .season import current_season

    return current_season()


if __name__ == "__main__":
    sys.exit(main())
