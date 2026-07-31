"""FleaFlicker projections.  Weekly only, paginated twenty rows at a time."""

from __future__ import annotations

import re

import pandas as pd

from ..players import resolve_ids
from ..stats import to_numeric_frame
from ._http import Session, for_each_position, html_table, polite_pause
from .columns import FLEAFLICKER, FLEAFLICKER_IDP, rename

__all__ = ["scrape_fleaflicker"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB")
DRAFT = False
WEEKLY = True

_POSITION_IDS = {"QB": 4, "RB": 1, "WR": 2, "TE": 8, "K": 16, "DST": 256,
                 "DE": 2048, "DT": 64, "LB": 128, "CB": 512, "S": 1024}
_PAGES = {"K": 2, "DST": 2, "QB": 2, "DT": 4, "TE": 5,
          "DE": 6, "LB": 6, "S": 6, "RB": 6, "CB": 6, "WR": 6}
#: The site splits the line and the secondary; we report them merged.
_MERGED = {"DL": ("DE", "DT"), "DB": ("CB", "S")}
_PAGE_SIZE = 20

_DST = re.compile(r"(.*)\s+D/ST\s+([A-Z]{2,3}).*?(\d+).*")
_PLAYER = re.compile(r"(.*?)\s+(.*?)\s+(.*?)\s+(.*?)\s+.*(\d+)\)$")


def scrape_fleaflicker(positions=POSITIONS, season=None, week=0,
                       **_) -> dict[str, pd.DataFrame]:
    if week == 0:
        print("  FleaFlicker: weekly projections only, skipping")
        return {}

    requested = list(positions)
    site_positions = []
    for position in requested:
        site_positions.extend(_MERGED.get(position, (position,)))

    session = Session("https://www.fleaflicker.com/nfl/leaders")

    def scrape_one(position: str) -> pd.DataFrame:
        idp = position in ("DE", "DT", "LB", "CB", "S")
        pages, offset = [], 0

        for page_number in range(_PAGES[position]):
            url = (
                f"https://www.fleaflicker.com/nfl/leaders?week={week}&statType=7"
                f"&sortMode=7&position={_POSITION_IDS[position]}&tableOffset={offset}"
            )
            if page_number == 0:
                print(f"  FleaFlicker {position}: {url}")
            else:
                polite_pause()

            page = session.html(url)
            tables = page.cssselect("#body-center-main table")
            if not tables:
                break

            table = html_table(tables[0], header=True)
            # Drop the pagination row at the foot of the table.
            table = table[~table.apply(
                lambda row: row.astype(str).str.contains("Previous.*Next", regex=True).all(),
                axis=1,
            )]
            if table.empty:
                break

            names = [
                " ".join(re.sub(r"Week\s+\d+|Projected", "", f"{group} {stat}").split())
                for group, stat in zip(table.columns, table.iloc[0])
            ]
            if position == "K":
                for index, name in zip((9, 10, 12, 13),
                                       ("fg_att", "fg_pct", "xp_att", "xp_pct")):
                    if index < len(names):
                        names[index] = name
            names = rename(names, FLEAFLICKER_IDP if idp else FLEAFLICKER)
            names = [name or f"unnamed_{i}" for i, name in enumerate(names)]

            body = table.iloc[1:].reset_index(drop=True)
            body.columns = names[: body.shape[1]]
            body = to_numeric_frame(body.replace(["—", "NA", ""], pd.NA))
            body = body[[c for c in body.columns if body[c].notna().any()]]

            site_ids = [
                re.sub(r".*-(\d+)$", r"\1", link.get("href", ""))
                for link in page.cssselect("a.player-text")
            ]
            body["data_src"] = "FleaFlicker"
            body["src_id"] = site_ids[: len(body)]

            if position == "DST":
                parts = body["player"].str.extract(_DST)
                body["player"], body["team"], body["bye"] = parts[0], parts[1], parts[2]
                body["pos"] = "DST"
                body["id"] = resolve_ids(
                    body["src_id"], "fleaflicker_id", pos="DST", team=body["team"]
                ).to_numpy()
            else:
                body["player"] = body["player"].str.replace(r"^Q(?=[A-Z])", "", regex=True)
                parts = body["player"].str.extract(_PLAYER)
                body["player"] = parts[0] + " " + parts[1]
                body["pos"], body["team"], body["bye"] = parts[2], parts[3], parts[4]
                body["id"] = resolve_ids(
                    body["src_id"], "fleaflicker_id",
                    name=body["player"], first=parts[0], last=parts[1],
                    pos=body["pos"], team=body["team"],
                ).to_numpy()

            pages.append(body)

            site_points = pd.to_numeric(body.get("site_pts"), errors="coerce")
            if site_points is None or site_points.min() <= 1 or len(body) < _PAGE_SIZE:
                break
            offset += _PAGE_SIZE

        if not pages:
            return pd.DataFrame()
        return pd.concat(pages, ignore_index=True)

    scraped = for_each_position(site_positions, scrape_one)

    for merged, parts in _MERGED.items():
        available = [scraped.pop(part) for part in parts if part in scraped]
        if available:
            frame = pd.concat(available, ignore_index=True)
            frame["pos"] = merged
            scraped[merged] = frame.drop_duplicates(subset="src_id", keep="first")

    return {position: scraped[position] for position in requested if position in scraped}
