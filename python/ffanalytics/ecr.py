"""FantasyPros expert consensus rankings.

Where the projection sites tell you what a player will *do*, these tell you
where a room full of analysts would draft them.  The numbers are not in the
page's HTML; they are embedded in a ``var ecrData = {...}`` assignment.
"""

from __future__ import annotations

import json
import re

import pandas as pd

from . import cache
from .players import resolve_ids
from .sources._http import Session

__all__ = ["scrape_ecr", "PERIODS", "SCORING_FORMATS", "ECR_POSITIONS"]

PERIODS = ("draft", "weekly", "ros", "dynasty", "rookies")
SCORING_FORMATS = ("Std", "PPR", "Half")
ECR_POSITIONS = ("Overall", "QB", "RB", "WR", "TE", "K", "SUPERFLEX", "DST",
                 "IDP", "DL", "LB", "DB")

_BASE_URL = "https://www.fantasypros.com/nfl/rankings/"
#: Only these positions have separate rankings per scoring format.
_FORMAT_SENSITIVE = ("RB", "WR", "TE", "FLEX")
_ECR_DATA = re.compile(r"var\s+ecrData\s*=\s*(\{.*?\});", re.S)
_TTL = 8 * 60 * 60

_EMPTY = pd.DataFrame(columns=["id", "avg", "std_dev", "ecr_rank", "ecr_min", "ecr_max"])


def _page(period: str, position: str, scoring: str) -> str:
    slug = position.lower()
    prefix = {"Std": "", "PPR": "ppr", "Half": "half-point-ppr"}[scoring]
    formatted = scoring != "Std" and position in _FORMAT_SENSITIVE

    if period == "draft":
        if position == "Overall":
            return "consensus-cheatsheets.php" if scoring == "Std" \
                else f"{prefix}-cheatsheets.php"
        return f"{prefix}-{slug}-cheatsheets.php" if formatted else f"{slug}-cheatsheets.php"
    if period == "weekly":
        return f"{prefix}-{slug}.php" if formatted else f"{slug}.php"
    if period == "ros":
        if scoring != "Std" and position in _FORMAT_SENSITIVE + ("Overall",):
            return f"ros-{prefix}-{slug}.php"
        return f"ros-{slug}.php"
    if period == "dynasty":
        return f"dynasty-{slug}.php"
    return "rookies.php"


def scrape_ecr(period: str = "draft", position: str = "Overall",
               scoring: str = "Std") -> pd.DataFrame:
    """Consensus rank, spread and best/worst rank for one position.

    Returns an empty frame rather than raising when a combination is not
    published -- there are no overall weekly rankings, for instance.
    """
    if period not in PERIODS:
        raise ValueError(f"period must be one of {PERIODS}")
    if position not in ECR_POSITIONS:
        return _EMPTY.copy()
    if scoring not in SCORING_FORMATS:
        raise ValueError(f"scoring must be one of {SCORING_FORMATS}")
    if period == "weekly" and position == "Overall":
        return _EMPTY.copy()

    key = f"ecr_{period}_{position.lower()}_{scoring.lower()}"
    cached = cache.load(key, _TTL)
    if cached is not None:
        return cached

    url = _BASE_URL + _page(period, position, scoring)
    try:
        page = Session().html(url)
    except Exception as error:  # noqa: BLE001 - a missing ranking page is not fatal
        print(f"  ECR {position}: {type(error).__name__}: {error}")
        return _EMPTY.copy()

    scripts = page.xpath(".//script[contains(text(), 'var ecrData')]")
    match = _ECR_DATA.search(scripts[0].text_content()) if scripts else None
    if match is None:
        print(f"  ECR {position}: no ranking data at {url}")
        return _EMPTY.copy()

    players = pd.DataFrame(json.loads(match.group(1)).get("players", []))
    if players.empty:
        return _EMPTY.copy()

    out = pd.DataFrame({
        "id": resolve_ids(
            players["player_id"].astype(str), "fantasypro_num_id",
            name=players.get("player_name"),
            team=players.get("player_team_id"),
            pos=players.get("player_position_id"),
        ).to_numpy(),
        "avg": pd.to_numeric(players.get("rank_ave"), errors="coerce"),
        "std_dev": pd.to_numeric(players.get("rank_std"), errors="coerce"),
        "ecr_rank": pd.to_numeric(players.get("rank_ecr"), errors="coerce").astype("Int64"),
        "ecr_min": pd.to_numeric(players.get("rank_min"), errors="coerce").astype("Int64"),
        "ecr_max": pd.to_numeric(players.get("rank_max"), errors="coerce").astype("Int64"),
    })
    cache.save(key, out)
    return out
