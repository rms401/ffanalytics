"""Scoring rules and the defensive points-allowed bracket."""

import numpy as np
import pytest

from ffanalytics.scoring import (
    DEFAULT_SCORING,
    PointsAllowedTier,
    ScoringRules,
    score_points_allowed,
)


def test_per_position_overrides_only_change_that_position():
    rules = ScoringRules(
        stats={"rec": 1.0, "rec_yds": 0.1},
        by_pos={"TE": {"rec": 2.5}},
    )
    assert rules.for_position("WR")["rec"] == 1.0
    assert rules.for_position("TE")["rec"] == 2.5
    assert rules.for_position("TE")["rec_yds"] == 0.1


def test_unknown_stats_are_rejected_rather_than_silently_ignored():
    with pytest.raises(ValueError, match="rushing_yards"):
        ScoringRules(stats={"rushing_yards": 0.1})


def test_format_label_follows_the_reception_value():
    assert ScoringRules(stats={"rec": 1.0}).format_label("WR") == "PPR"
    assert ScoringRules(stats={"rec": 0.5}).format_label("WR") == "Half"
    assert ScoringRules(stats={}).format_label("WR") == "Std"


def test_points_allowed_takes_the_first_tier_it_does_not_exceed():
    bracket = DEFAULT_SCORING.pts_bracket
    assert score_points_allowed([0], bracket)[0] == 10
    assert score_points_allowed([3], bracket)[0] == 7
    assert score_points_allowed([6], bracket)[0] == 7
    assert score_points_allowed([7], bracket)[0] == 4
    assert score_points_allowed([20], bracket)[0] == 4
    assert score_points_allowed([21], bracket)[0] == 0


def test_a_blowout_scores_the_worst_tier_not_the_best():
    """A defense giving up 60 points must not be paid like a shutout."""
    bracket = (
        PointsAllowedTier(0, 10),
        PointsAllowedTier(6, 7),
        PointsAllowedTier(34, 0),
    )
    assert score_points_allowed([60], bracket)[0] == 0


def test_points_allowed_passes_missing_values_through():
    assert np.isnan(score_points_allowed([np.nan], DEFAULT_SCORING.pts_bracket)[0])


def test_no_bracket_scores_nothing():
    assert score_points_allowed([14], ())[0] == 0
