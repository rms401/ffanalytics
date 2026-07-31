"""Scrape dispatch, source registry, and ECR URL construction.

The network-dependent checks are marked ``network`` and deselected by default:
``pytest -m network`` runs them.
"""

import pandas as pd
import pytest

from ffanalytics.scrape_ecr import _page_name
from ffanalytics.scrape_funcs import SOURCE_NAMES, scrape_data
from ffanalytics.source_scrapes import SOURCES


def test_every_r_source_is_registered():
    assert set(SOURCE_NAMES) == {
        "CBS", "ESPN", "FantasyData", "FantasyPros", "FantasySharks", "FFToday",
        "FleaFlicker", "NumberFire", "Yahoo", "FantasyFootballNerd", "NFL",
        "RTSports", "Walterfootball", "FanDuel",
    }


def test_period_support_matches_the_r_package():
    assert SOURCES["FleaFlicker"].draft is False   # weekly only
    assert SOURCES["RTSports"].weekly is False     # season-long only
    assert SOURCES["Walterfootball"].weekly is False
    assert SOURCES["CBS"].draft and SOURCES["CBS"].weekly


def test_only_some_sources_cover_individual_defensive_players():
    for name in ("ESPN", "FantasySharks", "FFToday", "FleaFlicker"):
        assert "DL" in SOURCES[name].positions
    for name in ("CBS", "NFL", "FantasyPros", "FanDuel"):
        assert "DL" not in SOURCES[name].positions


def test_unknown_source_is_rejected():
    with pytest.raises(ValueError, match="not a valid source"):
        scrape_data(src=["NotARealSite"], pos=["QB"], season=2026, week=0)


def test_unknown_position_is_rejected():
    with pytest.raises(ValueError, match="not a valid position"):
        scrape_data(src=["CBS"], pos=["PUNTER"], season=2026, week=0)


def test_numberfire_is_rewritten_to_fanduel(capsys):
    scrape_data(src=["NumberFire"], pos=["QB"], season=2026, week=0)
    assert "NumberFire is now FanDuel" in capsys.readouterr().out


def test_sources_without_draft_data_are_skipped_and_reported(capsys):
    scrape_data(src=["FleaFlicker"], pos=["QB"], season=2026, week=0)
    assert "Draft data not available for FleaFlicker" in capsys.readouterr().out


def test_sources_without_weekly_data_are_skipped_and_reported(capsys):
    scrape_data(src=["RTSports"], pos=["QB"], season=2026, week=3)
    assert "Weekly data not available for RTSports" in capsys.readouterr().out


def test_stub_sources_explain_themselves(capsys):
    scrape_data(src=["Yahoo"], pos=["QB"], season=2026, week=0)
    assert "no longer supported" in capsys.readouterr().out


# --------------------------------------------------------------------------
# ECR page names
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "period,position,scoring,expected",
    [
        ("draft", "Overall", "Std", "consensus-cheatsheets.php"),
        ("draft", "Overall", "PPR", "ppr-cheatsheets.php"),
        ("draft", "Overall", "Half", "half-point-ppr-cheatsheets.php"),
        ("draft", "QB", "Std", "qb-cheatsheets.php"),
        ("draft", "QB", "PPR", "qb-cheatsheets.php"),  # QB has no PPR variant
        ("draft", "RB", "PPR", "ppr-rb-cheatsheets.php"),
        ("draft", "TE", "Half", "half-point-ppr-te-cheatsheets.php"),
        ("weekly", "QB", "Std", "qb.php"),
        ("weekly", "WR", "PPR", "ppr-wr.php"),
        ("ros", "RB", "PPR", "ros-ppr-rb.php"),
        ("ros", "QB", "Std", "ros-qb.php"),
        ("dynasty", "RB", "Std", "dynasty-rb.php"),
        ("rookies", "RB", "Std", "rookies.php"),
    ],
)
def test_ecr_page_names(period, position, scoring, expected):
    assert _page_name(period, position, scoring) == expected


# --------------------------------------------------------------------------
# Live checks
# --------------------------------------------------------------------------

@pytest.mark.network
def test_live_scrape_and_projections():
    scrape = scrape_data(
        src=["FantasyPros", "ESPN", "RTSports"],
        pos=["QB", "RB", "WR", "TE", "DST"],
        season=2026,
        week=0,
    )
    assert len(scrape) > 0
    assert len(scrape.sources()) >= 2

    for position, frame in scrape.items():
        assert {"id", "pos", "data_src"} <= set(frame.columns)
        assert frame["id"].notna().mean() > 0.9, f"{position} ids mostly unresolved"

    from ffanalytics import projections_table

    table = projections_table(scrape)
    assert set(table.df["avg_type"]) == {"average", "robust", "weighted"}
    assert (table.df["points"] > 0).all()
    assert table.df["rank"].min() == 1
