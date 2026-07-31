"""Scoring sources and aggregating them into a projections table.

Ported from ``R/calc_projections.R`` -- the core of the package.
"""

from __future__ import annotations

import math
from typing import Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from .custom_scoring import make_scoring_tables
from .rcompat import stats as rst
from .rcompat.rng import RRandom
from .results import ProjectionsTable, ScrapeResult
from .scoring_rules import scoring as default_scoring
from .sysdata import pts_bracket_coefs

__all__ = [
    "default_weights",
    "default_weights_by_src",
    "default_baseline",
    "default_threshold",
    "source_points",
    "projections_table",
    "add_ecr",
    "add_adp",
    "add_aav",
    "add_uncertainty",
    "add_player_info",
    "calculate_uncertainty",
    "score_pts_bracket",
]

_POSITIONS = ("QB", "RB", "WR", "TE", "DST", "K", "DB", "DL", "LB")
_AVG_TYPES = ("average", "robust", "weighted")

#: Weight given to each source when averaging (``R/calc_projections.R:106``).
default_weights = {
    "CBS": 0.145, "Yahoo": 0.000, "ESPN": 0.157, "NFL": 0.140,
    "FFToday": 0.151, "NumberFire": 0.142, "FantasyPros": 0.000,
    "FantasySharks": 0.142, "FantasyFootballNerd": 0.000,
    "WalterFootball": 0.130, "RTSports": 0.123,
    "FantasyData": 0.000, "FleaFlicker": 0.000, "FanDuel": 0.142,
}

#: Per-position source weights (``R/calc_projections.R:115``); all 0.5 by default.
default_weights_by_src = {
    position: {source: 0.5 for source in default_weights} for position in _POSITIONS
}

#: Rank used as the replacement level for VOR (``R/calc_projections.R:169``).
default_baseline = {
    "QB": 13, "RB": 35, "WR": 36, "TE": 13, "K": 8, "DST": 3,
    "DL": 10, "LB": 10, "DB": 10,
}

#: Cohen's-d style tier thresholds (``R/calc_projections.R:280``).
default_threshold = {
    "QB": 1, "RB": 1, "WR": 1, "TE": 1, "K": 1, "DST": 0.1,
    "DL": 1, "DB": 1, "LB": 1,
}


def prep_src_weights(src_weights: Mapping | None = None) -> pd.DataFrame:
    """Long ``(pos, data_src, weights)`` frame from flat or per-position weights."""
    if src_weights is None:
        src_weights = default_weights

    first = next(iter(src_weights.values()), None)
    if not isinstance(first, Mapping):
        src_weights = {position: dict(src_weights) for position in _POSITIONS}

    rows = [
        {"pos": position, "data_src": source, "weights": weight}
        for position, weights in src_weights.items()
        for source, weight in weights.items()
    ]
    return pd.DataFrame(rows, columns=["pos", "data_src", "weights"])


# --------------------------------------------------------------------------
# DST points allowed
# --------------------------------------------------------------------------

def score_pts_bracket(points, pts_bracket: Sequence[Mapping]) -> np.ndarray:
    """Score points allowed against the league's bracket.

    Each value takes the points of the first bracket whose threshold it does
    not exceed.  A value above every threshold falls back to the *first*
    bracket, which is what R's ``max.col(..., "first")`` does with an all-false
    row (``R/calc_projections.R:173-178``).
    """
    thresholds = np.array([entry["threshold"] for entry in pts_bracket], dtype=float)
    values = np.array([entry["points"] for entry in pts_bracket], dtype=float)

    points = np.atleast_1d(np.asarray(points, dtype=float))
    matches = points[:, None] <= thresholds[None, :]
    return values[matches.argmax(axis=1)]


