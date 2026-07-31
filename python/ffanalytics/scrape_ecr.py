"""FantasyPros expert consensus rankings.

Ported from ``R/scrape_ecr.R``.  The rankings are not in the page's HTML -- they
are embedded in a ``var ecrData = {...}`` assignment in a ``<script>`` tag.
"""

from __future__ import annotations

import json
import re

import pandas as pd

from .caching import cache_object, get_cached_object, is_cached
from .helper_funcs import get_mfl_id
from .source_scrapes._common import Session

__all__ = ["scrape_ecr"]

RANK_PERIODS = ("draft", "weekly", "ros", "dynasty", "rookies")
RANK_TYPES = ("Std", "PPR", "Half")
ECR_POSITIONS = (
    "Overall", "QB", "RB", "WR", "TE", "K", "SUPERFLEX", "DST", "IDP", "DL", "LB", "DB",
)

_BASE_URL = "https://www.fantasypros.com/nfl/rankings/"
_PPR_POSITIONS = ("RB", "WR", "TE", "FLEX")
_ECR_DATA = re.compile(r"var\s+ecrData\s*=\s*(\{.*?\});", re.S)


def _page_name(rank_period: str, position: str, rank_type: str) -> str:
    """The ``.php`` page for a period/position/scoring combination."""
    slug = position.lower()
    suffix = {"Std": "", "PPR": "ppr", "Half": "half-point-ppr"}[rank_type]

    if rank_period == "draft":
        if position == "Overall":
            return (
                "consensus-cheatsheets.php" if rank_type == "Std"
                else f"{suffix}-cheatsheets.php"
            )
        if rank_type == "Std" or position not in _PPR_POSITIONS:
            return f"{slug}-cheatsheets.php"
        return f"{suffix}-{slug}-cheatsheets.php"

    if rank_period == "weekly":
        if rank_type == "Std" or position not in _PPR_POSITIONS:
            return f"{slug}.php"
        return f"{suffix}-{slug}.php"

    if rank_period == "ros":
        if rank_type == "Std" or position not in _PPR_POSITIONS + ("Overall",):
            return f"ros-{slug}.php"
        return f"ros-{suffix}-{slug}.php"

    if rank_period == "dynasty":
        return f"dynasty-{slug}.php"
    return "rookies.php"


def scrape_ecr(rank_period: str = "draft", position: str = "Overall",
               rank_type: str = "Std") -> pd.DataFrame:
    """Scrape expert consensus rankings from FantasyPros.

    Returns ``id``, ``avg``, ``std_dev``, ``ecr_rank``, ``ecr_min`` and
    ``ecr_max``.  Draft and weekly rankings are cached for 8 hours.
    """
    if rank_period not in RANK_PERIODS:
        raise ValueError(f"rank_period must be one of {RANK_PERIODS}")
    if position not in ECR_POSITIONS:
        raise ValueError(f"position must be one of {ECR_POSITIONS}")
    if rank_type not in RANK_TYPES:
        raise ValueError(f"rank_type must be one of {RANK_TYPES}")

    cacheable = rank_period in ("draft", "weekly")
    display_name = f"ECR {rank_period.title()} {position} {rank_type}"
    file_name = f"ecr_{rank_period}_{position.lower()}_{rank_type.lower()}.rds"

    if cacheable and is_cached(display_name):
        return get_cached_object(file_name)

    if rank_period == "weekly" and position == "Overall":
        raise ValueError("Overall weekly ranks are not provided")
    if rank_period == "ros" and position == "IDP":
        raise ValueError("Combined IDP ROS ranks are not provided")

    url = _BASE_URL + _page_name(rank_period, position, rank_type)
    page = Session().read_html(url)

    scripts = page.xpath(".//script[contains(text(), 'var ecrData')]")
    if not scripts:
        raise ValueError(f"No ranking data found at {url}")

    match = _ECR_DATA.search(scripts[0].text_content())
    if match is None:
        raise ValueError(f"Could not parse the ranking data at {url}")

    players = json.loads(match.group(1)).get("players", [])
    frame = pd.DataFrame(players)
    if frame.empty:
        return pd.DataFrame(
            columns=["id", "avg", "std_dev", "ecr_rank", "ecr_min", "ecr_max"]
        )

    out = pd.DataFrame(
        {
            "id": get_mfl_id(
                frame["player_id"].astype(str),
                id_col_name="fantasypro_num_id",
                player_name=frame.get("player_name"),
                team=frame.get("player_team_id"),
                pos=frame.get("player_position_id"),
            ).to_numpy(),
            "avg": pd.to_numeric(frame.get("rank_ave"), errors="coerce"),
            "std_dev": pd.to_numeric(frame.get("rank_std"), errors="coerce"),
            "ecr_rank": pd.to_numeric(frame.get("rank_ecr"), errors="coerce").astype("Int64"),
            "ecr_min": pd.to_numeric(frame.get("rank_min"), errors="coerce").astype("Int64"),
            "ecr_max": pd.to_numeric(frame.get("rank_max"), errors="coerce").astype("Int64"),
        }
    )

    if cacheable:
        cache_object(out, file_name)
    return out
