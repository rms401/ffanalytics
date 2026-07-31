"""Scoring and aggregation, on synthetic scrapes with hand-computed answers."""

import numpy as np
import pandas as pd
import pytest

from ffanalytics.calc_projections import (
    default_baseline,
    default_threshold,
    default_weights,
    prep_src_weights,
    projections_table,
    score_pts_bracket,
    source_points,
)
from ffanalytics.results import ScrapeResult
from ffanalytics.scoring_rules import scoring


def make_scrape(week=1, season=2026):
    """Three QBs projected by three sources, with round numbers."""
    qb = pd.DataFrame(
        {
            "id": ["0001", "0001", "0001", "0002", "0002", "0002", "0003", "0003", "0003"],
            "pos": ["QB"] * 9,
            "data_src": ["CBS", "ESPN", "NFL"] * 3,
            "pass_yds": [4000, 4200, 4100, 3000, 3100, 3200, 2000, 2100, 2200],
            "pass_tds": [30, 32, 31, 20, 21, 22, 10, 11, 12],
            "pass_int": [10, 10, 10, 10, 10, 10, 10, 10, 10],
            "pass_att": [500] * 9,
            "pass_comp": [350] * 9,
            "rush_yds": [0] * 9,
            "rush_tds": [0] * 9,
        }
    )
    return ScrapeResult({"QB": qb}, season=season, week=week)


def test_source_points_scores_each_source_row():
    scrape = make_scrape()
    points = source_points(scrape, scoring)

    # CBS row for player 0001: 4000 * 0.04 + 30 * 4 + 10 * -3 = 160 + 120 - 30 = 250
    row = points[(points.id == "0001") & (points.data_src == "CBS")]
    assert row["raw_points"].iloc[0] == pytest.approx(250.0)

    # NFL row for player 0003: 2200 * 0.04 + 12 * 4 + 10 * -3 = 88 + 48 - 30 = 106
    row = points[(points.id == "0003") & (points.data_src == "NFL")]
    assert row["raw_points"].iloc[0] == pytest.approx(106.0)


def test_source_points_returns_long_format_sorted():
    points = source_points(make_scrape(), scoring)
    assert list(points.columns) == ["pos", "data_src", "id", "raw_points"]
    assert points["id"].tolist() == ["0001"] * 3 + ["0002"] * 3 + ["0003"] * 3


def test_source_points_can_return_the_scrape_with_points_attached():
    result = source_points(make_scrape(), scoring, return_data_result=True)
    assert isinstance(result, ScrapeResult)
    assert "raw_points" in result["QB"].columns
    assert result.season == 2026 and result.week == 1


def test_projections_table_averages_across_sources():
    table = projections_table(make_scrape(), avg_type="average")
    frame = table.df

    # player 0001 across CBS/ESPN/NFL: 250, 266, 258  -> mean 258
    player = frame[frame.id == "0001"].iloc[0]
    assert player["points"] == pytest.approx(258.0)
    assert player["sd_pts"] == pytest.approx(8.0)
    assert player["pos_rank"] == 1

    # 5th/95th percentile of (250, 258, 266), quantile type 7
    assert player["floor"] == pytest.approx(250.8)
    assert player["ceiling"] == pytest.approx(265.2)


def test_projections_table_ranks_and_drops_off_by_points():
    frame = projections_table(make_scrape(), avg_type="average").df
    frame = frame.sort_values("pos_rank")

    assert frame["id"].tolist() == ["0001", "0002", "0003"]
    assert frame["pos_rank"].tolist() == [1, 2, 3]
    # points are 258, 178, 98 -> each step down is 80
    assert frame["points"].tolist() == pytest.approx([258.0, 178.0, 98.0])
    # R computes dropoff as c(0, diff(points)) while the frame is still sorted
    # ascending, then re-sorts descending, so the last-placed player carries the 0.
    assert frame["dropoff"].tolist() == pytest.approx([80.0, 80.0, 0.0])


