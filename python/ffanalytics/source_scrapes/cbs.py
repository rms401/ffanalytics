"""CBS Sports projections (``R/source_scrapes.R:4-126``)."""

from __future__ import annotations

import re

import pandas as pd

from ..results import ScrapeResult
from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec
from ..recode_vars import team_corrections
from ..schedule_data import get_scrape_week
from ..source_objects import cbs_columns
from ..sysdata import player_ids
from ._common import (
    Session,
    cached_positions,
    convert_types,
    html_table,
    rate_limit,
    row_text,
    store_scrape,
)

__all__ = ["scrape_cbs"]

DRAFT = True
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")

_PLAYER_PATTERN = re.compile(
    r".*?\s{2,}[A-Z]{1,3}\s{2,}[A-Z]{2,3}\s{2,}(.*?)\s{2,}(.*?)\s{2,}(.*)"
)


def scrape_cbs(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape projections from cbssports.com."""
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week
    positions = list(pos)

    cached = cached_positions("CBS Scrape", "cbs_scrape.rds", positions)
    if cached is not None:
        return ScrapeResult(cached, season=season, week=week)

    print("\nThe CBS scrape uses a 2 second delay between pages")
    scrape_week = "restofseason" if week in (0, "ros") else week
    session = Session("https://www.cbssports.com/fantasy/football/")

    def scrape_one(position: str) -> pd.DataFrame:
        url = (
            f"https://www.cbssports.com/fantasy/football/stats/{position}/"
            f"{season}/{scrape_week}/projections/nonppr/"
        )
        if position != positions[0]:
            rate_limit()
        print(f"Scraping {position} projections from\n  {url}")

        page = session.read_html(url)

        head = page.cssselect("#TableBase > div > div > table > thead > tr.TableBase-headTr")
        col_names = re.split(r"\n|\t", row_text(head[0], separator="\t")[0]) if head else []
        col_names = [name for name in col_names if re.search(r"[A-Z]", name)]
        col_names = rename_vec(col_names, cbs_columns)

        if position == "DST":
            hrefs = [a.get("href", "") for a in page.cssselect("span.TeamName a")]
            cbs_id = [re.sub(r".*?([A-Z]{2,3}).*", r"\1", href) for href in hrefs]
        else:
            hrefs = [
                a.get("href", "")
                for a in page.cssselect(
                    "table > tbody > tr > td:nth-child(1) > "
                    "span.CellPlayerName--long > span > a"
                )
            ]
            cbs_id = [re.sub(r".*?(\d+).*", r"\1", href) for href in hrefs]

        body = page.cssselect("#TableBase > div > div > table > tbody")
        frame = html_table(body[0], header=False)
        frame.columns = col_names[: frame.shape[1]]

        if position != "DST":
            extracted = frame["player"].str.extract(_PLAYER_PATTERN)
            frame["player"] = extracted[0]
            frame["pos"] = extracted[1]
            frame["team"] = extracted[2]
            frame["src_id"] = cbs_id[: len(frame)]
            frame["data_src"] = "CBS"
            frame["id"] = get_mfl_id(
                frame["src_id"],
                id_col_name="cbs_id",
                player_name=frame["player"],
                pos=frame["pos"],
                team=frame["team"],
            ).to_numpy()
        else:
            frame["team"] = cbs_id[: len(frame)]
            frame["data_src"] = "CBS"
            frame["pos"] = position
            frame["id"] = get_mfl_id(
                pos=["DST"] * len(frame),
                team=rename_vec(frame["team"].tolist(), team_corrections),
            ).to_numpy()
            crosswalk = player_ids().set_index("id")["cbs_id"]
            frame["src_id"] = frame["id"].map(crosswalk)

        frame = frame.replace("—", pd.NA)
        frame = convert_types(frame)
        return frame[frame["site_pts"] > 0]

    frames = lapply_safe(positions, scrape_one)
    result = {
        position: frame
        for position, frame in zip(positions, frames)
        if frame is not None
    }
    store_scrape(result, "cbs_scrape.rds")
    return ScrapeResult(result, season=season, week=week)
