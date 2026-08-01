"""CBS Sports projections."""

from __future__ import annotations

import re

import pandas as pd

from ..players import TEAM_CORRECTIONS, player_ids, resolve_ids
from ..stats import to_numeric_frame
from ._http import Session, for_each_position, header_rows, html_table, polite_pause
from .columns import CBS, rename

__all__ = ["scrape_cbs"]

POSITIONS = ("QB", "RB", "WR", "TE", "K", "DST")
DRAFT = True
WEEKLY = True

# The player cell runs "name  POS  TEAM  ..." together, separated only by the
# markup's own indentation.
_PLAYER = re.compile(r".*?\s{2,}[A-Z]{1,3}\s{2,}[A-Z]{2,3}\s{2,}(.*?)\s{2,}(.*?)\s{2,}(.*)")


def scrape_cbs(positions=POSITIONS, season=None, week=0, **_) -> dict[str, pd.DataFrame]:
    period = "restofseason" if week == 0 else week
    session = Session("https://www.cbssports.com/fantasy/football/")

    def scrape_one(position: str) -> pd.DataFrame:
        url = (
            f"https://www.cbssports.com/fantasy/football/stats/{position}/"
            f"{season}/{period}/projections/nonppr/"
        )
        print(f"  CBS {position}: {url}")
        page = session.html(url)
        polite_pause()

        head = page.cssselect("#TableBase > div > div > table > thead > tr.TableBase-headTr")
        names = re.split(r"\n|\t", header_rows(head[0])[0]) if head else []
        names = rename([n for n in names if re.search(r"[A-Z]", n)], CBS)

        body = page.cssselect("#TableBase > div > div > table > tbody")
        if not body:
            return pd.DataFrame()
        frame = html_table(body[0], header=False)
        frame.columns = names[: frame.shape[1]]

        if position == "DST":
            teams = [
                re.sub(r".*?([A-Z]{2,3}).*", r"\1", link.get("href", ""))
                for link in page.cssselect("span.TeamName a")
            ]
            frame["team"] = [TEAM_CORRECTIONS.get(t, t) for t in teams[: len(frame)]]
            frame["pos"] = "DST"
            frame["id"] = resolve_ids(pos="DST", team=frame["team"]).to_numpy()
            frame["src_id"] = frame["id"].map(player_ids().set_index("id")["cbs_id"])
        else:
            site_ids = [
                re.sub(r".*?(\d+).*", r"\1", link.get("href", ""))
                for link in page.cssselect(
                    "table > tbody > tr > td:nth-child(1) > "
                    "span.CellPlayerName--long > span > a"
                )
            ]
            parts = frame["player"].str.extract(_PLAYER)
            frame["player"], frame["pos"], frame["team"] = parts[0], parts[1], parts[2]
            frame["src_id"] = site_ids[: len(frame)]
            frame["id"] = resolve_ids(
                frame["src_id"], "cbs_id",
                name=frame["player"], pos=frame["pos"], team=frame["team"],
            ).to_numpy()

        frame["data_src"] = "CBS"
        frame = to_numeric_frame(frame.replace("—", pd.NA), exclude=("id", "src_id"))
        return frame[frame["site_pts"] > 0]

    return for_each_position(positions, scrape_one)
