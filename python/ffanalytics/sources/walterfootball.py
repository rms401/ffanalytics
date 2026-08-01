"""WalterFootball projections, published as one spreadsheet a year."""

from __future__ import annotations

import io
import re

import pandas as pd
import requests

from ..players import resolve_ids
from ._http import USER_AGENT, for_each_position
from .columns import WALTERFOOTBALL, rename

__all__ = ["scrape_walterfootball"]

POSITIONS = ("QB", "RB", "WR", "TE", "K")
DRAFT = True
WEEKLY = False

_SHEETS = {"QB": "QBs", "RB": "RBs", "WR": "WRs", "TE": "TEs", "K": "Ks"}
_KEEP = re.compile(
    r"^Pass|^Rush|^Catch|^Rec|^Reg TD$|^Int|^FG|^XP|name$|^player|^Team$|^Pos|^Bye",
    re.IGNORECASE,
)


def scrape_walterfootball(positions=POSITIONS, season=None, week=0,
                          **_) -> dict[str, pd.DataFrame]:
    if week > 0:
        print("  WalterFootball: season-long projections only, skipping")
        return {}

    url = f"http://walterfootball.com/fantasy{season}rankingsexcel.xlsx"
    print(f"  WalterFootball: {url}")
    response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
    response.raise_for_status()
    workbook = io.BytesIO(response.content)

    def scrape_one(position: str) -> pd.DataFrame:
        frame = pd.read_excel(workbook, sheet_name=_SHEETS[position])
        frame.insert(
            0, "Player",
            frame["First Name"].astype(str).str.strip() + " "
            + frame["Last Name"].astype(str).str.strip(),
        )
        frame = frame.loc[:, frame.notna().any()]
        frame = frame[[c for c in frame.columns if _KEEP.search(str(c))]]
        frame = frame.rename(columns={"Pos": "position", "BYE": "Bye"})

        frame["id"] = resolve_ids(
            name=frame["Player"], pos=frame.get("position"), team=frame.get("Team")
        ).to_numpy()
        frame = frame.drop(columns=["Last Name", "First Name"], errors="ignore")
        frame.columns = rename(frame.columns, WALTERFOOTBALL)
        frame["data_src"] = "WalterFootball"
        frame["pos"] = position

        # One combined touchdown number; split it by share of yardage.
        if "reg_tds" in frame.columns:
            has_rush = "rush_yds" in frame.columns
            has_rec = "rec_yds" in frame.columns
            if has_rush and has_rec:
                rush = frame["rush_yds"].fillna(0)
                total = rush + frame["rec_yds"].fillna(0)
                share = (rush / total).where(total != 0, 0)
                # No yardage figure on either side leaves the touchdowns
                # unattributable; those rows stay NaN.
                known = frame[["rush_yds", "rec_yds"]].notna().any(axis=1)
                frame["rush_tds"] = (share * frame["reg_tds"]).where(known)
                frame["rec_tds"] = ((1 - share) * frame["reg_tds"]).where(known)
                frame = frame.drop(columns="reg_tds")
            elif has_rush or has_rec:
                target = "rush_tds" if has_rush else "rec_tds"
                frame = frame.rename(columns={"reg_tds": target})

        leading = [c for c in ("id", "player", "pos", "team") if c in frame.columns]
        return frame[leading + [c for c in frame.columns if c not in leading]]

    return for_each_position(positions, scrape_one)
