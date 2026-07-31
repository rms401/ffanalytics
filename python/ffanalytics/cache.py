"""A small time-to-live cache on disk.

Scraping a dozen sites takes minutes and the sites publish new numbers at most
daily, so results are kept for an hour by default.  Set ``FFANALYTICS_CACHE_DIR``
to move it, or ``FFANALYTICS_NO_CACHE=1`` to turn it off.
"""

from __future__ import annotations

import os
import pickle
import time
from pathlib import Path
from typing import Any

import pandas as pd

__all__ = ["cache_dir", "load", "save", "clear", "listing", "DEFAULT_TTL"]

DEFAULT_TTL = 60 * 60  # one hour


def cache_dir() -> Path:
    override = os.environ.get("FFANALYTICS_CACHE_DIR")
    if override:
        return Path(override)
    root = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(root) / "ffanalytics"


def _enabled() -> bool:
    return os.environ.get("FFANALYTICS_NO_CACHE", "").lower() not in ("1", "true", "yes")


def _path(key: str) -> Path:
    return cache_dir() / f"{key}.pkl"


def load(key: str, ttl: float = DEFAULT_TTL) -> Any | None:
    """The cached value for ``key``, or ``None`` if missing or too old."""
    if not _enabled():
        return None
    path = _path(key)
    try:
        age = time.time() - path.stat().st_mtime
    except OSError:
        return None
    if age > ttl:
        path.unlink(missing_ok=True)
        return None
    try:
        with path.open("rb") as handle:
            return pickle.load(handle)
    except (OSError, pickle.UnpicklingError, EOFError, AttributeError):
        path.unlink(missing_ok=True)
        return None


def save(key: str, value: Any) -> None:
    if not _enabled():
        return
    directory = cache_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = _path(key)
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as handle:
        pickle.dump(value, handle)
    tmp.replace(path)


def clear(key: str | None = None) -> int:
    """Delete one entry, or the whole cache.  Returns how many files went."""
    directory = cache_dir()
    if not directory.exists():
        return 0
    paths = [_path(key)] if key else list(directory.glob("*.pkl"))
    removed = 0
    for path in paths:
        if path.exists():
            path.unlink()
            removed += 1
    return removed


def listing() -> pd.DataFrame:
    """What is currently cached, newest first."""
    directory = cache_dir()
    paths = sorted(directory.glob("*.pkl")) if directory.exists() else []
    now = time.time()
    rows = [
        {
            "key": path.stem,
            "age_minutes": round((now - path.stat().st_mtime) / 60, 1),
            "size_kb": round(path.stat().st_size / 1024, 1),
        }
        for path in paths
    ]
    frame = pd.DataFrame(rows, columns=["key", "age_minutes", "size_kb"])
    return frame.sort_values("age_minutes").reset_index(drop=True)