def score_dst_pts_allowed(data_result, pts_bracket: Sequence[Mapping],
                          is_actual: bool = False) -> pd.Series:
    """Convert DST points allowed into fantasy points.

    Weekly numbers go straight through the bracket.  Season-long projections
    are simulated: a season total implies a per-game average, and the spread
    around it comes from a per-team model, so each team's 17 games are drawn
    and scored individually and then summed.  The draw is seeded with
    ``set.seed(1)`` in R, so results are reproducible -- see
    :mod:`ffanalytics.rcompat.rng` for how that stream is reproduced here.
    """
    frame = data_result["DST"]
    allowed = pd.to_numeric(frame["dst_pts_allowed"], errors="coerce")
    known = allowed.notna()

    week = data_result.week
    season = data_result.season
    n_games = 17 if season >= 2021 else 16

    if week == 0 and not is_actual:
        coefs = pts_bracket_coefs()
        by_id = coefs.set_index("id")["team"]
        by_nfl_id = coefs.set_index("nfl_id")["team"]
        intercept_by_team = coefs.set_index("team")["Intercept"]
        season_mean_slope = float(coefs["season_mean"].iloc[0])

        ids = frame.loc[known, "id"].astype("string")
        teams = ids.map(by_id)
        teams = teams.fillna(ids.map(by_nfl_id))

        ppg = allowed[known] / n_games
        ppg_sd = teams.map(intercept_by_team) + season_mean_slope * ppg

        rng = RRandom(1)  # one continuous stream across all teams, as in R
        totals = []
        for mean, sd in zip(ppg, ppg_sd):
            draws = rng.rnorm(17)  # always consume 17 draws, so the stream lines up
            if pd.isna(mean) or pd.isna(sd):
                totals.append(np.nan)
                continue
            games = np.round(np.asarray(draws) * sd + mean)  # R rounds half to even
            games[games < 0] = 0
            totals.append(float(score_pts_bracket(games, pts_bracket).sum()))
        allowed.loc[known] = totals
    else:
        allowed.loc[known] = score_pts_bracket(allowed[known], pts_bracket)

    return allowed


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------

def source_points(data_result, scoring_rules: dict | None = None,
                  return_data_result: bool = False, is_actual: bool = False):
    """Fantasy points for every player *by source*.

    Multiplies each stat column by its scoring value and sums the row.  Unlike
    :func:`projections_table` this does no imputation, so a source that omits a
    scored column simply contributes nothing for it.

    Returns a long frame of ``pos, data_src, id, raw_points`` unless
    ``return_data_result`` is set, in which case the input is returned with a
    ``raw_points`` column added to each position.
    """
    if scoring_rules is None:
        scoring_rules = default_scoring

    scoring_objs = make_scoring_tables(scoring_rules)
    scoring_tables = scoring_objs["scoring_tables"]
    pts_bracket = scoring_objs["pts_bracket"]

    frames = {position: frame.copy() for position, frame in data_result.items()}

    if "DST" in frames and pts_bracket:
        wrapped = ScrapeResult(frames, season=data_result.season, week=data_result.week)
        frames["DST"]["dst_pts_allowed"] = score_dst_pts_allowed(
            wrapped, pts_bracket, is_actual
        )

    for position, frame in frames.items():
        scoring_table = scoring_tables.get(position)
        if scoring_table is None:
            frame["raw_points"] = np.nan
            continue

        # First match wins, as R's match() does.
        values = (
            scoring_table.drop_duplicates(subset="column", keep="first")
            .set_index("column")["val"]
        )
        columns = [column for column in frame.columns if column in values.index]
        if not columns:
            frame["raw_points"] = np.nan
            continue

        scored = frame[columns].apply(pd.to_numeric, errors="coerce") * values[columns]
        frame["raw_points"] = scored.sum(axis=1, skipna=True)

    if return_data_result:
        return ScrapeResult(frames, season=data_result.season, week=data_result.week)

    long = pd.concat(
        [frame[["pos", "data_src", "id", "raw_points"]] for frame in frames.values()],
        ignore_index=True,
    )
    return long.sort_values(["pos", "id", "data_src"]).reset_index(drop=True)


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------

def _nanmean(values) -> float:
    values = np.asarray(values, dtype=float)
    values = values[~np.isnan(values)]
    return float(values.mean()) if values.size else float("nan")


