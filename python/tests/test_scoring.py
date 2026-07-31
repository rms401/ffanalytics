"""Scoring rules: the defaults, custom rules, and the per-position tables."""

import copy

import pytest

from ffanalytics.custom_scoring import custom_scoring, make_scoring_tables
from ffanalytics.scoring_rules import scoring, scoring_empty, scoring_type_for_cols


def value_for(table, column):
    values = table.loc[table["column"] == column, "val"].tolist()
    return values[0] if values else None


def test_default_scoring_matches_the_documented_settings():
    assert scoring["pass"]["pass_yds"] == 0.04
    assert scoring["pass"]["pass_tds"] == 4
    assert scoring["pass"]["pass_int"] == -3
    assert scoring["rush"]["rush_yds"] == 0.1
    assert scoring["rush"]["rush_tds"] == 6
    assert scoring["rec"]["rec"] == 0  # not PPR by default
    assert scoring["misc"]["fumbles_lost"] == -3
    assert scoring["kick"]["fg_50"] == 5.0
    assert scoring["dst"]["dst_td"] == 6
    assert scoring["idp"]["idp_solo"] == 1


def test_default_points_bracket():
    assert [(entry["threshold"], entry["points"]) for entry in scoring["pts_bracket"]] == [
        (0, 10), (6, 7), (20, 4), (34, 0), (99, -4)
    ]


def test_scoring_type_for_cols_returns_the_first_match():
    """R's named-vector lookup takes the first `all_pos`, which is `rush`."""
    assert scoring_type_for_cols["all_pos"] == "rush"
    assert scoring_type_for_cols["pass_yds"] == "pass"
    assert scoring_type_for_cols["idp_solo"] == "idp"
    assert scoring_type_for_cols["dst_pts_allowed"] == "dst"


def test_scoring_empty_has_no_all_pos_for_passing():
    assert "all_pos" not in scoring_empty["pass"]
    assert "all_pos" in scoring_empty["rush"]


def test_make_scoring_tables_covers_all_nine_positions():
    tables = make_scoring_tables(scoring)["scoring_tables"]
    assert set(tables) == {"QB", "RB", "WR", "TE", "K", "DST", "DL", "LB", "DB"}


def test_scoring_table_carries_the_default_values():
    qb = make_scoring_tables(scoring)["scoring_tables"]["QB"]
    assert value_for(qb, "pass_yds") == pytest.approx(0.04)
    assert value_for(qb, "pass_tds") == pytest.approx(4)
    assert value_for(qb, "pass_int") == pytest.approx(-3)
    assert value_for(qb, "rush_tds") == pytest.approx(6)


def test_only_dst_onward_gets_the_points_bracket_row():
    """R rebinds the shared table inside the loop, so DL/LB/DB inherit it."""
    tables = make_scoring_tables(scoring)["scoring_tables"]
    has_bracket = {
        position: "pts_bracket" in table["column"].values
        for position, table in tables.items()
    }
    assert has_bracket == {
        "QB": False, "RB": False, "WR": False, "TE": False, "K": False,
        "DST": True, "DL": True, "LB": True, "DB": True,
    }


def test_points_bracket_is_extracted_as_numbers():
    bracket = make_scoring_tables(scoring)["pts_bracket"]
    assert bracket[0] == {"threshold": 0.0, "points": 10.0}
    assert all(isinstance(v, float) for entry in bracket for v in entry.values())


def test_custom_scoring_routes_flat_values_into_categories():
    rules = custom_scoring(pass_yds=0.04, pass_tds=4, rush_yds=0.1, rec=1)
    assert rules["pass"] == {"all_pos": True, "pass_yds": 0.04, "pass_tds": 4}
    assert rules["rush"]["rush_yds"] == 0.1
    assert rules["rec"]["rec"] == 1
    assert rules["rec"]["all_pos"] is True


def test_custom_scoring_marks_per_position_categories():
    rules = custom_scoring(
        pass_yds=0.04,
        RB={"rec": 1, "rec_yds": 0.1},
        WR={"rec": 1, "rec_yds": 0.1},
        TE={"rec": 1.5, "rec_yds": 0.1},
    )
    assert rules["rec"]["all_pos"] is False
    assert rules["rec"]["TE"]["rec"] == 1.5
    assert rules["rec"]["RB"]["rec"] == 1


def test_per_position_scoring_produces_per_position_tables():
    rules = custom_scoring(
        pass_yds=0.04,
        RB={"rec": 1, "rec_yds": 0.1},
        TE={"rec": 1.5, "rec_yds": 0.1},
    )
    rules["pts_bracket"] = scoring["pts_bracket"]
    tables = make_scoring_tables(rules)["scoring_tables"]

    assert value_for(tables["TE"], "rec") == pytest.approx(1.5)
    assert value_for(tables["RB"], "rec") == pytest.approx(1.0)
    # QB was given no receiving scoring, so the category is dropped for QB
    assert value_for(tables["QB"], "rec") is None


def test_custom_scoring_rejects_unknown_stats():
    with pytest.raises(ValueError, match="not a scoring variable"):
        custom_scoring(not_a_real_stat=1)


def test_custom_scoring_does_not_mutate_the_shared_skeleton():
    before = copy.deepcopy(scoring_empty)
    custom_scoring(pass_yds=99)
    assert scoring_empty == before


def test_make_scoring_tables_does_not_mutate_the_rules():
    before = copy.deepcopy(scoring)
    make_scoring_tables(scoring)
    assert scoring == before
    assert "pts_bracket" in scoring