def test_projections_table_cuts_tiers_from_the_median_spread():
    frame = projections_table(make_scrape(), avg_type="average").df.sort_values("pos_rank")
    # median sd is 8 and the QB threshold is 1, so an 80-point gap is 10 tiers
    # wide: the top QB stands alone and the other two share a tier.
    assert frame["tier"].tolist() == [1, 2, 2]


def test_projections_table_computes_vor_against_the_baseline_rank():
    # With a baseline of rank 2, VOR is measured against the second QB.
    frame = projections_table(
        make_scrape(), avg_type="average", vor_baseline={"QB": 2}
    ).df.sort_values("pos_rank")
    assert frame["points_vor"].tolist() == pytest.approx([80.0, 0.0, -80.0])


def test_projections_table_falls_back_to_the_top_player_when_baseline_is_missing():
    # Default QB baseline is rank 13, but only three players exist.
    frame = projections_table(make_scrape(), avg_type="average").df.sort_values("pos_rank")
    assert frame["points_vor"].tolist() == pytest.approx([0.0, -80.0, -160.0])


def test_projections_table_produces_every_requested_avg_type():
    table = projections_table(make_scrape())
    assert sorted(table.df["avg_type"].unique()) == ["average", "robust", "weighted"]
    assert len(table.df) == 9  # 3 players x 3 methods


def test_weighted_avg_type_uses_the_source_weights():
    scrape = make_scrape()
    frame = projections_table(scrape, avg_type="weighted").df
    player = frame[frame.id == "0001"].iloc[0]

    # CBS .145, ESPN .157, NFL .140 against raw points 250, 262, 256
    weights = np.array([default_weights[s] for s in ("CBS", "ESPN", "NFL")])
    points = np.array([250.0, 266.0, 258.0])
    assert player["points"] == pytest.approx((points * weights).sum() / weights.sum())


def test_projections_table_carries_season_week_and_league_type():
    table = projections_table(make_scrape(season=2026, week=3))
    assert table.season == 2026
    assert table.week == 3
    assert table.lg_type == {"QB": "Std"}  # default scoring has rec = 0


def test_league_type_follows_points_per_reception():
    import copy

    ppr = copy.deepcopy(scoring)
    ppr["rec"]["rec"] = 1
    assert projections_table(make_scrape(), scoring_rules=ppr).lg_type == {"QB": "PPR"}

    half = copy.deepcopy(scoring)
    half["rec"]["rec"] = 0.5
    assert projections_table(make_scrape(), scoring_rules=half).lg_type == {"QB": "Half"}


def test_return_raw_stats_aggregates_stats_not_points():
    stats = projections_table(make_scrape(), avg_type="average", return_raw_stats=True)
    row = stats[stats.id == "0001"].iloc[0]
    assert row["pass_yds"] == pytest.approx(4100.0)
    assert row["pass_tds"] == pytest.approx(31.0)
    assert "pass_yds_sd" in stats.columns


def test_projections_table_drops_players_with_no_id():
    scrape = make_scrape()
    scrape["QB"].loc[0, "id"] = None
    frame = projections_table(scrape, avg_type="average").df
    # player 0001 keeps its two remaining sources
    assert (frame.id == "0001").sum() == 1


# --------------------------------------------------------------------------
# DST points allowed
# --------------------------------------------------------------------------

def test_score_pts_bracket_picks_the_first_matching_threshold():
    bracket = scoring["pts_bracket"]
    assert list(score_pts_bracket([0, 3, 6, 7, 20, 21, 34, 35], bracket)) == [
        10, 7, 7, 4, 4, 0, 0, -4
    ]


def test_score_pts_bracket_falls_back_to_the_first_bracket_above_every_threshold():
    """R's max.col(..., "first") returns column 1 when no threshold matches."""
    assert score_pts_bracket([500], scoring["pts_bracket"])[0] == 10


def _bracket_scoring():
    """Default scoring, but with the points-allowed bracket actually counting.

    ``score_dst_pts_allowed`` writes the bracket result *into* the
    ``dst_pts_allowed`` column, which ``source_points`` then multiplies by that
    stat's scoring value.  The package default is 0, so a league that scores
    points allowed by bracket has to set it to 1 for the bracket to survive.
    """
    import copy

    rules = copy.deepcopy(scoring)
    rules["dst"]["dst_pts_allowed"] = 1
    return rules