def _estimators(avg_type: str):
    """The (location, spread, quantile) triple for an averaging method.

    Every estimator takes ``(values, weights)`` and handles missing values
    itself, matching the ``na.rm = TRUE`` calls in R.
    """
    if avg_type == "average":
        return (
            lambda x, w: _nanmean(x),
            lambda x, w: rst.r_sd(x, na_rm=True),
            lambda x, w: rst.quantile_type7(x, [0.05, 0.95]),
        )
    if avg_type == "robust":
        return (
            lambda x, w: rst.wilcox_loc(x, na_rm=True),
            lambda x, w: rst.mad2(x, na_rm=True),
            lambda x, w: rst.quantile_type7(x, [0.05, 0.95]),
        )
    if avg_type == "weighted":
        return (
            lambda x, w: rst.weighted_mean(x, w, na_rm=True),
            lambda x, w: rst.weighted_sd(x, w, na_rm=True),
            lambda x, w: rst.whdquantile(x, w, [0.05, 0.95]),
        )
    raise ValueError(f"unknown avg_type {avg_type!r}")


def _league_type(scoring_rules: dict, positions: Iterable[str]) -> dict[str, str]:
    """Standard / Half / PPR per position, from the receiving scoring."""
    positions = list(positions)

    def label(points_per_reception: float) -> str:
        if points_per_reception > 0.5:
            return "PPR"
        return "Half" if points_per_reception > 0 else "Std"

    receiving = scoring_rules.get("rec", {})
    if receiving.get("all_pos"):
        if receiving.get("rec") is None:
            receiving["rec"] = 0
            print("Note: scoring_rules['rec']['rec'] not specified. "
                  "Default value is 0 (i.e., 0 PPR)")
        return {position: label(receiving["rec"]) for position in positions}

    by_position = {
        key: value["rec"]
        for key, value in receiving.items()
        if key != "all_pos" and isinstance(value, Mapping) and value.get("rec") is not None
    }
    out = {key: label(value) for key, value in by_position.items()}
    for position in positions:
        out.setdefault(position, "Std")
    return out


def projections_table(
    data_result,
    scoring_rules: dict | None = None,
    src_weights: Mapping | None = None,
    vor_baseline: Mapping | None = None,
    tier_thresholds: Mapping | None = None,
    avg_type: Sequence[str] = _AVG_TYPES,
    return_raw_stats: bool = False,
):
    """Aggregate a multi-source scrape into projected points and rankings.

    Imputes missing stats across sources, scores each source, then summarises
    each player under every requested averaging method -- ``average`` (mean),
    ``robust`` (Wilcox location and MAD) and ``weighted`` (source-weighted) --
    producing points, spread, 5th/95th percentile floor and ceiling, positional
    rank, drop-off, tier and value over replacement.

    Set ``return_raw_stats`` to aggregate the underlying *stats* instead of
    fantasy points.
    """
    scoring_rules = default_scoring if scoring_rules is None else scoring_rules
    src_weights = default_weights if src_weights is None else src_weights
    vor_baseline = default_baseline if vor_baseline is None else vor_baseline
    tier_thresholds = default_threshold if tier_thresholds is None else tier_thresholds
    avg_type = [avg_type] if isinstance(avg_type, str) else list(avg_type)

    season, week = data_result.season, data_result.week

    if len(data_result.sources()) < 3:
        print("Note: the projections table function is intended to aggregate "
              "several sources")

    lg_type = _league_type(scoring_rules, data_result.keys())

    scoring_objs = make_scoring_tables(scoring_rules)
    scoring_tables = scoring_objs["scoring_tables"]
    weights = prep_src_weights(src_weights)

    frames = {}
    for position, frame in data_result.items():
        frame = frame[frame["id"].notna()].copy()
        frame = frame.merge(weights, on=["pos", "data_src"], how="left")
        frames[position] = frame
    working = ScrapeResult(frames, season=season, week=week)

    from .impute_funcs import impute_bonus_cols, impute_via_rates_and_mean

    working = impute_via_rates_and_mean(working, scoring_objs)
    working = impute_bonus_cols(working, scoring_tables)

    if return_raw_stats:
        return _raw_stats_table(working, scoring_tables, avg_type)

    working = source_points(working, scoring_rules, return_data_result=True)

    per_type = []
    for name in avg_type:
        fun_avg, fun_sd, fun_quantile = _estimators(name)
        for position, frame in working.items():
            summary = _summarise_position(frame, position, fun_avg, fun_sd, fun_quantile)
            if summary.empty:
                continue
            summary = _rank_and_tier(summary, tier_thresholds.get(position, 1))
            summary.insert(0, "avg_type", name)
            per_type.append(summary)

    if not per_type:
        empty = pd.DataFrame(columns=[
            "avg_type", "id", "pos", "points", "sd_pts", "dropoff", "floor", "ceiling",
            "points_vor", "floor_vor", "ceiling_vor", "rank", "floor_rank",
            "ceiling_rank", "pos_rank", "tier",
        ])
        return ProjectionsTable(empty, season=season, week=week, lg_type=lg_type)

    out = pd.concat(per_type, ignore_index=True)
    out = _add_vor_and_rank(out, vor_baseline)
    return ProjectionsTable(out, season=season, week=week, lg_type=lg_type)


