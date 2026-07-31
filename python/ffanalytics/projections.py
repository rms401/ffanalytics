"""Turning a multi-source scrape into one projected-points table.

The pipeline is: fill the gaps between sources (:mod:`ffanalytics.impute`),
score every source's stat line under the league's rules, then summarise each
player across sources -- a central estimate, how much the sources disagree, a
floor and a ceiling, and where that leaves them relative to replacement level.
"""

from __future__ import annotations

import functools
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from . import stats as st
from .impute import (
    impute_bonus_columns,
    impute_first_downs,
    impute_missing_stats,
)
from .players import DATA_DIR, player_table
from .scoring import DEFAULT_SCORING, ScoringRules, score_points_allowed
from .sources import SOURCES

__all__ = [
    "projections_table",
    "source_points",
    "add_ecr",
    "add_uncertainty",
    "add_player_info",
    "REPLACEMENT_RANKS",
    "TIER_THRESHOLDS",
    "AVERAGE_TYPES",
]

AVERAGE_TYPES = ("average", "robust", "weighted")

#: Positional rank treated as freely available, for value over replacement.
#: :func:`ffanalytics.league.replacement_ranks` derives better ones from a
#: real league's roster settings; these are the fallback.
REPLACEMENT_RANKS = {
    "QB": 13, "RB": 35, "WR": 36, "TE": 13, "K": 8, "DST": 3,
    "DL": 10, "LB": 10, "DB": 10,
}

#: How big a gap, in units of the position's typical source disagreement,
#: starts a new tier.
TIER_THRESHOLDS = {
    "QB": 1, "RB": 1, "WR": 1, "TE": 1, "K": 1, "DST": 0.1,
    "DL": 1, "LB": 1, "DB": 1,
}

_GAMES_PER_SEASON = 17
_SIMULATION_SEED = 1


# ---------------------------------------------------------------------------
# Scoring each source
# ---------------------------------------------------------------------------

@functools.lru_cache(maxsize=1)
def _points_allowed_spread() -> pd.DataFrame:
    """Per-team model of how much a defense's points allowed swings week to week."""
    return pd.read_csv(DATA_DIR / "pts_allowed_sd_coefs.csv", dtype={"id": str,
                                                                     "nfl_id": str})


def _defense_points_allowed(frame: pd.DataFrame, rules: ScoringRules,
                            week: int) -> pd.Series:
    """Fantasy points from the points-allowed bracket.

    A weekly figure goes straight through the bracket.  A season total cannot:
    a defense averaging 21 a game does not score the 21-27 bracket seventeen
    times, it scores a spread of shutouts and blowouts.  So a season-long
    projection is simulated -- seventeen games drawn around the implied average
    with that team's historical spread, each scored and then summed.
    """
    allowed = pd.to_numeric(frame["dst_pts_allowed"], errors="coerce")
    if not rules.pts_bracket:
        return pd.Series(0.0, index=frame.index)

    if week > 0:
        return pd.Series(score_points_allowed(allowed, rules.pts_bracket),
                         index=frame.index)

    model = _points_allowed_spread()
    by_id = model.set_index("id")["team"]
    by_nfl_id = model.set_index("nfl_id")["team"]
    intercepts = model.set_index("team")["Intercept"]
    slope = float(model["season_mean"].iloc[0])

    ids = frame["id"].astype("string")
    teams = ids.map(by_id).fillna(ids.map(by_nfl_id))

    per_game = allowed / _GAMES_PER_SEASON
    spread = teams.map(intercepts) + slope * per_game

    rng = np.random.default_rng(_SIMULATION_SEED)
    totals = []
    for mean, sd in zip(per_game, spread):
        if not np.isfinite(mean) or not np.isfinite(sd):
            totals.append(np.nan)
            continue
        games = np.round(rng.normal(mean, sd, _GAMES_PER_SEASON)).clip(min=0)
        totals.append(float(np.sum(score_points_allowed(games, rules.pts_bracket))))
    return pd.Series(totals, index=frame.index)


def source_points(frames: Mapping[str, pd.DataFrame],
                  scoring_rules: ScoringRules = DEFAULT_SCORING,
                  week: int = 0) -> dict[str, pd.DataFrame]:
    """Add a ``projected_points`` column to each frame, one value per source."""
    out = {}
    for position, frame in frames.items():
        frame = frame.copy()
        values = scoring_rules.for_position(position)

        scored = [c for c in frame.columns if c in values and values[c]]
        if scored:
            numbers = frame[scored].apply(pd.to_numeric, errors="coerce")
            points = (numbers * pd.Series({c: values[c] for c in scored})).sum(
                axis=1, skipna=True
            )
        else:
            points = pd.Series(0.0, index=frame.index)

        if position == "DST" and "dst_pts_allowed" in frame.columns:
            bracket_points = _defense_points_allowed(frame, scoring_rules, week)
            frame["dst_pts_allowed_points"] = bracket_points
            points = points + bracket_points.fillna(0)

        frame["projected_points"] = points
        out[position] = frame
    return out


