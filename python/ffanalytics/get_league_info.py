"""Reading league settings from Sleeper.

Ported from ``R/get_league_info.R``, which is experimental and incomplete in
the R package -- ``clean_scoring_sleeper`` builds a scoring object and never
returns it.  That is reproduced here rather than quietly finished, since
guessing the intended mapping would change behaviour.
"""

from __future__ import annotations

import copy
import tempfile
from pathlib import Path

import requests

from .scoring_rules import scoring_empty
from .source_scrapes._common import USER_AGENT

__all__ = [
    "get_league_info_sleeper",
    "clean_scoring_sleeper",
    "get_sleeper_avatar_png",
]


def get_league_info_sleeper(league_id: str | int) -> dict:
    """League name, size, starters, bench count and raw scoring settings."""
    response = requests.get(
        f"https://api.sleeper.app/v1/league/{league_id}",
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    payload = response.json()

    roster = payload.get("roster_positions") or []
    return {
        "league_name": payload.get("name"),
        "n_teams": payload.get("total_rosters"),
        "starters": [slot for slot in roster if slot != "BN"],
        "n_bench": sum(1 for slot in roster if slot == "BN"),
        "scoring_obj": payload.get("scoring_settings"),
    }


def clean_scoring_sleeper(scoring_obj: dict):
    """Translate Sleeper scoring settings into this package's format.

    Incomplete, exactly as in R: the object is assembled but never returned, and
    several Sleeper settings (``def_st_ff`` among them) are not mapped at all.
    Left as-is so the port does not invent behaviour the R package never had --
    call :func:`ffanalytics.custom_scoring` to build scoring rules instead.
    """
    obj = copy.deepcopy(scoring_empty)

    obj["dst"]["dst_blk"] = scoring_obj.get("blk_kick")
    obj["pass"]["pass_300_yds"] = scoring_obj.get("bonus_pass_yd_300")
    obj["pass"]["pass_400_yds"] = scoring_obj.get("bonus_pass_yd_400")

    te_bonus = scoring_obj.get("bonus_rec_te") or 0
    if te_bonus > 0:
        obj["rec"]["all_pos"] = False
        for position in ("QB", "RB", "WR"):
            obj["rec"][position]["rec"] = scoring_obj.get("rec")
        obj["rec"]["TE"]["rec"] = (scoring_obj.get("rec") or 0) + te_bonus
    else:
        obj["rec"]["all_pos"] = True
        obj["rec"]["rec"] = scoring_obj.get("rec")

    obj["rec"]["rec_100_yds"] = scoring_obj.get("bonus_rec_yd_100")
    obj["rec"]["rec_200_yds"] = scoring_obj.get("bonus_rec_yd_200")
    obj["rush"]["rush_100_yds"] = scoring_obj.get("bonus_rush_yd_100")
    obj["rush"]["rush_200_yds"] = scoring_obj.get("bonus_rush_yd_200")

    obj["dst"]["dst_fum_rec"] = scoring_obj.get("def_st_fum_rec")
    obj["dst"]["dst_td"] = max(
        value for value in
        (scoring_obj.get("def_st_td") or 0, scoring_obj.get("def_td") or 0)
    )
    obj["idp"]["idp_fum_force"] = scoring_obj.get("ff")
    obj["kick"]["fg_0019"] = -99

    # R returns nothing here; see the docstring.
    return None


def get_sleeper_avatar_png(avatar_id: str) -> Path:
    """Download a league avatar and return the local path."""
    response = requests.get(
        f"https://sleepercdn.com/uploads/{avatar_id}",
        headers={"User-Agent": USER_AGENT},
        timeout=60,
    )
    response.raise_for_status()
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(response.content)
        return Path(handle.name)