def _summarise_position(frame: pd.DataFrame, position: str,
                        fun_avg, fun_sd, fun_quantile) -> pd.DataFrame:
    """One row per player: points, spread and the 5th/95th percentiles."""
    rows = []
    weights_available = "weights" in frame.columns
    for player_id, group in frame.groupby("id", sort=False):
        points = pd.to_numeric(group["raw_points"], errors="coerce").to_numpy(dtype=float)
        weight = (
            pd.to_numeric(group["weights"], errors="coerce").to_numpy(dtype=float)
            if weights_available else np.ones(points.size)
        )
        quantiles = fun_quantile(points, weight)
        rows.append(
            {
                "id": player_id,
                "pos": position,
                "points": fun_avg(points, weight),
                "sd_pts": fun_sd(points, weight),
                "floor": quantiles[0],
                "ceiling": quantiles[1],
            }
        )

    summary = pd.DataFrame(rows, columns=["id", "pos", "points", "sd_pts", "floor", "ceiling"])
    if summary.empty:
        return summary

    keep = (summary["points"] > 0) & np.isfinite(summary["points"])
    return summary[keep].sort_values("points").reset_index(drop=True)


def _rank_and_tier(summary: pd.DataFrame, tier_threshold: float) -> pd.DataFrame:
    """Positional rank, drop-off to the next player, and tier cuts."""
    with np.errstate(invalid="ignore"):
        spread = summary["sd_pts"].to_numpy(dtype=float)
        spread = spread[~np.isnan(spread)]
    points_sd = float(np.median(spread)) if spread.size else np.nan

    summary = summary.copy()
    summary["pos_rank"] = rst.dense_rank(-summary["points"])
    # Computed while ascending, then carried onto the descending ordering below.
    summary["dropoff"] = summary["points"].diff().fillna(0.0)

    summary = summary.sort_values("points", ascending=False).reset_index(drop=True)

    if summary.empty or not np.isfinite(points_sd) or points_sd * tier_threshold == 0:
        summary["tier"] = 1
        return summary

    cumulative = summary["dropoff"].cumsum() - summary["dropoff"].iloc[0]
    tier = 1 + np.trunc(cumulative / (points_sd * tier_threshold))
    summary["tier"] = rst.dense_rank(tier).astype("Int64")
    return summary


