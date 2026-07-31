"""Average draft position and auction value.

Ported from ``R/adp_functions.R``.  Each source has its own function, and
:func:`get_adp` combines several of them.
"""

from __future__ import annotations

import posixpath
import re
from typing import Sequence

import numpy as np
import pandas as pd

from .caching import cache_object, get_cached_object, is_cached
from .helper_funcs import get_mfl_id, get_scrape_year
from .rcompat.stats import rename_vec, row_sd, type_convert_frame
from .recode_vars import team_corrections
from .source_objects import espn_team_nums
from .source_scrapes._common import Session, html_table, rate_limit

__all__ = [
    "rts_draft",
    "cbs_draft",
    "yahoo_draft",
    "nfl_draft",
    "mfl_draft",
    "ffc_draft",
    "espn_draft",
    "get_adp",
]

_ADP_SOURCES = ("RTS", "CBS", "Yahoo", "NFL", "FFC", "MFL", "ESPN")
#: These sources publish draft position but no auction value.
_NO_AAV = ("CBS", "FFC", "NFL")


def _check_metric(metric: str) -> str:
    metric = str(metric).lower()
    if metric not in ("adp", "aav"):
        raise ValueError("metric must be 'adp' or 'aav'")
    return metric


# --------------------------------------------------------------------------
# Individual sources
# --------------------------------------------------------------------------

def rts_draft(metric: str = "adp") -> pd.DataFrame:
    """ADP or AAV from RTSports."""
    metric = _check_metric(metric)
    file_name = f"rts_{metric}.rds"

    if is_cached(f"RTS {metric.upper()}"):
        payload = get_cached_object(file_name)
    else:
        url = (
            "https://www.freedraftguide.com/football/adp-aav-provider.php"
            "?NUM=&STYLE=0&AAV=" + ("YES" if metric == "aav" else "")
        )
        payload = Session().get_json(url)
        cache_object(payload, file_name)

    players = payload["player_list"]
    frame = pd.DataFrame(list(players.values()) if isinstance(players, dict) else players)
    frame = frame.rename(columns={"player_id": "rts_id"})
    frame["bye_week"] = frame["bye_week"].replace("-", np.nan)

    out = pd.DataFrame(
        {
            "id": get_mfl_id(
                frame["rts_id"].astype(str),
                id_col_name="rts_id",
                player_name=frame["name"],
                team=frame["team"],
                pos=frame["position"],
            ).to_numpy(),
            "rts_id": frame["rts_id"],
            metric: pd.to_numeric(frame["avg"], errors="coerce"),
            "name": frame["name"],
            "team": frame["team"],
            "position": frame["position"],
            "bye_week": pd.to_numeric(frame["bye_week"], errors="coerce").astype("Int64"),
        }
    )
    if metric == "adp" and "change" in frame.columns:
        out["change"] = frame["change"]
    return out


def cbs_draft(metric: str = "adp") -> pd.DataFrame:
    """ADP from CBS Sports."""
    if is_cached("CBS ADP"):
        return get_cached_object("cbs_adp.rds")

    session = Session()
    page = session.read_html(
        "https://www.cbssports.com/fantasy/football/draft/averages/both/h2h/all"
    )

    cbs_id = [
        posixpath.basename(posixpath.dirname(posixpath.dirname(a.get("href", ""))))
        for a in page.cssselect("span.CellPlayerName--long > span > a")
    ]

    table = html_table(page.cssselect("#TableBase > div > div > table")[0], header=True)
    extracted = table["Player"].str.extract(
        r"\n\s+(.*?)\n\s+([A-Z]{1,3})\s+([A-Z]{2,3})"
    )

    out = pd.DataFrame(
        {
            "id": get_mfl_id(
                pd.Series(cbs_id[: len(table)]),
                id_col_name="cbs_id",
                player_name=extracted[0],
                pos=extracted[1],
                team=extracted[2],
            ).to_numpy(),
            "cbs_id": cbs_id[: len(table)],
            "player": extracted[0],
            "pos": extracted[1],
            "team": extracted[2],
            "change": pd.to_numeric(
                table["Trend"].replace("—", 0), errors="coerce"
            ).astype("Int64"),
            "adp": pd.to_numeric(table["Avg Pos"], errors="coerce"),
            "high_adp": pd.to_numeric(
                table["Hi/Lo"].str.replace(r"/\d+", "", regex=True), errors="coerce"
            ).astype("Int64"),
            "low_adp": pd.to_numeric(
                table["Hi/Lo"].str.replace(r"\d+/", "", regex=True), errors="coerce"
            ).astype("Int64"),
            "percent_drafted": table["Pct"],
        }
    )
    cache_object(out, "cbs_adp.rds")
    return out


