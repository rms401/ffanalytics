"""League scoring rules.

A rule set is just "how many points is this stat worth", plus per-position
overrides for the handful of leagues that score a position differently (TE
premium, mostly), plus the points-allowed brackets that score a defense.

    >>> DEFAULT_SCORING.for_position("WR")["rec_yds"]
    0.1

The stat names are the canonical column names the scrapers produce; see
:data:`SCORING_STATS` for the full vocabulary.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

import numpy as np

__all__ = [
    "ScoringRules",
    "PointsAllowedTier",
    "DEFAULT_SCORING",
    "SCORING_STATS",
    "score_points_allowed",
]

#: Every stat that can carry a point value, grouped only for documentation.
SCORING_STATS: dict[str, tuple[str, ...]] = {
    "passing": (
        "pass_att", "pass_comp", "pass_inc", "pass_yds", "pass_tds", "pass_int",
        "pass_40_yds", "pass_300_yds", "pass_350_yds", "pass_400_yds",
    ),
    "rushing": (
        "rush_att", "rush_yds", "rush_tds", "rush_40_yds",
        "rush_100_yds", "rush_150_yds", "rush_200_yds", "rush_fd",
    ),
    "receiving": (
        "rec", "rec_tgt", "rec_yds", "rec_tds", "rec_40_yds",
        "rec_100_yds", "rec_150_yds", "rec_200_yds", "rec_fd",
    ),
    "misc": ("fumbles_lost", "fumbles_total", "sacks", "two_pts"),
    "kicking": (
        "xp", "xp_miss", "fg_0019", "fg_2029", "fg_3039", "fg_4049", "fg_50",
        "fg_miss",
    ),
    "returns": ("return_tds", "return_yds"),
    "idp": (
        "idp_solo", "idp_asst", "idp_sack", "idp_int", "idp_fum_force",
        "idp_fum_rec", "idp_pd", "idp_td", "idp_safety",
    ),
    "dst": (
        "dst_sacks", "dst_int", "dst_fum_rec", "dst_fum_force", "dst_safety",
        "dst_td", "dst_blk", "dst_ret_yds", "dst_pts_allowed",
    ),
}

_KNOWN_STATS = frozenset(stat for group in SCORING_STATS.values() for stat in group)


@dataclass(frozen=True)
class PointsAllowedTier:
    """Points a defense scores for holding an offense under ``max_allowed``."""

    max_allowed: float
    points: float


@dataclass(frozen=True)
class ScoringRules:
    """Point values for each stat, with optional per-position overrides."""

    stats: Mapping[str, float] = field(default_factory=dict)
    by_pos: Mapping[str, Mapping[str, float]] = field(default_factory=dict)
    pts_bracket: Sequence[PointsAllowedTier] = ()
    name: str = "custom"

    def __post_init__(self) -> None:
        unknown = sorted(
            stat
            for stat in {*self.stats, *(s for o in self.by_pos.values() for s in o)}
            if stat not in _KNOWN_STATS
        )
        if unknown:
            raise ValueError(
                f"Unknown scoring stat(s): {', '.join(unknown)}. "
                "See ffanalytics.scoring.SCORING_STATS for the vocabulary."
            )

    def for_position(self, position: str) -> dict[str, float]:
        """The point values that apply to one position."""
        return {**self.stats, **self.by_pos.get(position, {})}

    def points_per_reception(self, position: str) -> float:
        return float(self.for_position(position).get("rec", 0.0) or 0.0)

    def format_label(self, position: str) -> str:
        """``PPR`` / ``Half`` / ``Std`` -- what rankings sites call the format."""
        per_reception = self.points_per_reception(position)
        if per_reception > 0.5:
            return "PPR"
        return "Half" if per_reception > 0 else "Std"

    def with_overrides(self, **stats: float) -> "ScoringRules":
        return replace(self, stats={**self.stats, **stats}, name=f"{self.name}+")


#: The package's default rule set: a standard, non-PPR league.
DEFAULT_SCORING = ScoringRules(
    name="ffanalytics default",
    stats={
        "pass_yds": 0.04, "pass_tds": 4, "pass_int": -3,
        "rush_yds": 0.1, "rush_tds": 6,
        "rec": 0, "rec_yds": 0.1, "rec_tds": 6,
        "fumbles_lost": -3, "two_pts": 2,
        "xp": 1, "fg_0019": 3, "fg_2029": 3, "fg_3039": 3, "fg_4049": 4, "fg_50": 5,
        "return_tds": 6,
        "idp_solo": 1, "idp_asst": 0.5, "idp_sack": 2, "idp_int": 3,
        "idp_fum_force": 3, "idp_fum_rec": 2, "idp_pd": 1, "idp_td": 6,
        "idp_safety": 2,
        "dst_sacks": 1, "dst_int": 2, "dst_fum_rec": 2, "dst_safety": 2,
        "dst_td": 6, "dst_blk": 1.5,
    },
    pts_bracket=(
        PointsAllowedTier(0, 10),
        PointsAllowedTier(6, 7),
        PointsAllowedTier(20, 4),
        PointsAllowedTier(34, 0),
        PointsAllowedTier(float("inf"), -4),
    ),
)


def score_points_allowed(points_allowed, pts_bracket: Sequence[PointsAllowedTier]):
    """Points a defense scores for each points-allowed figure.

    A value takes the points of the first tier it does not exceed; anything
    above every tier takes the last (worst) tier.
    """
    values = np.atleast_1d(np.asarray(points_allowed, dtype=float))
    if not pts_bracket:
        return np.zeros(values.shape)

    tiers = sorted(pts_bracket, key=lambda tier: tier.max_allowed)
    thresholds = np.array([tier.max_allowed for tier in tiers], dtype=float)
    payouts = np.array([tier.points for tier in tiers], dtype=float)

    index = np.searchsorted(thresholds, values, side="left")
    index = np.clip(index, 0, len(tiers) - 1)
    scored = payouts[index]
    return np.where(np.isnan(values), np.nan, scored)