def _add_vor_and_rank(out: pd.DataFrame, vor_baseline: Mapping) -> pd.DataFrame:
    """Value over replacement, then overall ranks within each averaging method."""
    out = out.copy()
    out["temp_vor_pos"] = out["pos"].map(vor_baseline)

    pieces = []
    for _, group in out.groupby(["avg_type", "pos"], sort=False):
        group = group.copy()
        baseline = group["temp_vor_pos"].iloc[0]

        group["temp_floor_rank"] = rst.dense_rank(-group["floor"])
        group["temp_ceiling_rank"] = rst.dense_rank(-group["ceiling"])

        group["points_vor"] = group["points"] - _at_rank(
            group["points"], group["pos_rank"], baseline
        )
        group["floor_vor"] = group["floor"] - _at_rank(
            group["floor"], group["temp_floor_rank"], baseline
        )
        group["ceiling_vor"] = group["ceiling"] - _at_rank(
            group["ceiling"], group["temp_ceiling_rank"], baseline
        )
        pieces.append(group)

    out = pd.concat(pieces, ignore_index=True)

    ranked = []
    for _, group in out.groupby("avg_type", sort=False):
        group = group.copy()
        group["rank"] = rst.dense_rank(-group["points_vor"])
        group["floor_rank"] = rst.dense_rank(-group["floor_vor"])
        group["ceiling_rank"] = rst.dense_rank(-group["ceiling_vor"])
        ranked.append(group)

    out = pd.concat(ranked, ignore_index=True)
    return out[[
        "avg_type", "id", "pos", "points", "sd_pts", "dropoff", "floor", "ceiling",
        "points_vor", "floor_vor", "ceiling_vor", "rank", "floor_rank", "ceiling_rank",
        "pos_rank", "tier",
    ]]


def _at_rank(values: pd.Series, ranks: pd.Series, baseline) -> float:
    """The value at the baseline rank, falling back to the first row.

    R uses ``which.max(rank == baseline)``, which returns 1 when nothing
    matches -- e.g. when a position has fewer players than the baseline.
    """
    matches = np.flatnonzero((ranks == baseline).to_numpy())
    position = matches[0] if matches.size else 0
    return values.iloc[position]


def _raw_stats_table(working: ScrapeResult, scoring_tables: dict,
                     avg_type: Sequence[str]) -> pd.DataFrame:
    """Aggregate the underlying stats rather than fantasy points."""
    out = []
    for position, frame in working.items():
        scoring_table = scoring_tables.get(position)
        if scoring_table is None:
            continue
        scored = set(scoring_table.loc[scoring_table["val"] != 0, "column"])
        columns = [column for column in frame.columns if column in scored]
        if not columns:
            continue

        # Players projected by only one source have nothing to aggregate.
        multi = frame.groupby("id", sort=False)["id"].transform("size") > 1
        frame = frame[multi]
        if frame.empty:
            continue

        for name in avg_type:
            fun_avg, fun_sd, _ = _estimators(name)
            rows = []
            for player_id, group in frame.groupby("id", sort=False):
                weight = pd.to_numeric(
                    group.get("weights", pd.Series(1.0, index=group.index)),
                    errors="coerce",
                ).to_numpy(dtype=float)
                row = {"id": player_id, "avg_type": name, "position": position}
                for column in columns:
                    values = pd.to_numeric(group[column], errors="coerce").to_numpy(dtype=float)
                    row[column] = fun_avg(values, weight)
                    row[f"{column}_sd"] = fun_sd(values, weight)
                rows.append(row)
            out.append(pd.DataFrame(rows))

    if not out:
        return pd.DataFrame()
    return pd.concat(out, ignore_index=True)


# --------------------------------------------------------------------------
# Enrichment
# --------------------------------------------------------------------------

def add_ecr(projection_table: ProjectionsTable) -> ProjectionsTable:
    """Join FantasyPros expert consensus rankings onto the table.

    Adds ``pos_ecr`` and ``sd_ecr`` (and ``overall_ecr`` for season-long
    tables).  Call this before :func:`add_uncertainty`, which consumes
    ``sd_ecr``.
    """
    from .scrape_ecr import scrape_ecr

    lg_type = projection_table.lg_type
    week = projection_table.week
    print("Scraping ECR data (w/ 2 second delay between pages if not cached)")

    rank_period = "draft" if week == 0 else "weekly"
    frame = projection_table.df

    if week == 0:
        if "PPR" in lg_type.values():
            overall_type = "PPR"
        elif "Half" in lg_type.values():
            overall_type = "Half"
        else:
            overall_type = "Std"
        overall = scrape_ecr(rank_period="draft", rank_type=overall_type, position="Overall")
        frame = frame.merge(
            overall[["id", "avg"]].rename(columns={"avg": "overall_ecr"}), on="id", how="left"
        )

    scraped = []
    for position, scoring_type in lg_type.items():
        scraped.append(
            scrape_ecr(rank_period=rank_period, position=position, rank_type=scoring_type)
        )

    if scraped:
        pos_ecr = pd.concat(scraped, ignore_index=True)
        pos_ecr = pos_ecr[["id", "avg", "std_dev"]].rename(
            columns={"avg": "pos_ecr", "std_dev": "sd_ecr"}
        )
        frame = frame.merge(pos_ecr, on="id", how="left")

    return projection_table.with_df(frame)