# ---------------------------------------------------------------------------
# Aggregating across sources
# ---------------------------------------------------------------------------

def _estimators(avg_type: str):
    """``(centre, spread, floor/ceiling)`` for one averaging method."""
    if avg_type == "average":
        return (lambda x, w: st.mean(x),
                lambda x, w: st.sd(x),
                lambda x, w: st.quantiles(x))
    if avg_type == "robust":
        return (lambda x, w: st.wilcox_location(x),
                lambda x, w: st.mad(x),
                lambda x, w: st.quantiles(x))
    if avg_type == "weighted":
        return (st.weighted_mean, st.weighted_sd, st.weighted_quantiles)
    raise ValueError(
        f"Unknown avg_type {avg_type!r}; choose from {', '.join(AVERAGE_TYPES)}"
    )


def _source_weights() -> pd.Series:
    return pd.Series({name: source.weight for name, source in SOURCES.items()})


def _summarise(frame: pd.DataFrame, position: str, avg_type: str) -> pd.DataFrame:
    """One row per player: points, disagreement, floor and ceiling."""
    centre, spread, bounds = _estimators(avg_type)
    weights = frame["data_src"].map(_source_weights()).fillna(0.0)
    if not (weights > 0).any():
        # Nothing available has a published weight -- weighting by nothing
        # would throw the whole position away, so weight them equally.
        weights = pd.Series(1.0, index=frame.index)

    rows = []
    for player_id, group in frame.groupby("id", sort=False):
        points = pd.to_numeric(group["projected_points"], errors="coerce").to_numpy(float)
        weight = weights.loc[group.index].to_numpy(float)
        low, high = bounds(points, weight)
        rows.append({
            "id": player_id,
            "pos": position,
            "points": centre(points, weight),
            "sd_pts": spread(points, weight),
            "floor": low,
            "ceiling": high,
            "sources": int(np.isfinite(points).sum()),
        })

    summary = pd.DataFrame(rows)
    if summary.empty:
        return summary
    keep = np.isfinite(summary["points"]) & (summary["points"] > 0)
    return summary[keep].reset_index(drop=True)


def _rank_and_tier(summary: pd.DataFrame, tier_threshold: float) -> pd.DataFrame:
    """Positional rank, the gap to the next player down, and tier breaks."""
    summary = summary.sort_values("points", ascending=False).reset_index(drop=True)
    summary["pos_rank"] = st.dense_rank(-summary["points"]).astype("Int64")

    # How many points this player is worth over the next one down.
    summary["dropoff"] = (summary["points"] - summary["points"].shift(-1)).fillna(0.0)

    typical_spread = float(np.nanmedian(summary["sd_pts"])) if len(summary) else np.nan
    step = typical_spread * tier_threshold
    if not np.isfinite(step) or step <= 0:
        summary["tier"] = 1
        return summary

    fallen = summary["points"].iloc[0] - summary["points"]
    summary["tier"] = st.dense_rank(np.trunc(fallen / step)).astype("Int64")
    return summary


def _value_over_replacement(table: pd.DataFrame,
                            replacement_ranks: Mapping[str, int]) -> pd.DataFrame:
    """Points above the last starter at each position, and the overall ranks."""
    pieces = []
    for (_, position), group in table.groupby(["avg_type", "pos"], sort=False):
        group = group.copy()
        baseline = replacement_ranks.get(position, REPLACEMENT_RANKS.get(position, 1))
        for column, rank_column in (("points", "pos_rank"),
                                    ("floor", "_floor_rank"),
                                    ("ceiling", "_ceiling_rank")):
            if rank_column.startswith("_"):
                group[rank_column] = st.dense_rank(-group[column])
            group[f"{column}_vor"] = group[column] - _replacement_value(
                group[column], group[rank_column], baseline
            )
        pieces.append(group.drop(columns=["_floor_rank", "_ceiling_rank"]))

    table = pd.concat(pieces, ignore_index=True)

    ranked = []
    for _, group in table.groupby("avg_type", sort=False):
        group = group.copy()
        group["rank"] = st.dense_rank(-group["points_vor"]).astype("Int64")
        ranked.append(group)
    return pd.concat(ranked, ignore_index=True)


def _replacement_value(values: pd.Series, ranks: pd.Series, baseline: int) -> float:
    """The points of the player at the replacement rank.

    When a position has fewer players than the baseline -- a short scrape, or a
    deep league -- the worst projected player is the best available stand-in.
    """
    matches = np.flatnonzero(np.asarray(ranks == baseline))
    if matches.size:
        return float(values.iloc[matches[0]])
    order = np.argsort(np.asarray(ranks, dtype=float))
    return float(values.iloc[order[-1]]) if len(values) else 0.0


