"""FantasyPros projections.

FantasyPros publishes a *consensus* of the other sites, so it is not part of
the default source list -- averaging it with its own inputs would double-count
them.  It is also the one site that caps its public projections table (ten rows
a position at the time of writing), which is another reason not to lean on it.
Its expert consensus rankings, which do come through in full, are scraped
separately by :mod:`ffanalytics.ecr`.
"""

from __future__ import annotations

import re

import pandas as pd

from ..players import resolve_ids
from ..stats import to_numeric_frame
from ._http import Session, for_each_position, header_rows, html_table, polite_pause
from .columns import FANTASYPROS, rename

__all__ = ["scrape_fantasypros"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
DRAFT = True
WEEKLY = True

_PLAYER = re.compile(r"(.*)\s+([A-Z]{2,3})")


def scrape_fantasypros(positions=POSITIONS, season=None, week=0,
                       **_) -> dict[str, pd.DataFrame]:
    period = f"week={week}" if week > 0 else "week=draft"
    session = Session("https://www.fantasypros.com/nfl/projections")

    def scrape_one(position: str) -> pd.DataFrame:
        url = f"https://www.fantasypros.com/nfl/projections/{position.lower()}.php?{period}"
        print(f"  FantasyPros {position}: {url}")
        page = session.html(url)
        polite_pause()

        heads = page.cssselect("table > thead")
        bodies = page.cssselect("table > tbody")
        if not heads or not bodies:
            return pd.DataFrame()

        if position in ("K", "DST"):
            names = header_rows(heads[0])[-1].split("\t")
        else:
            # Two header rows: stat group over stat.
            grouped = html_table(heads[0], header=False)
            names = [" ".join(f"{a} {b}".split())
                     for a, b in zip(grouped.iloc[0], grouped.iloc[1])]
        names = rename(names, FANTASYPROS)

        site_ids = [
            re.sub(r".*?(\d{4,6}).*", r"\1", row.get("class") or "")
            for row in page.cssselect("table > tbody > tr")
            if row.get("class")
        ]

        frame = html_table(bodies[0], header=False).replace(",", "", regex=True)
        frame.columns = names[: frame.shape[1]]
        frame["src_id"] = site_ids[: len(frame)]
        frame["data_src"] = "FantasyPros"
        frame["pos"] = position

        if position == "DST":
            frame["id"] = resolve_ids(frame["src_id"], "fantasypro_num_id").to_numpy()
        else:
            parts = frame["player"].str.extract(_PLAYER)
            frame["player"], frame["team"] = parts[0], parts[1]
            frame["id"] = resolve_ids(
                frame["src_id"], "fantasypro_num_id",
                name=frame["player"], pos=frame["pos"], team=frame["team"],
            ).to_numpy()

        frame = to_numeric_frame(frame, exclude=("id", "src_id"))
        return frame[frame["site_pts"] > 0]

    return for_each_position(positions, scrape_one)