def add_adp(projection_table: ProjectionsTable,
            sources: Sequence[str] = ("RTS", "CBS", "Yahoo", "NFL", "FFC", "MFL")
            ) -> ProjectionsTable:
    """Join average draft position, and its difference from the overall rank."""
    from .adp_functions import get_adp

    if projection_table.week != 0:
        print("Warning: ADP data is not available for weekly data")
        return projection_table

    print("Scraping ADP data")
    adp = get_adp(sources, metric="adp")
    if adp is None or adp.empty:
        return projection_table

    if adp.shape[1] == 2:
        adp.columns = ["id", "adp"]
    else:
        adp = adp[["id", "adp_avg", "adp_sd"]].rename(columns={"adp_avg": "adp"})

    frame = projection_table.df.merge(adp, on="id", how="left")
    frame["adp_diff"] = frame["rank"] - frame["adp"]
    return projection_table.with_df(frame)


def add_aav(projection_table: ProjectionsTable,
            sources: Sequence[str] = ("RTS", "ESPN", "Yahoo", "NFL", "MFL")
            ) -> ProjectionsTable:
    """Join average auction value."""
    from .adp_functions import get_adp

    if projection_table.week != 0:
        print("Warning: AAV data is not available for weekly data")
        return projection_table

    print("Scraping AAV Data")
    aav = get_adp(sources, metric="aav")
    if aav is None or aav.empty:
        return projection_table

    if aav.shape[1] == 2:
        aav.columns = ["id", "aav"]
    else:
        aav = aav[["id", "aav_avg", "aav_sd"]].rename(columns={"aav_avg": "aav"})

    return projection_table.with_df(projection_table.df.merge(aav, on="id", how="left"))


def calculate_uncertainty(*variables, percentage: bool = True) -> np.ndarray:
    """Combine spread measures into a 1-99 uncertainty score.

    Each input is standardised, the standardised values are averaged per
    player, and that average is standardised again and turned into a percentile
    rank.  Low means the sources agree; high means they do not.
    """
    matrix = np.column_stack([rst.r_scale(variable) for variable in variables])
    present = np.count_nonzero(~np.isnan(matrix), axis=1)
    with np.errstate(invalid="ignore"):
        totals = np.nansum(matrix, axis=1)
    row_means = np.divide(
        totals, present, out=np.full(totals.shape, np.nan), where=present > 0
    )
    mean_risk = rst.r_scale(row_means)

    if not percentage:
        return mean_risk

    out = np.round(rst.percent_rank(mean_risk).to_numpy(dtype=float), 2)
    out[out <= 0.01] = 0.01
    out[out >= 0.99] = 0.99
    return out


def add_uncertainty(projection_table: ProjectionsTable) -> ProjectionsTable:
    """Add a within-position uncertainty percentile from ``sd_pts`` and ``sd_ecr``."""
    frame = projection_table.df.copy()
    if "sd_ecr" not in frame.columns:
        raise ValueError(
            "add_uncertainty() needs sd_ecr; call add_ecr() first."
        )

    frame["uncertainty"] = np.nan
    for _, index in frame.groupby("pos", sort=False).groups.items():
        group = frame.loc[index]
        frame.loc[index, "uncertainty"] = calculate_uncertainty(
            group["sd_pts"], group["sd_ecr"]
        )
    return projection_table.with_df(frame)


def add_player_info(projection_table: ProjectionsTable) -> ProjectionsTable:
    """Join names, team, position, age and experience onto the table."""
    from .player_data import player_table

    players = player_table()[
        ["id", "first_name", "last_name", "team", "position", "age", "exp"]
    ]
    return projection_table.with_df(
        projection_table.df.merge(players, on="id", how="left")
    )
