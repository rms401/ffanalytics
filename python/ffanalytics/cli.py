"""Command line entry point: ``python -m ffanalytics``."""

from __future__ import annotations

import argparse
import sys

import pandas as pd

from . import cache
from .adp import ADP_SOURCES
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
    parser.add_argument("--avg-type", choices=("average", "robust", "weighted"),
                        default="average",
                        help="how to combine the sources (default: average)")
    parser.add_argument("--top", type=int, default=30,
                        help="how many players to print (default: 30)")
    parser.add_argument("--out", "-o", metavar="PATH",
                        help="write the full table to a .csv or .xlsx file")
    parser.add_argument("--available-only", action="store_true",
                        help="print only players nobody in the league has rostered")
    parser.add_argument("--no-ecr", action="store_true",
                        help="skip the expert consensus rankings scrape")
    parser.add_argument("--no-adp", action="store_true",
                        help=f"skip average draft position ({', '.join(ADP_SOURCES)})")
    parser.add_argument("--refresh", action="store_true",
                        help="ignore cached scrapes and fetch everything again")
    parser.add_argument("--clear-cache", action="store_true",
                        help="empty the cache and exit")
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

    if args.clear_cache:
        print(f"Removed {cache.clear()} cached files from {cache.cache_dir()}")
        return 0

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

    result = build_league_projections(
        args.league,
        week=args.week,
        season=args.season,
        sources=args.sources,
        positions=args.positions,
        avg_type=args.avg_type,
        with_ecr=not args.no_ecr,
        with_adp=not args.no_adp,
        cache_ttl=0 if args.refresh else None,
    )

    print("\n" + result.report())

    table = result.available if args.available_only else result.table
    print(f"\nTop {args.top} by points over replacement:")
    with pd.option_context("display.width", 220, "display.max_columns", 40):
        print(table.head(args.top).pipe(_printable).to_string(index=False))

    if args.out:
        _write(result.table, args.out)
        print(f"\nWrote {len(result.table)} rows to {args.out}")
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


def _write(table: pd.DataFrame, path: str) -> None:
    if path.endswith(".xlsx"):
        table.to_excel(path, index=False)
    else:
        table.to_csv(path, index=False)


def _default_season() -> int:
    from .season import current_season

    return current_season()


if __name__ == "__main__":
    sys.exit(main())
