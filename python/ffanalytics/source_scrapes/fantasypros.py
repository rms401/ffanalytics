"""FantasyPros projections (``R/source_scrapes.R:1095-1189``)."""

from __future__ import annotations

import re

import pandas as pd

from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import fantasypros_columns
from ._common import (
    Session,
    convert_types,
    html_table,
    rate_limit,
    row_text,
    store_scrape,
)

__all__ = ["scrape_fantasypros"]

DRAFT = True
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")

_PLAYER_PATTERN = re.compile(r"(.*)\s+([A-Z]{2,3})")


def scrape_fantasypros(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape projections from fantasypros.com."""
    print("\nThe FantasyPros scrape uses a 2 second delay between pages")
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    positions = list(pos)

    scrape_week = f".php?week={week}" if week > 0 else ".php?week=draft"
    session = Session("https://www.fantasypros.com/nfl/projections")

    def scrape_one(position: str) -> pd.DataFrame:
        url = (
            f"https://www.fantasypros.com/nfl/projections/"
            f"{position.lower()}{scrape_week}"
        )
        rate_limit()
        print(f"Scraping {position} projections from\n  {url}")
        page = session.read_html(url)

        head = page.cssselect("table > thead")[0]
        if position in ("K", "DST"):
            col_names = row_text(head, separator="\t")[-1].split("\t")
        else:
            header = html_table(head, header=False)
            col_names = [
                " ".join(f"{a} {b}".split()) for a, b in zip(header.iloc[0], header.iloc[1])
            ]
        col_names = rename_vec(col_names, fantasypros_columns)

        num_ids = []
        for row in page.cssselect("table > tbody > tr"):
            classes = row.get("class")
            if classes is None:
                continue
            num_ids.append(re.sub(r".*?(\d{4,6}).*", r"\1", classes))

        frame = html_table(page.cssselect("table > tbody")[0], header=False)
        frame = frame.replace(",", "", regex=True)
        frame.columns = col_names[: frame.shape[1]]

        if position == "DST":
            frame["src_id"] = num_ids[: len(frame)]
            frame["data_src"] = "FantasyPros"
            frame["pos"] = position
            frame["id"] = get_mfl_id(
                frame["src_id"], id_col_name="fantasypro_num_id"
            ).to_numpy()
        else:
            extracted = frame["player"].str.extract(_PLAYER_PATTERN)
            frame["player"], frame["team"] = extracted[0], extracted[1]
            frame["src_id"] = num_ids[: len(frame)]
            frame["data_src"] = "FantasyPros"
            frame["pos"] = position
            frame["id"] = get_mfl_id(
                frame["src_id"],
                id_col_name="fantasypro_num_id",
                player_name=frame["player"],
                team=frame["team"],
                pos=frame["pos"],
            ).to_numpy()

        frame = convert_types(frame)
        return frame[frame["site_pts"] > 0]

    frames = lapply_safe(positions, scrape_one)
    result = {p: f for p, f in zip(positions, frames) if f is not None}

    # NOTE: mirrors R/source_scrapes.R:1187, which caches the FantasyPros
    # scrape under WalterFootball's filename. Kept as-is rather than silently
    # diverging; the two sources therefore share one cache slot.
    store_scrape(result, "walterfootball_scrape.rds")
    return ScrapeResult(result, season=season, week=week)
