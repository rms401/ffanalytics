"""FFToday projections."""

from __future__ import annotations

import re

import pandas as pd

from ..players import resolve_ids
from ..stats import to_numeric_frame
from ._http import Session, for_each_position, html_table, polite_pause
from .columns import FFTODAY, FFTODAY_IDP, rename

__all__ = ["scrape_fftoday"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")
DRAFT = True
WEEKLY = True

_POSITION_IDS = {"QB": 10, "RB": 20, "WR": 30, "TE": 40, "DL": 50, "LB": 60,
                 "DB": 70, "K": 80, "DST": 99}
_PAGES = {"QB": 1, "TE": 1, "K": 1, "DST": 1, "RB": 2, "WR": 3, "DL": 3, "DB": 3, "LB": 3}
_TEAM_ONLY = ("DST", "DL", "LB", "DB")

_PLAYER_LINK = re.compile(r"stats/players/(\d+)/")
_DST_LINK = re.compile(r"stats/players\?TeamID=(\d{4})")


def scrape_fftoday(positions=POSITIONS, season=None, week=0, **_) -> dict[str, pd.DataFrame]:
    if week > 0:
        # FFToday only publishes team defense and IDP numbers for the season.
        positions = [p for p in positions if p not in _TEAM_ONLY]
    session = Session("https://www.fftoday.com/rankings/index.html")

    def scrape_one(position: str) -> pd.DataFrame:
        idp = position in ("DL", "LB", "DB")
        pages = []
        for page_number in range(_PAGES[position]):
            page_name = "playerproj" if week == 0 else "playerwkproj"
            query = f"Season={season}"
            if week > 0:
                query += f"&GameWeek={week}"
            query += (
                f"&PosID={_POSITION_IDS[position]}&LeagueID=1"
                f"&order_by=FFPts&sort_order=DESC&cur_page={page_number}"
            )
            url = f"https://www.fftoday.com/rankings/{page_name}.php?{query}"
            if page_number == 0:
                print(f"  FFToday {position}: {url}")

            page = session.html(url)
            polite_pause()

            tables = page.cssselect("table table table")
            if not tables:
                continue
            table = html_table(tables[0], header=False).replace(",", "", regex=True)
            if len(table) < 3:
                continue

            # The page also carries a bare /stats/players nav link, which would
            # shift every id by one if it were counted as a player.
            hrefs = [
                link.get("href", "")
                for link in page.xpath("//a[contains(@href, 'stats/players')]")
            ]
            pattern = _DST_LINK if position == "DST" else _PLAYER_LINK
            site_ids = [match.group(1) for match in map(pattern.search, hrefs) if match]

            # Two header rows, the second holding the stat group.
            second = [re.sub(r"^(.*?)\n.*", r"\1", value, flags=re.S) for value in table.iloc[1]]
            names = rename(
                [" ".join(f"{a} {b}".split()) for a, b in zip(table.iloc[0], second)],
                FFTODAY_IDP if idp else FFTODAY,
            )

            body = table.iloc[2:].replace("%", "", regex=True).reset_index(drop=True)
            body.columns = names[: body.shape[1]]
            frame = to_numeric_frame(body, exclude=("id",))
            frame["pos"] = position
            frame["data_src"] = "FFToday"
            frame["src_id"] = site_ids[: len(frame)]
            frame = frame.drop(columns="chg", errors="ignore")

            if week > 0 and "opp" in frame.columns:
                frame["opp"] = frame["opp"].astype(str).str.replace("@", "", regex=False)
            if "bye" in frame.columns:
                frame["bye"] = pd.to_numeric(
                    frame["bye"].astype(str).str.replace("-", "", regex=False),
                    errors="coerce",
                ).astype("Int64")

            if position == "DST":
                frame["id"] = resolve_ids(frame["src_id"], "fftoday_id",
                                          pos="DST").to_numpy()
            else:
                frame["id"] = resolve_ids(
                    frame["src_id"], "fftoday_id",
                    name=frame["player"], pos=frame["pos"], team=frame.get("team"),
                ).to_numpy()
            pages.append(frame)

        if not pages:
            return pd.DataFrame()
        return pd.concat(pages, ignore_index=True)

    return for_each_position(positions, scrape_one)
