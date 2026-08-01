"""Average draft position and auction value.

Where the projections say what a player is worth, these say what a draft room
will actually charge you for them.  Seven sites publish one or both; each
function below returns ``id`` plus the metric, and :func:`get_adp` averages
whichever ones answered.
"""

from __future__ import annotations

import posixpath
import re
from typing import Sequence

import numpy as np
import pandas as pd

from .players import TEAM_CORRECTIONS, resolve_ids
from .season import current_season
from .sources._http import Session, html_table, local_json, polite_pause
from .sources.columns import ESPN_TEAM_ABBREVIATIONS

__all__ = ["get_adp", "ADP_SOURCES", "AAV_SOURCES"]

#: Sites that publish draft position, and those that publish auction value.
ADP_SOURCES = ("RTS", "CBS", "Yahoo", "NFL", "FFC", "MFL", "ESPN")
AAV_SOURCES = ("RTS", "Yahoo", "MFL", "ESPN")


def rts_draft(metric: str = "adp") -> pd.DataFrame:
    """RTSports."""
    saved = local_json("aav") if metric == "aav" else \
        local_json("adp", "adp-aav-provider")
    if saved is not None:
        path, payload = saved
        print(f"  RTS {metric.upper()}: {path} (local copy)")
    else:
        url = ("https://www.freedraftguide.com/football/adp-aav-provider.php"
               "?NUM=&STYLE=0&AAV=" + ("YES" if metric == "aav" else ""))
        payload = Session().json(url)
    players = payload["player_list"]
    frame = pd.DataFrame(
        list(players.values()) if isinstance(players, dict) else players
    )
    return pd.DataFrame({
        "id": resolve_ids(frame["player_id"].astype(str), "rts_id",
                          name=frame["name"], team=frame["team"],
                          pos=frame["position"]).to_numpy(),
        metric: pd.to_numeric(frame["avg"], errors="coerce"),
    })


def cbs_draft(metric: str = "adp") -> pd.DataFrame:
    """CBS Sports (draft position only)."""
    page = Session().html(
        "https://www.cbssports.com/fantasy/football/draft/averages/both/h2h/all"
    )
    site_ids = [
        posixpath.basename(posixpath.dirname(posixpath.dirname(a.get("href", ""))))
        for a in page.cssselect("span.CellPlayerName--long > span > a")
    ]
    table = html_table(page.cssselect("#TableBase > div > div > table")[0],
                       header=True)
    parts = table["Player"].str.extract(r"\n\s+(.*?)\n\s+([A-Z]{1,3})\s+([A-Z]{2,3})")
    frame = pd.DataFrame({
        "id": resolve_ids(pd.Series(site_ids[: len(table)]), "cbs_id",
                          name=parts[0], pos=parts[1], team=parts[2]).to_numpy(),
        "adp": pd.to_numeric(table["Avg Pos"], errors="coerce"),
    })
    return frame


def yahoo_draft(metric: str = "adp") -> pd.DataFrame:
    """Yahoo's public fantasy API."""
    url = (
        "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2/league/"
        "470.l.public;out=settings/players;position=ALL;start=0;count=200;"
        "sort=average_cost;search=;out=auction_values,ranks;ranks=season;"
        "ranks_by_position=season;out=expert_ranks;"
        "expert_ranks.rank_type=projected_season_remaining/draft_analysis;"
        "cut_types=diamond;slices=last7days?format=json_f"
    )
    payload = Session().json(url, headers={
        "Accept": "*/*",
        "Origin": "https://football.fantasysports.yahoo.com",
    })
    players = payload["fantasy_content"]["league"]["players"]
    rows = []
    for entry in (players.values() if isinstance(players, dict) else players):
        player = entry["player"]
        analysis = player.get("draft_analysis", {})
        rows.append({
            "yahoo_id": player["player_id"],
            "name": player["name"]["full"],
            "team": player.get("editorial_team_abbr"),
            "pos": player["eligible_positions"][0]["position"],
            "adp": analysis.get("average_pick"),
            "aav": analysis.get("average_cost"),
        })
    frame = pd.DataFrame(rows).replace("-", np.nan)
    team = frame["team"].astype("string").str.upper()
    frame["team"] = team.replace(TEAM_CORRECTIONS)
    frame["id"] = resolve_ids(frame["yahoo_id"].astype(str), "stats_id",
                              name=frame["name"], pos=frame["pos"],
                              team=frame["team"]).to_numpy()
    frame = frame
    return pd.DataFrame({
        "id": frame["id"],
        metric: pd.to_numeric(frame[metric], errors="coerce"),
    })


def nfl_draft(metric: str = "adp") -> pd.DataFrame:
    """NFL.com's draft centre (draft position only)."""
    url = (
        "https://fantasy.nfl.com/draftcenter/breakdown?leagueId=&offset=1"
        f"&count=200&position=all&season={current_season()}"
        "&sort=draftAveragePosition"
    )
    page = Session().html(url)
    table = html_table(page.cssselect("tbody")[0], header=False)
    parts = table["X1"].str.extract(r"(.*?)\s+([A-Z]{2,3}).*?([A-Z]{2,3}).*")

    seen, site_ids = set(), []
    for anchor in page.cssselect("tbody > tr > td > div > a"):
        href = anchor.get("href", "")
        if href not in seen:
            seen.add(href)
            site_ids.append(re.sub(r".*playerId=", "", href))

    frame = pd.DataFrame({
        "id": resolve_ids(pd.Series(site_ids[: len(table)]), "nfl_id",
                          name=parts[0], pos=parts[1], team=parts[2]).to_numpy(),
        "adp": pd.to_numeric(table["X2"], errors="coerce"),
    })
    return frame