def projections_table(
    scrape,
    scoring_rules: ScoringRules = DEFAULT_SCORING,
    avg_type: str | Sequence[str] = "average",
    replacement_ranks: Mapping[str, int] | None = None,
    tier_thresholds: Mapping[str, float] | None = None,
) -> pd.DataFrame:
    """Aggregate a scrape into projected points, ranks, tiers and VOR.

    Returns one row per player (per ``avg_type``, if you ask for more than
    one), with:

    ``points``
        the central estimate across sources
    ``sd_pts``
        how much the sources disagree
    ``floor`` / ``ceiling``
        the 5th and 95th percentiles of what they project
    ``dropoff``
        points lost by taking the next player at the position instead
    ``points_vor``
        points above a freely available player at that position
    ``rank`` / ``pos_rank`` / ``tier``
        overall by value over replacement, within position, and grouped

    ``avg_type`` picks how sources are combined: ``average`` is the plain
    mean, ``robust`` resists one site being far out on its own, and
    ``weighted`` uses each site's published accuracy weight.
    """
    avg_types = [avg_type] if isinstance(avg_type, str) else list(avg_type)
    replacement_ranks = replacement_ranks or REPLACEMENT_RANKS
    tier_thresholds = tier_thresholds or TIER_THRESHOLDS

    frames = {
        position: frame[frame["id"].notna()].copy()
        for position, frame in scrape.items()
    }
    frames = {position: frame for position, frame in frames.items() if len(frame)}

    frames = impute_missing_stats(frames, scoring_rules)
    frames = impute_bonus_columns(frames, scoring_rules)
    frames = impute_first_downs(frames, scoring_rules)
    frames = source_points(frames, scoring_rules, week=scrape.week)

    tables = []
    for name in avg_types:
        for position, frame in frames.items():
            summary = _summarise(frame, position, name)
            if summary.empty:
                continue
            summary = _rank_and_tier(summary, tier_thresholds.get(position, 1))
            summary.insert(0, "avg_type", name)
            tables.append(summary)

    columns = ["avg_type", "id", "pos", "points", "sd_pts", "floor", "ceiling",
               "dropoff", "points_vor", "floor_vor", "ceiling_vor", "rank",
               "pos_rank", "tier", "sources"]
    if not tables:
        return pd.DataFrame(columns=columns)

    table = _value_over_replacement(pd.concat(tables, ignore_index=True),
                                    replacement_ranks)
    table = table[columns].sort_values(["avg_type", "rank"]).reset_index(drop=True)
    table.attrs.update(season=scrape.season, week=scrape.week,
                       scoring=scoring_rules.name)
    return table


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------

def add_player_info(table: pd.DataFrame) -> pd.DataFrame:
    """Join names, team, position, age and experience onto the table."""
    players = player_table()[
        ["id", "first_name", "last_name", "team", "position", "age", "exp"]
    ].rename(columns={"position": "listed_pos"})
    merged = table.merge(players, on="id", how="left")
    merged["player"] = (
        merged["first_name"].fillna("") + " " + merged["last_name"].fillna("")
    ).str.strip()
    merged.attrs.update(table.attrs)
    return merged.drop(columns=["first_name", "last_name"])


def add_ecr(table: pd.DataFrame, scoring_rules: ScoringRules = DEFAULT_SCORING,
            week: int | None = None) -> pd.DataFrame:
    """Join FantasyPros expert consensus rankings (``pos_ecr``, ``sd_ecr``)."""
    from .ecr import scrape_ecr

    week = table.attrs.get("week", 0) if week is None else week
    period = "draft" if week == 0 else "weekly"

    pieces = []
    for position in sorted(table["pos"].dropna().unique()):
        frame = scrape_ecr(period=period, position=position,
                           scoring=scoring_rules.format_label(position))
        if frame is not None and len(frame):
            pieces.append(frame)

    if not pieces:
        print("No ECR data available; leaving pos_ecr blank")
        merged = table.copy()
        merged["pos_ecr"] = np.nan
        merged["sd_ecr"] = np.nan
    else:
        ecr = pd.concat(pieces, ignore_index=True)[["id", "avg", "std_dev"]]
        ecr = ecr.rename(columns={"avg": "pos_ecr", "std_dev": "sd_ecr"})
        merged = table.merge(ecr.drop_duplicates("id"), on="id", how="left")

    merged.attrs.update(table.attrs)
    return merged


def add_uncertainty(table: pd.DataFrame) -> pd.DataFrame:
    """Add a 1-99 uncertainty score per position.

    Combines how much the projection sources disagree with how much the human
    rankers disagree.  Low means everyone sees the player the same way.
    """
    merged = table.copy()
    inputs = ["sd_pts"] + (["sd_ecr"] if "sd_ecr" in merged.columns else [])
    merged["uncertainty"] = np.nan

    for _, index in merged.groupby("pos", sort=False).groups.items():
        group = merged.loc[index]
        standardized = np.column_stack([st.standardize(group[c]) for c in inputs])
        present = np.count_nonzero(~np.isnan(standardized), axis=1)
        with np.errstate(invalid="ignore"):
            totals = np.nansum(standardized, axis=1)
        combined = np.divide(totals, present, out=np.full(totals.shape, np.nan),
                             where=present > 0)
        score = st.percentile(st.standardize(combined)).to_numpy(dtype=float)
        merged.loc[index, "uncertainty"] = np.clip(np.round(score, 2), 0.01, 0.99)

    merged.attrs.update(table.attrs)
    return merged