def test_weekly_dst_points_allowed_goes_straight_through_the_bracket():
    dst = pd.DataFrame(
        {
            "id": ["0501", "0502"],
            "pos": ["DST", "DST"],
            "data_src": ["CBS", "CBS"],
            "dst_pts_allowed": [3.0, 24.0],
            "dst_sacks": [2.0, 1.0],
        }
    )
    result = source_points(
        ScrapeResult({"DST": dst}, season=2026, week=5),
        _bracket_scoring(),
        return_data_result=True,
    )
    # 3 points allowed -> 7, plus 2 sacks; 24 allowed -> 0, plus 1 sack
    assert result["DST"]["raw_points"].tolist() == pytest.approx([9.0, 1.0])


def test_default_scoring_multiplies_the_bracket_result_by_zero():
    """Documents R's behaviour: the default dst_pts_allowed value of 0 voids
    the bracket, so a bracket league must set it explicitly."""
    dst = pd.DataFrame(
        {
            "id": ["0501"],
            "pos": ["DST"],
            "data_src": ["CBS"],
            "dst_pts_allowed": [3.0],
            "dst_sacks": [2.0],
        }
    )
    result = source_points(
        ScrapeResult({"DST": dst}, season=2026, week=5), scoring, return_data_result=True
    )
    assert result["DST"]["raw_points"].tolist() == pytest.approx([2.0])


def test_seasonal_dst_points_allowed_is_simulated_and_reproducible():
    dst = pd.DataFrame(
        {
            "id": ["0501", "0512"],
            "pos": ["DST", "DST"],
            "data_src": ["CBS", "CBS"],
            "dst_pts_allowed": [340.0, 400.0],
        }
    )
    rules = _bracket_scoring()

    def run():
        return source_points(
            ScrapeResult({"DST": dst.copy()}, season=2026, week=0),
            rules,
            return_data_result=True,
        )["DST"]["raw_points"].tolist()

    first, second = run(), run()
    assert first == second, "the seeded simulation must be reproducible"
    # 17 games scored individually, so within the bracket's per-game range.
    assert all(-4 * 17 <= value <= 10 * 17 for value in first)
    # Allowing fewer points over the season should score better.
    assert first[0] > first[1]


def test_seasonal_dst_simulation_consumes_one_shared_rng_stream():
    """Every team draws from the same seeded stream, so a team's result
    depends on its position in the frame -- as it does in R."""
    dst = pd.DataFrame(
        {
            "id": ["0501", "0512"],
            "pos": ["DST", "DST"],
            "data_src": ["CBS", "CBS"],
            "dst_pts_allowed": [340.0, 340.0],
        }
    )
    points = source_points(
        ScrapeResult({"DST": dst}, season=2026, week=0),
        _bracket_scoring(),
        return_data_result=True,
    )["DST"]["raw_points"].tolist()
    assert points[0] != points[1]


# --------------------------------------------------------------------------
# Weights
# --------------------------------------------------------------------------

def test_prep_src_weights_expands_a_flat_mapping_over_every_position():
    weights = prep_src_weights({"CBS": 0.5, "ESPN": 0.25})
    assert set(weights["pos"]) == {"QB", "RB", "WR", "TE", "DST", "K", "DB", "DL", "LB"}
    assert weights.loc[weights.data_src == "CBS", "weights"].unique().tolist() == [0.5]


def test_prep_src_weights_accepts_per_position_mappings():
    weights = prep_src_weights({"QB": {"CBS": 0.9}, "RB": {"CBS": 0.1}})
    assert weights.loc[weights.pos == "QB", "weights"].iloc[0] == 0.9
    assert weights.loc[weights.pos == "RB", "weights"].iloc[0] == 0.1


def test_defaults_cover_every_position():
    assert set(default_baseline) == set(default_threshold)