def mfl_draft(metric: str = "adp", n_teams: int = 12) -> pd.DataFrame:
    """MyFantasyLeague.  Auction values are rescaled to a $200 budget."""
    season = current_season()
    if metric == "aav":
        url = (f"https://api.myfantasyleague.com/{season}/export?TYPE=aav"
               "&PERIOD=RECENT&IS_PPR=-1&IS_KEEPER=N&JSON=1")
        key = "averageValue"
    else:
        url = (f"https://api.myfantasyleague.com/{season}/export?TYPE=adp"
               f"&PERIOD=RECENT&FCOUNT={n_teams}&IS_PPR=-1&IS_KEEPER=N"
               "&IS_MOCK=0&CUTOFF=10&DETAILS=&JSON=1")
        key = "averagePick"

    frame = pd.DataFrame(Session().json(url)[metric]["player"])
    out = pd.DataFrame({
        "id": frame["id"].astype(str),
        metric: pd.to_numeric(frame[key], errors="coerce"),
    })
    if metric == "aav":
        # MFL reports a share of $1000 across the league.
        out["aav"] = out["aav"] * (200 / (1000 / n_teams))
    frame = out
    return frame


def ffc_draft(metric: str = "adp", n_teams: int = 12,
              scoring: str = "standard") -> pd.DataFrame:
    """Fantasy Football Calculator (draft position only)."""
    url = (f"https://fantasyfootballcalculator.com/api/v1/adp/{scoring}"
           f"?teams={n_teams}&year={current_season()}&position=all")
    frame = pd.DataFrame(Session().json(url)["players"])
    frame = pd.DataFrame({
        "id": resolve_ids(name=frame["name"], team=frame["team"],
                          pos=frame["position"]).to_numpy(),
        "adp": pd.to_numeric(frame["adp"], errors="coerce"),
    })
    return frame


def espn_draft(metric: str = "adp") -> pd.DataFrame:
    """ESPN."""
    season = current_season()
    slots = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "K": 17, "DST": 16}
    limits = {"QB": 60, "RB": 120, "WR": 160, "TE": 80, "K": 40, "DST": 32}
    session = Session()
    frames = []

    for index, (position, slot) in enumerate(slots.items()):
        if index:
            polite_pause()
        url = (
            "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
            f"{season}/segments/0/leaguedefaults/3"
            "?scoringPeriodId=0&view=kona_player_info"
        )
        payload = session.json(url, headers={
            "Accept": "application/json",
            "X-Fantasy-Source": "kona",
            "X-Fantasy-Filter": (
                '{"players":{'
                f'"filterSlotIds":{{"value":[{slot}]}},'
                '"filterStatsForSourceIds":{"value":[1]},'
                '"filterStatsForSplitTypeIds":{"value":[0]},'
                '"sortDraftRanks":{"sortPriority":2,"sortAsc":true,"value":"PPR"},'
                f'"limit":{limits[position]},"offset":0}}}}'
            ),
        })["players"]

        rows = []
        for entry in payload:
            player = entry.get("player") or {}
            ownership = player.get("ownership") or {}
            rows.append({
                "espn_id": str(entry.get("id")),
                "name": player.get("fullName"),
                "team": ESPN_TEAM_ABBREVIATIONS.get(str(player.get("proTeamId"))),
                "pos": position,
                "adp": ownership.get("averageDraftPosition"),
                "aav": ownership.get("auctionValueAverage"),
            })

        frame = pd.DataFrame(rows)
        if frame.empty:
            continue
        if position == "DST":
            frame["id"] = resolve_ids(pos="DST", team=frame["team"]).to_numpy()
        else:
            frame["id"] = resolve_ids(frame["espn_id"], "espn_id",
                                      name=frame["name"], pos=frame["pos"],
                                      team=frame["team"]).to_numpy()
        frames.append(frame)

    frame = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if frame.empty:
        return frame
    return pd.DataFrame({
        "id": frame["id"],
        metric: pd.to_numeric(frame[metric], errors="coerce").round(1),
    })


_BUILDERS = {
    "RTS": rts_draft, "CBS": cbs_draft, "Yahoo": yahoo_draft, "NFL": nfl_draft,
    "FFC": ffc_draft, "MFL": mfl_draft, "ESPN": espn_draft,
}


def get_adp(sources: Sequence[str] = ADP_SOURCES,
            metric: str = "adp") -> pd.DataFrame:
    """Average draft position (or auction value) across several sites.

    Sources that error out are reported and left out; you get the average of
    whatever answered, plus how much they disagreed.
    """
    metric = str(metric).lower()
    if metric not in ("adp", "aav"):
        raise ValueError("metric must be 'adp' or 'aav'")

    allowed = AAV_SOURCES if metric == "aav" else ADP_SOURCES
    chosen = [s for s in ([sources] if isinstance(sources, str) else sources)
              if s in allowed]

    collected = []
    for name in chosen:
        try:
            frame = _BUILDERS[name](metric=metric)
        except Exception as error:  # noqa: BLE001 - a dead site is not fatal here
            print(f"  ! {name} {metric.upper()}: {type(error).__name__}: {error}")
            continue
        frame = frame[frame["id"].notna()][["id", metric]]
        collected.append(frame.rename(columns={metric: f"{metric}_{name.lower()}"})
                         .drop_duplicates("id"))

    if not collected:
        return pd.DataFrame(columns=["id", metric, f"{metric}_sd"])

    out = collected[0]
    for frame in collected[1:]:
        out = out.merge(frame, on="id", how="outer")

    values = out.drop(columns="id")
    out[metric] = values.mean(axis=1, skipna=True).round(1)
    out[f"{metric}_sd"] = values.std(axis=1, ddof=1, skipna=True).round(2)
    return out.sort_values(metric, ascending=(metric == "adp")).reset_index(drop=True)
