"""On-disk cache for scraped data, mirroring ``R/caching_helpers.R``.

Same policy as the R package: ADP/AAV and ECR scrapes live for 8 hours,
projection scrapes for 1 hour, and stale entries are dropped whenever the cache
is inspected.

The cache directory is *not* shared with the R package.  R stores ``.rds``
files, which Python cannot write, so the two would corrupt each other's view of
what is cached; this uses its own directory and pickle files under the same
XDG cache root.
"""

from __future__ import annotations

import os
import pickle
import time
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = [
    "cache_dir",
    "cache_object",
    "get_cached_object",
    "clear_ffanalytics_cache",
    "list_ffanalytics_cache",
    "cache_file_names",
]

_SCRAPE_TTL_SECONDS = 60 * 60          # 1 hour for projection scrapes
_DEFAULT_TTL_SECONDS = 8 * 60 * 60     # 8 hours for everything else


def cache_dir() -> Path:
    """Cache location (``FFANALYTICS_CACHE_DIR`` overrides)."""
    override = os.environ.get("FFANALYTICS_CACHE_DIR")
    if override:
        return Path(override)
    root = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(root) / "ffanalytics" / "python"


def _ensure_cache_dir() -> Path:
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _clear_cache_by_time() -> None:
    directory = cache_dir()
    if not directory.exists():
        return
    now = time.time()
    for path in directory.glob("*.pkl"):
        try:
            age = now - path.stat().st_mtime
        except OSError:
            continue
        ttl = _SCRAPE_TTL_SECONDS if "_scrape" in path.name else _DEFAULT_TTL_SECONDS
        if age > ttl:
            path.unlink(missing_ok=True)


def cache_object(obj: Any, file_name: str) -> None:
    """Store ``obj`` under ``file_name`` unless it is already cached."""
    directory = _ensure_cache_dir()
    _clear_cache_by_time()
    path = directory / _pickle_name(file_name)
    if not path.exists():
        with path.open("wb") as handle:
            pickle.dump(obj, handle)


def get_cached_object(file_name: str) -> Any:
    with (cache_dir() / _pickle_name(file_name)).open("rb") as handle:
        return pickle.load(handle)


def is_cached(display_name: str) -> bool:
    """Whether the object with this display name is currently cached."""
    return display_name in list_ffanalytics_cache(quiet=True)["object"].tolist()


def list_ffanalytics_cache(quiet: bool = False) -> pd.DataFrame:
    """Drop expired entries and report what is cached, newest first.

    Returns a frame of ``object`` (display name) and ``hr_min_since_cache``.
    """
    directory = cache_dir()
    _clear_cache_by_time()

    paths = sorted(directory.glob("*.pkl")) if directory.exists() else []
    if not paths and not quiet:
        print("ffanalytics cache is empty")

    now = time.time()
    rows = []
    for path in paths:
        rds_name = path.name[: -len(".pkl")] + ".rds"
        elapsed = now - path.stat().st_mtime
        rows.append(
            {
                "object": cache_file_names.get(rds_name, rds_name),
                "hr_min_since_cache": f"{int(elapsed // 3600):02d}:{int(elapsed % 3600 // 60):02d}",
                "_mtime": path.stat().st_mtime,
            }
        )

    frame = pd.DataFrame(rows, columns=["object", "hr_min_since_cache", "_mtime"])
    frame = frame.sort_values("_mtime", ascending=False).drop(columns="_mtime")
    return frame.reset_index(drop=True)


def clear_ffanalytics_cache(ffa_objects: list[str] | str | None = None) -> None:
    """Clear the whole cache, or just the named objects.

    ``ffa_objects`` takes the display names reported by
    :func:`list_ffanalytics_cache`.
    """
    directory = cache_dir()
    if not directory.exists():
        return

    if ffa_objects is None:
        for path in directory.glob("*.pkl"):
            path.unlink(missing_ok=True)
        return

    if isinstance(ffa_objects, str):
        ffa_objects = [ffa_objects]

    wanted = {
        _pickle_name(rds_name)
        for rds_name, display in cache_file_names.items()
        if display in ffa_objects
    }
    removed = 0
    for path in directory.glob("*.pkl"):
        if path.name in wanted:
            path.unlink(missing_ok=True)
            removed += 1

    if removed == 0:
        print(
            "Note: None of the listed objects were removed\n\n"
            "Use list_ffanalytics_cache() to see object names"
        )
    elif removed != len(ffa_objects):
        print(
            "Note: Not all of the listed objects were removed\n\n"
            "Use list_ffanalytics_cache() to check object names"
        )


def _pickle_name(file_name: str) -> str:
    return file_name[: -len(".rds")] + ".pkl" if file_name.endswith(".rds") else file_name


def _build_cache_file_names() -> dict[str, str]:
    """The filename -> display-name registry from ``R/caching_helpers.R:117-210``."""
    names = {
        "yahoo_adp_aav.rds": "Yahoo ADP/AAV",
        "espn_adp_aav.rds": "ESPN ADP/AAV",
        "cbs_adp.rds": "CBS ADP",
        "rts_adp.rds": "RTS ADP",
        "rts_aav.rds": "RTS AAV",
        "nfl_adp.rds": "NFL ADP",
        "mfl_adp.rds": "MFL ADP",
        "mfl_aav.rds": "MFL AAV",
        "ffc_adp.rds": "FFC ADP",
    }

    ecr_positions = [
        ("overall", "Overall"), ("qb", "QB"), ("rb", "RB"), ("wr", "WR"),
        ("te", "TE"), ("k", "K"), ("superflex", "SUPERFLEX"), ("dst", "DST"),
        ("idp", "IDP"), ("dl", "DL"), ("lb", "LB"), ("db", "DB"),
    ]
    for slug, label in ecr_positions:
        for period, period_label in (("draft", "Draft"), ("weekly", "Weekly")):
            for scoring, scoring_label in (("std", "Std"), ("half", "Half"), ("ppr", "PPR")):
                names[f"ecr_{period}_{slug}_{scoring}.rds"] = (
                    f"ECR {period_label} {label} {scoring_label}"
                )

    for slug, label in [
        ("cbs", "CBS"), ("nfl", "NFL"), ("fantasysharks", "FantasySharks"),
        ("numberfire", "NumberFire"), ("walterfootball", "WalterFootball"),
        ("fleaflicker", "FleaFlicker"), ("fftoday", "FFToday"),
        ("fantasypros", "FantasyPros"), ("rtsports", "RTSports"),
        ("espn", "ESPN"), ("fanduel", "FanDuel"),
    ]:
        names[f"{slug}_scrape.rds"] = f"{label} Scrape"

    return names


#: Cache filename -> human-readable object name.
cache_file_names = _build_cache_file_names()
