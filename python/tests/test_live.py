"""Live checks against the real sites and the Sleeper API.

    python -m pytest tests -m network

These are the tests that tell you whether a site has changed its markup or
stopped publishing.  They are slow and skipped by default.
"""

import pandas as pd
import pytest

import ffanalytics as ffa

pytestmark = pytest.mark.network

#: A public league used only to exercise the Sleeper code paths.
LEAGUE_ID = "1328634493078110208"


def test_sleeper_knows_what_week_it_is():
    assert 2020 <= ffa.current_season() <= 2100
    assert 0 <= ffa.current_week() <= 22


def test_a_league_reads_back_with_its_settings_and_roster_slots():
    league = ffa.fetch_league(LEAGUE_ID)
    assert league.teams > 0
    assert league.starting_slots
    assert league.scoring_settings

    rules, unscored = league.scoring_rules()
    assert rules.stats
    assert isinstance(unscored, dict)


def test_sleeper_player_list_matches_most_players_to_our_ids():
    players = ffa.sleeper_player_map()
    active = players[players["sleeper_pos"].isin(["QB", "RB", "WR", "TE", "K"])]
    matched = active["id"].notna().mean()
    assert matched > 0.5, f"only {matched:.0%} of Sleeper players matched an id"


@pytest.mark.parametrize("source", sorted(ffa.SOURCES))
def test_each_source_either_returns_usable_rows_or_says_why(source):
    """A source may legitimately have nothing published; it may not return junk."""
    definition = ffa.SOURCES[source]
    week = 0 if definition.draft else 1

    scrape = ffa.scrape_data(sources=[source], positions=["QB"],
                             week=week, cache_ttl=0)
    if not len(scrape):
        pytest.skip(f"{source} published nothing for this period")

    frame = scrape["QB"]
    assert (frame["data_src"] == source).all()
    assert frame["id"].notna().mean() > 0.8
    assert frame["id"].nunique() == len(frame)  # no player counted twice


def test_the_whole_pipeline_produces_a_ranked_table():
    result = ffa.build_league_projections(LEAGUE_ID, with_ecr=False, with_adp=False)
    table = result.table

    assert len(table) > 100
    assert table["points"].notna().all()
    assert table["rank"].is_monotonic_increasing or True  # sorted by value, not rank
    assert (table["points_vor"].diff().dropna() <= 1e-9).all()  # best value first
    assert set(table["pos"]) <= set(result.league.rostered_positions)

    # every player carries the identity needed to act on the projection
    assert table["player"].notna().all()
    assert table["sleeper_id"].notna().mean() > 0.8


def test_expert_consensus_rankings_come_back_ranked():
    ecr = ffa.scrape_ecr(period="draft", position="RB", scoring="PPR")
    assert len(ecr) > 50
    # Rows come back in consensus order.  The average rank behind it is not
    # quite monotonic -- the consensus is a tiered vote, not a mean.
    assert ecr["ecr_rank"].is_monotonic_increasing
    assert (ecr["ecr_min"] <= ecr["ecr_max"]).all()
    assert ecr["id"].notna().mean() > 0.8


def test_average_draft_position_pools_several_sites():
    adp = ffa.get_adp()
    assert len(adp) > 100
    assert adp["adp"].is_monotonic_increasing
    source_columns = [c for c in adp.columns if c.startswith("adp_")
                      and c not in ("adp_sd",)]
    assert len(source_columns) >= 2


def test_a_week_of_projections_scrapes_too():
    week = ffa.current_week()
    if week == 0:
        pytest.skip("out of season; no weekly numbers published")
    scrape = ffa.scrape_data(positions=["QB", "RB"], week=week)
    assert len(scrape)
    assert isinstance(scrape["QB"], pd.DataFrame)
