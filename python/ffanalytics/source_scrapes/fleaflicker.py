"""FleaFlicker projections (``R/source_scrapes.R:679-908``).

Weekly only, paginated 20 rows at a time.  Defensive line and defensive back
projections come from four narrower positions that get merged afterwards.
"""

from __future__ import annotations

import re

import pandas as pd

from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec, type_convert_frame
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import fleaflicker_columns
from ._common import (
    Session,
    cached_positions,
    convert_types,
    html_table,
    rate_limit,
    store_scrape,
)

__all__ = ["scrape_fleaflicker"]

DRAFT = False
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")

_POSITION_IDS = {
    "QB": 4, "RB": 1, "WR": 2, "TE": 8, "K": 16, "DST": 256,
    "DE": 2048, "DT": 64, "LB": 128, "CB": 512, "S": 1024,
}
_PAGES = {
    "K": 2, "DST": 2, "QB": 2, "DT": 4, "TE": 5,
    "DE": 6, "LB": 6, "S": 6, "RB": 6, "CB": 6, "WR": 6,
}
_DST_PATTERN = re.compile(r"(.*)\s+D/ST\s+([A-Z]{2,3}).*?(\d+).*")
_PLAYER_PATTERN = re.compile(r"(.*?)\s+(.*?)\s+(.*?)\s+(.*?)\s+.*(\d+)\)$")


def scrape_fleaflicker(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape weekly projections from fleaflicker.com."""
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    requested = list(pos)

    cached = cached_positions("FleaFlicker Scrape", "fleaflicker_scrape.rds", requested)
    if cached is not None:
        return ScrapeResult(cached, season=season, week=week)

    site_positions = list(requested)
    if "DL" in site_positions:
        site_positions = [p for p in site_positions if p != "DL"] + ["DE", "DT"]
    if "DB" in site_positions:
        site_positions = [p for p in site_positions if p != "DB"] + ["CB", "S"]

    session = Session("https://www.fleaflicker.com/nfl/leaders")

    def scrape_one(position: str) -> pd.DataFrame:
        position_id = _POSITION_IDS[position]
        offset = 0
        pages = _PAGES[position]
        out_frames = []

        for page_number in range(pages):
            url = (
                f"https://www.fleaflicker.com/nfl/leaders?week={week}&statType=7"
                f"&sortMode=7&position={position_id}&tableOffset={offset}"
            )
            if page_number == 0:
                print(f"Scraping {position} projections from\n {url}")
            else:
                rate_limit()

            page = session.read_html(url)

            site_ids = [
                re.sub(r".*-(\d+)$", r"\1", a.get("href", ""))
                for a in page.cssselect("a.player-text")
            ]

            tables = page.cssselect("#body-center-main table")
            if not tables:
                break
            scrape = html_table(tables[0], header=True)

            # Drop the pagination row at the bottom of the table.
            keep = ~scrape.apply(
                lambda row: row.astype(str).str.contains("Previous.*Next", regex=True).all(),
                axis=1,
            )
            scrape = scrape[keep]
            if scrape.empty:
                break

            col_names = [
                " ".join(re.sub(r"Week\s+\d+|Projected", "", f"{name} {value}").split())
                for name, value in zip(scrape.columns, scrape.iloc[0])
            ]
            if position == "K":
                for index, name in zip((9, 10, 12, 13),
                                       ("fg_att", "fg_pct", "xp_att", "xp_pct")):
                    if index < len(col_names):
                        col_names[index] = name

            renamed = rename_vec(col_names, fleaflicker_columns)
            unnamed = 0
            for index, name in enumerate(renamed):
                if not name:
                    unnamed += 1
                    renamed[index] = f"...{unnamed}"

            body = scrape.iloc[1:].reset_index(drop=True)
            body.columns = renamed[: body.shape[1]]
            body = body.replace(to_replace=["—", "NA", ""], value=pd.NA)
            body = type_convert_frame(body, exclude=())
            body = body[[c for c in body.columns if body[c].notna().any()]]

            if position == "DST":
                extracted = body["player"].str.extract(_DST_PATTERN)
                body["player"], body["team"], body["bye"] = (
                    extracted[0], extracted[1], extracted[2]
                )
                body["pos"] = "DST"
                body["data_src"] = "FleaFlicker"
                body["src_id"] = site_ids[: len(body)]
                body["id"] = get_mfl_id(
                    body["src_id"],
                    id_col_name="fleaflicker_id",
                    pos=body["pos"],
                    player_name=body["team"],
                ).to_numpy()
            else:
                body["player"] = body["player"].str.replace(
                    r"^Q(?=[A-Z])", "", regex=True
                )
                extracted = body["player"].str.extract(_PLAYER_PATTERN)
                first_name, last_name = extracted[0], extracted[1]
                body["player"] = first_name + " " + last_name
                body["pos"] = extracted[2]
                body["team"] = extracted[3]
                body["bye"] = extracted[4]
                body["data_src"] = "FleaFlicker"
                body["src_id"] = site_ids[: len(body)]
                body["id"] = get_mfl_id(
                    body["src_id"],
                    id_col_name="fleaflicker_id",
                    player_name=body["player"],
                    first=first_name,
                    last=last_name,
                    pos=body["pos"],
                    team=body["team"],
                ).to_numpy()

            out_frames.append(body)

            site_points = pd.to_numeric(body.get("site_pts"), errors="coerce")
            if site_points is None or site_points.min() <= 1 or len(body) < 20:
                break
            offset += 20

        if not out_frames:
            return pd.DataFrame()
        return convert_types(pd.concat(out_frames, ignore_index=True))

    frames = lapply_safe(site_positions, scrape_one)
    result = {p: f for p, f in zip(site_positions, frames) if f is not None}

    for combined, parts in (("DL", ("DE", "DT")), ("DB", ("CB", "S"))):
        if all(part in result for part in parts):
            merged = pd.concat([result.pop(part) for part in parts], ignore_index=True)
            merged["pos"] = combined
            result[combined] = merged.drop_duplicates(subset="src_id", keep="first")

    store_scrape(result, "fleaflicker_scrape.rds")
    return ScrapeResult(
        {p: result[p] for p in requested if p in result}, season=season, week=week
    )
