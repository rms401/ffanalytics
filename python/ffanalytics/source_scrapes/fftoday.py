"""FFToday projections (``R/source_scrapes.R:911-1092``)."""

from __future__ import annotations

import posixpath
import re

import pandas as pd

from ..helper_funcs import get_mfl_id, get_scrape_year, lapply_safe
from ..rcompat.stats import rename_vec
from ..results import ScrapeResult
from ..schedule_data import get_scrape_week
from ..source_objects import fftoday_columns
from ._common import Session, convert_types, html_table, rate_limit

__all__ = ["scrape_fftoday"]

DRAFT = True
WEEKLY = True
POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")

_POSITION_IDS = {
    "QB": 10, "RB": 20, "WR": 30, "TE": 40, "DL": 50, "LB": 60, "DB": 70,
    "K": 80, "DST": 99,
}
_PAGES = {
    "QB": 1, "TE": 1, "K": 1, "DST": 1, "RB": 2, "WR": 3, "DL": 3, "DB": 3, "LB": 3,
}


def scrape_fftoday(pos=POSITIONS, season=None, week=None, **kwargs) -> ScrapeResult:
    """Scrape projections from fftoday.com."""
    print("\nThe FFToday scrape uses a 2 second delay between pages")
    season = get_scrape_year() if season is None else season
    week = get_scrape_week() if week is None else week

    if week > 18:
        week = week + 2
    positions = list(pos)
    if week > 0:
        positions = [p for p in positions if p not in ("DST", "DL", "LB", "DB")]

    session = Session("https://www.fftoday.com/rankings/index.html")

    def scrape_one(position: str) -> pd.DataFrame:
        position_id = _POSITION_IDS[position]
        pages = _PAGES[position]
        out_frames = []

        for page_number in range(pages):
            rate_limit()
            if week == 0:
                url = (
                    "https://www.fftoday.com/rankings/playerproj.php"
                    f"?Season={season}&PosID={position_id}&LeagueID=1"
                    f"&order_by=FFPts&sort_order=DESC&cur_page={page_number}"
                )
            else:
                url = (
                    "https://www.fftoday.com/rankings/playerwkproj.php"
                    f"?Season={season}&GameWeek={week}&PosID={position_id}&LeagueID=1"
                    f"&order_by=FFPts&sort_order=DESC&cur_page={page_number}"
                )
            if page_number == 0:
                print(f"Scraping {position} projections from\n {url}")

            page = session.read_html(url)

            if position == "DST":
                hrefs = [
                    a.get("href", "")
                    for a in page.xpath("//a[contains(@href, 'stats/players')]")
                ]
                site_ids = [re.sub(r".*?=(\d{4}).*", r"\1", href) for href in hrefs]
                site_ids = [value for value in site_ids if re.search(r"\d{4}", value)]
            else:
                hrefs = [
                    a.get("href", "")
                    for a in page.xpath("//a[contains(@href, 'stats/players/')]")
                ]
                site_ids = [posixpath.basename(posixpath.dirname(href)) for href in hrefs]

            tables = page.cssselect("table table table")
            if not tables:
                continue
            scrape = html_table(tables[0], header=False)
            scrape = scrape.replace(",", "", regex=True)
            if len(scrape) < 3:
                continue

            second = [re.sub(r"^(.*?)\n.*", r"\1", value, flags=re.S)
                      for value in scrape.iloc[1]]
            col_names = [" ".join(f"{a} {b}".split())
                         for a, b in zip(scrape.iloc[0], second)]
            col_names = rename_vec(col_names, fftoday_columns)
            if position in ("DL", "DB", "LB"):
                col_names = [
                    re.sub(r"(dst|pass)_", "idp_", name) for name in col_names
                ]

            body = scrape.iloc[2:].replace("%", "", regex=True).reset_index(drop=True)
            body.columns = col_names[: body.shape[1]]
            frame = convert_types(body, exclude=())

            frame["pos"] = position
            frame["data_src"] = "FFToday"
            frame["src_id"] = site_ids[: len(frame)]
            frame = frame.drop(columns="chg", errors="ignore")

            if week > 0 and "opp" in frame.columns:
                frame["opp"] = frame["opp"].astype(str).str.replace("@", "", regex=False)

            if position == "DST":
                frame["id"] = get_mfl_id(
                    frame["src_id"], id_col_name="fftoday_id", pos=frame["pos"]
                ).to_numpy()
            else:
                frame["id"] = get_mfl_id(
                    frame["src_id"],
                    id_col_name="fftoday_id",
                    player_name=frame["player"],
                    team=frame["team"],
                    pos=frame["pos"],
                ).to_numpy()

            if "bye" in frame.columns:
                frame["bye"] = pd.to_numeric(
                    frame["bye"].astype(str).str.replace("-", "", regex=False),
                    errors="coerce",
                ).astype("Int64")

            out_frames.append(frame)

        out_frames = [frame for frame in out_frames if len(frame) > 0]
        if not out_frames:
            return pd.DataFrame()
        return pd.concat(out_frames, ignore_index=True)

    frames = lapply_safe(positions, scrape_one)
    result = {p: f for p, f in zip(positions, frames) if f is not None}
    return ScrapeResult(result, season=season, week=week)
