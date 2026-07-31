"""Aggregating several sources into points, ranks, tiers and value."""

import numpy as np
import pandas as pd
import pytest

from ffanalytics.projections import projections_table, source_points
from ffanalytics.scoring import PointsAllowedTier, ScoringRules
from ffanalytics.scrape import Scrape

RULES = ScoringRules(
    stats={"pass_yds": 0.04, "pass_tds": 4, "pass_int": -3,
           "rush_yds": 0.1, "rush_tds": 6, "rec": 1, "rec_yds": 0.1, "rec_tds": 6},
    name="test",
)


def quarterbacks() -> pd.DataFrame:
    """Four passers seen by three sources, with one source missing a stat."""
    rows = []
    for player, sources in {
        "A": [(5000, 40, 10), (4800, 38, 12), (5200, 42, 8)],
        "B": [(4000, 30, 14), (4100, 28, 15), (3900, 32, 13)],
        "C": [(3000, 20, 18), (3100, 22, 17), (2900, 18, 19)],
        "D": [(2000, 10, 20), (2100, 12, 19), (1900, 8, 21)],
    }.items():
        for source, (yards, tds, interceptions) in zip(("CBS", "ESPN", "NFL"), sources):
            rows.append({
                "id": player, "pos": "QB", "data_src": source,
                "pass_yds": yards, "pass_tds": tds, "pass_int": interceptions,
                "rush_yds": 0, "rush_tds": 0,
            })
    return pd.DataFrame(rows)


@pytest.fixture
def scrape() -> Scrape:
    return Scrape(frames={"QB": quarterbacks()}, season=2025, week=0)


def test_source_points_scores_each_source_row_separately():
    scored = source_points({"QB": quarterbacks()}, RULES)["QB"]
    first = scored.iloc[0]
    expected = 5000 * 0.04 + 40 * 4 - 10 * 3
    assert first["projected_points"] == pytest.approx(expected)
    assert len(scored) == 12


def test_projections_table_gives_one_row_per_player(scrape):
    table = projections_table(scrape, RULES)
    assert len(table) == 4
    assert set(table["id"]) == {"A", "B", "C", "D"}
    assert table["sources"].tolist() == [3, 3, 3, 3]


def test_points_are_the_average_across_sources(scrape):
    table = projections_table(scrape, RULES).set_index("id")
    by_source = [5000 * 0.04 + 40 * 4 - 10 * 3,
                 4800 * 0.04 + 38 * 4 - 12 * 3,
                 5200 * 0.04 + 42 * 4 - 8 * 3]
    assert table.loc["A", "points"] == pytest.approx(np.mean(by_source))


def test_ranks_run_from_the_best_player_down(scrape):
    table = projections_table(scrape, RULES).sort_values("pos_rank")
    assert table["id"].tolist() == ["A", "B", "C", "D"]
    assert table["pos_rank"].tolist() == [1, 2, 3, 4]


def test_floor_and_ceiling_bracket_the_projection(scrape):
    table = projections_table(scrape, RULES)
    assert (table["floor"] <= table["points"]).all()
    assert (table["points"] <= table["ceiling"]).all()


def test_dropoff_is_the_gap_to_the_next_player_down(scrape):
    table = projections_table(scrape, RULES).sort_values("pos_rank").reset_index(drop=True)
    gap = table.loc[0, "points"] - table.loc[1, "points"]
    assert table.loc[0, "dropoff"] == pytest.approx(gap)
    assert table["dropoff"].iloc[-1] == 0.0  # nobody below the last player


def test_value_over_replacement_is_zero_at_the_baseline(scrape):
    table = projections_table(scrape, RULES, replacement_ranks={"QB": 3})
    replacement = table[table["pos_rank"] == 3]
    assert replacement["points_vor"].iloc[0] == pytest.approx(0.0)
    assert (table[table["pos_rank"] < 3]["points_vor"] > 0).all()
    assert (table[table["pos_rank"] > 3]["points_vor"] < 0).all()


def test_a_baseline_deeper_than_the_pool_falls_back_to_the_worst_player(scrape):
    table = projections_table(scrape, RULES, replacement_ranks={"QB": 99})
    assert table["points_vor"].min() == pytest.approx(0.0)


def test_every_averaging_method_produces_a_table(scrape):
    table = projections_table(scrape, RULES,
                              avg_type=("average", "robust", "weighted"))
    assert set(table["avg_type"]) == {"average", "robust", "weighted"}
    assert len(table) == 12
    for _, group in table.groupby("avg_type"):
        assert group.sort_values("pos_rank")["id"].tolist() == ["A", "B", "C", "D"]


def test_an_empty_scrape_gives_an_empty_table():
    table = projections_table(Scrape(frames={}, season=2025, week=0), RULES)
    assert table.empty
    assert "points_vor" in table.columns


def test_defense_points_allowed_is_simulated_over_a_season():
    """A season total cannot be run through the bracket once; it is spread out."""
    frame = pd.DataFrame({
        "id": ["0501", "0502"], "pos": ["DST", "DST"],
        "data_src": ["CBS", "CBS"],
        "team": ["BUF", "IND"],
        "dst_pts_allowed": [289.0, 400.0],  # 17 and ~23.5 a game
        "dst_sacks": [50, 40],
    })
    rules = ScoringRules(
        stats={"dst_sacks": 1},
        pts_bracket=(PointsAllowedTier(0, 10), PointsAllowedTier(13, 4),
                     PointsAllowedTier(20, 1), PointsAllowedTier(float("inf"), -4)),
        name="test",
    )
    scored = source_points({"DST": frame}, rules, week=0)["DST"]

    stingier, leakier = scored["dst_pts_allowed_points"]
    assert stingier > leakier
    # 17 games of bracket scoring, so well beyond any single week's payout.
    assert 17 * -4 <= leakier <= 17 * 10


def test_weekly_points_allowed_goes_straight_through_the_bracket():
    frame = pd.DataFrame({
        "id": ["0501"], "pos": ["DST"], "data_src": ["CBS"], "team": ["BUF"],
        "dst_pts_allowed": [3.0],
    })
    rules = ScoringRules(
        pts_bracket=(PointsAllowedTier(0, 10), PointsAllowedTier(6, 7),
                     PointsAllowedTier(float("inf"), 0)),
        name="test",
    )
    scored = source_points({"DST": frame}, rules, week=5)["DST"]
    assert scored["dst_pts_allowed_points"].iloc[0] == 7.0


def test_weighted_average_falls_back_when_no_source_has_a_weight():
    """A weekly scrape can be all unweighted sources; that must still rank."""
    frame = quarterbacks()
    frame["data_src"] = frame["data_src"].map(
        {"CBS": "FleaFlicker", "ESPN": "FleaFlicker", "NFL": "FantasyPros"}
    )
    table = projections_table(Scrape(frames={"QB": frame}, season=2025, week=1),
                              RULES, avg_type="weighted")
    assert len(table) == 4
    assert table["points"].notna().all()