def yahoo_draft(metric: str = "adp") -> pd.DataFrame:
    """ADP or AAV from Yahoo's public fantasy API."""
    metric = _check_metric(metric)
    columns = (
        ["aav", "projected_av", "percent_drafted"] if metric == "aav"
        else ["adp", "percent_drafted"]
    )
    keep = ["id", "yahoo_id", "player_name", "team", "pos"] + columns

    if is_cached("Yahoo ADP/AAV"):
        return get_cached_object("yahoo_adp_aav.rds")[keep]

    url = (
        "https://pub-api-ro.fantasysports.yahoo.com/fantasy/v2/league/"
        "470.l.public;out=settings/players;position=ALL;start=0;count=200;"
        "sort=average_cost;search=;out=auction_values,ranks;ranks=season;"
        "ranks_by_position=season;out=expert_ranks;"
        "expert_ranks.rank_type=projected_season_remaining/draft_analysis;"
        "cut_types=diamond;slices=last7days?format=json_f"
    )
    payload = Session().get_json(
        url,
        headers={
            "Accept": "*/*",
            "Origin": "https://football.fantasysports.yahoo.com",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )

    players = payload["fantasy_content"]["league"]["players"]
    rows = []
    for entry in players.values() if isinstance(players, dict) else players:
        player = entry["player"]
        analysis = player.get("draft_analysis", {})
        rows.append(
            {
                "player_name": player["name"]["full"],
                "yahoo_id": player["player_id"],
                "team": player.get("editorial_team_abbr"),
                "pos": player["eligible_positions"][0]["position"],
                "adp": analysis.get("average_pick"),
                "percent_drafted": analysis.get("percent_drafted"),
                "aav": analysis.get("average_cost"),
                "projected_av": player.get("projected_auction_value"),
            }
        )

    frame = pd.DataFrame(rows).replace("-", np.nan)
    frame = type_convert_frame(frame, exclude=("player_name", "yahoo_id", "pos"))
    frame["team"] = rename_vec(
        frame["team"].astype("string").str.upper().tolist(), team_corrections
    )
    frame["id"] = get_mfl_id(
        frame["yahoo_id"].astype(str),
        id_col_name="stats_id",
        player_name=frame["player_name"],
        pos=frame["pos"],
        team=frame["team"],
    ).to_numpy()
    frame["percent_drafted"] = pd.to_numeric(
        frame["percent_drafted"], errors="coerce"
    ) * 100

    cache_object(frame, "yahoo_adp_aav.rds")
    return frame[keep]


def nfl_draft(metric: str = "adp") -> pd.DataFrame:
    """ADP from NFL.com's draft centre."""
    if is_cached("NFL ADP"):
        return get_cached_object("nfl_adp.rds")

    year = get_scrape_year()
    url = (
        "https://fantasy.nfl.com/draftcenter/breakdown?leagueId=&offset=1&count=200"
        f"&position=all&season={year}&sort=draftAveragePosition"
    )
    page = Session().read_html(url)

    table = html_table(page.cssselect("tbody")[0], header=False)
    extracted = table["X1"].str.extract(r"(.*?)\s+([A-Z]{2,3}).*?([A-Z]{2,3}).*")

    seen, nfl_id = set(), []
    for anchor in page.cssselect("tbody > tr > td > div > a"):
        href = anchor.get("href", "")
        if href not in seen:
            seen.add(href)
            nfl_id.append(re.sub(r".*playerId=", "", href))

    out = pd.DataFrame(
        {
            "player": extracted[0],
            "pos": extracted[1],
            "team": extracted[2],
            "adp": table["X2"],
            "avg_round": table["X3"],
            "average_salary": table["X4"],
        }
    )
    out["nfl_id"] = nfl_id[: len(out)]
    out["id"] = get_mfl_id(
        out["nfl_id"],
        id_col_name="nfl_id",
        player_name=out["player"],
        pos=out["pos"],
        team=out["team"],
    ).to_numpy()
    out = type_convert_frame(out, exclude=("id", "nfl_id", "player", "pos", "team"))

    cache_object(out, "nfl_adp.rds")
    return out


def mfl_draft(metric: str = "adp", period: str = "RECENT", format: str = "All Leagues",
              nteams: int = 12, is_keeper: str = "No", is_mock: str = "No",
              cutoff: int = 10) -> pd.DataFrame:
    """ADP or AAV from MyFantasyLeague.

    ``period`` restricts to drafts after a point in the offseason, ``format``
    picks the reception scoring, and ``cutoff`` requires a player to appear in
    at least that percentage of drafts.
    """
    metric = _check_metric(metric)
    file_name = f"mfl_{metric}.rds"

    default_arguments = (
        period == "RECENT" and int(nteams) == 12 and format == "All Leagues"
        and is_keeper == "No" and is_mock == "No" and int(cutoff) == 10
    )
    if default_arguments and is_cached(f"MFL {metric.upper()}"):
        return get_cached_object(file_name)

    ppr = {"All Leagues": -1, "PPR": 1, "Std": 0}[format]
    keeper = is_keeper[0]
    mock = {"No": 0, "Mock": 1, "All Leagues": -1}[is_mock]
    year = get_scrape_year()

    if metric == "aav":
        url = (
            f"https://api.myfantasyleague.com/{year}/export?TYPE={metric}"
            f"&PERIOD={period}&IS_PPR={ppr}&IS_KEEPER={keeper}&JSON=1"
        )
        columns = {
            "id": "id", "averageValue": "aav", "minValue": "min_av",
            "maxValue": "max_av", "auctionSelPct": "draft_percentage",
        }
    else:
        url = (
            f"https://api.myfantasyleague.com/{year}/export?TYPE={metric}"
            f"&PERIOD={period}&FCOUNT={int(nteams)}&IS_PPR={ppr}&IS_KEEPER={keeper}"
            f"&IS_MOCK={mock}&CUTOFF={int(cutoff)}&DETAILS=&JSON=1"
        )
        columns = {
            "id": "id", "averagePick": "adp", "minPick": "min_dp",
            "maxPick": "max_dp", "draftSelPct": "draft_percentage",
        }

    payload = Session().get_json(url)
    frame = pd.DataFrame(payload[metric]["player"])
    frame = frame[list(columns)].rename(columns=columns)
    frame = type_convert_frame(frame, exclude=("id",))
    frame["id"] = frame["id"].astype(str)

    if metric == "aav":
        # MFL splits $1000 across the league; rescale to a ~$200 budget per team.
        frame["aav"] = frame["aav"] * (200 / (1000 / int(nteams)))

    if default_arguments:
        cache_object(frame, file_name)
    return frame


def ffc_draft(format: str = "standard", pos: str = "all", n_teams: str = "12",
              metric: str = "adp") -> pd.DataFrame:
    """ADP from fantasyfootballcalculator.com."""
    default_arguments = str(n_teams) == "12" and format == "standard" and pos == "all"
    if default_arguments and is_cached("FFC ADP"):
        return get_cached_object("ffc_adp.rds")

    url = (
        f"https://fantasyfootballcalculator.com/api/v1/adp/{format}"
        f"?teams={n_teams}&year={get_scrape_year()}&position={pos}"
    )
    payload = Session().get_json(url)
    frame = pd.DataFrame(payload["players"])

    out = pd.DataFrame(
        {
            "id": get_mfl_id(
                player_name=frame["name"], team=frame["team"], pos=frame["position"]
            ).to_numpy(),
            "ffc_id": frame["player_id"],
            "player": frame["name"],
            "pos": frame["position"],
            "team": frame["team"],
            "adp": frame["adp"],
        }
    )
    if default_arguments:
        cache_object(out, "ffc_adp.rds")
    return out


def espn_draft(metric: str = "adp") -> pd.DataFrame:
    """ADP or AAV from ESPN."""
    metric = _check_metric(metric)

    if is_cached("ESPN ADP/AAV"):
        frames = get_cached_object("espn_adp_aav.rds")
    else:
        season = get_scrape_year()
        slots = {"QB": 0, "RB": 2, "WR": 4, "TE": 6, "K": 17, "DST": 16}
        limits = {"QB": 42, "RB": 100, "WR": 150, "TE": 60, "K": 35, "DST": 32}
        session = Session()
        frames = []

        for index, (position, slot) in enumerate(slots.items()):
            if index:
                rate_limit()

            url = (
                "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
                f"{season}/segments/0/leaguedefaults/3"
                "?scoringPeriodId=0&view=kona_player_info"
            )
            fantasy_filter = (
                '{"players":{'
                f'"filterSlotIds":{{"value":[{slot}]}},'
                '"filterStatsForSourceIds":{"value":[1]},'
                '"filterStatsForSplitTypeIds":{"value":[0]},'
                '"sortAppliedStatTotal":{"sortAsc":false,"sortPriority":3,'
                f'"value":"11{season}0"}},'
                '"sortDraftRanks":{"sortPriority":2,"sortAsc":true,"value":"PPR"},'
                '"sortPercOwned":{"sortAsc":false,"sortPriority":4},'
                f'"limit":{limits[position]},'
                '"offset":0,'
                '"filterRanksForScoringPeriodIds":{"value":[2]},'
                '"filterRanksForRankTypes":{"value":["PPR"]},'
                '"filterRanksForSlotIds":{"value":[0,2,4,6,17,16]},'
                '"filterStatsForTopScoringPeriodIds":{"value":2,'
                f'"additionalValue":["00{season}","10{season}","11{season}0",'
                f'"02{season}"]}}}}}}'
            )

            payload = session.get_json(
                url,
                headers={
                    "Accept": "application/json",
                    "X-Fantasy-Source": "kona",
                    "X-Fantasy-Filter": fantasy_filter,
                },
            )["players"]

            rows = []
            for entry in payload:
                ownership = entry.get("player", {}).get("ownership") or {}
                rows.append(
                    {
                        "aav": _round1(ownership.get("auctionValueAverage")),
                        "adp": _round1(ownership.get("averageDraftPosition")),
                        "percent_owned": _round1(ownership.get("percentOwned")),
                        "espn_id": entry.get("id"),
                        "player": entry.get("player", {}).get("fullName"),
                        "team": espn_team_nums.get(
                            str(entry.get("player", {}).get("proTeamId"))
                        ),
                        "pos": position,
                    }
                )

            frame = pd.DataFrame(rows)
            if frame.empty:
                continue
            if position == "DST":
                frame["id"] = get_mfl_id(team=frame["team"], pos=frame["pos"]).to_numpy()
            else:
                frame["id"] = get_mfl_id(
                    frame["espn_id"].astype(str),
                    id_col_name="espn_id",
                    player_name=frame["player"],
                    pos=frame["pos"],
                ).to_numpy()
            frame["espn_id"] = frame["espn_id"].astype(str)
            frames.append(
                frame[["id", "espn_id", "pos", "player", "team", "adp", "aav",
                       "percent_owned"]]
            )

        cache_object(frames, "espn_adp_aav.rds")

    if not frames:
        return pd.DataFrame()

    out = pd.concat(frames, ignore_index=True)
    other = "aav" if metric == "adp" else "adp"
    return out.drop(columns=other).sort_values(metric, ascending=(metric == "adp"))


def _round1(value):
    return None if value is None else round(value, 1)


# --------------------------------------------------------------------------
# Combining sources
# --------------------------------------------------------------------------

_SOURCE_FUNCTIONS = {
    "RTS": rts_draft,
    "CBS": cbs_draft,
    "Yahoo": yahoo_draft,
    "NFL": nfl_draft,
    "FFC": ffc_draft,
    "MFL": mfl_draft,
    "ESPN": espn_draft,
}


def get_adp(sources: Sequence[str] = _ADP_SOURCES, metric: str = "adp"):
    """Average draft position (or auction value) across several sources.

    Full-joins the sources on player id and appends ``<metric>_avg`` and
    ``<metric>_sd``.  A source that errors is reported and left out rather than
    failing the whole call.  Returns ``None`` if no source produced data.
    """
    metric = _check_metric(metric)
    if isinstance(sources, str):
        sources = [sources]

    unknown = [s for s in sources if s not in _SOURCE_FUNCTIONS]
    if unknown:
        raise ValueError(
            f"unknown ADP source(s) {unknown}; choose from {list(_SOURCE_FUNCTIONS)}"
        )

    if metric == "aav":
        sources = [s for s in sources if s not in _NO_AAV]

    collected = []
    for name in sources:
        try:
            frame = _SOURCE_FUNCTIONS[name](metric=metric)
            frame = frame[["id", metric]].rename(columns={metric: f"{metric}_{name.lower()}"})
            collected.append(frame[frame["id"].notna()])
        except Exception as error:  # noqa: BLE001 - mirrors R's per-source tryCatch
            print(
                f" Error with the {name.upper()} {metric.upper()} data. "
                f"It is not included in the table or summary columns\n   {error!r}"
            )

    if not collected:
        return None
    if len(collected) == 1:
        return collected[0]

    out = collected[0]
    for frame in collected[1:]:
        out = out.merge(frame, on="id", how="outer")

    values = out.drop(columns="id")
    out[f"{metric}_avg"] = values.mean(axis=1, skipna=True)
    out[f"{metric}_sd"] = row_sd(values, na_rm=True)
    return out.sort_values(f"{metric}_avg", ascending=(metric == "adp"))
