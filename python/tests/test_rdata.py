"""The RData reader must recover the R package's internal data exactly."""

import pytest

from ffanalytics import sysdata
from ffanalytics.rdata import RDataError, read_rdata


def test_sysdata_contains_the_four_internal_objects():
    data = read_rdata(sysdata.sysdata_path())
    assert set(data) == {
        "player_ids",
        "bonus_col_coefs",
        "bonus_col_sets",
        "pts_bracket_coefs",
    }


def test_player_ids_shape_and_columns():
    frame = sysdata.player_ids()
    assert frame.shape == (5681, 15)
    assert list(frame.columns) == [
        "id", "stats_id", "cbs_id", "fleaflicker_id", "nfl_id", "espn_id",
        "fftoday_id", "numfire_id", "fantasypro_id", "fantasydata_id",
        "fantasynerd_id", "rts_id", "fantasypro_num_id", "gsis_id", "sleeper_id",
    ]


def test_pts_bracket_coefs_has_one_row_per_team():
    frame = sysdata.pts_bracket_coefs()
    assert frame.shape == (32, 5)
    assert list(frame.columns) == ["id", "nfl_id", "team", "Intercept", "season_mean"]

    arizona = frame.loc[frame["team"] == "ARI"].iloc[0]
    assert arizona["id"] == "0519"
    assert arizona["nfl_id"] == "100026"
    assert arizona["Intercept"] == pytest.approx(9.08675)
    assert arizona["season_mean"] == pytest.approx(0.06835)


def test_bonus_col_coefs_are_intercept_reference_slope_triples():
    coefs = sysdata.bonus_col_coefs()
    assert len(coefs) == 12

    intercept, reference, slope = coefs["pass_300_yds"]
    assert reference == "pass_yds"
    assert intercept == pytest.approx(-0.6583884759337107)
    assert slope == pytest.approx(0.0006993394619894457)

    # every bonus column regresses on the matching yardage column
    for name, (_, ref_col, _) in coefs.items():
        assert ref_col == name.split("_")[0] + "_yds"


def test_bonus_col_sets_roll_nested_thresholds_upward():
    sets = sysdata.bonus_col_sets()
    assert sets == {
        "pass_300_yds": ["pass_300_yds", "pass_350_yds", "pass_400_yds"],
        "pass_350_yds": ["pass_350_yds", "pass_400_yds"],
        "rec_100_yds": ["rec_100_yds", "rec_150_yds", "rec_200_yds"],
        "rec_150_yds": ["rec_150_yds", "rec_200_yds"],
        "rush_100_yds": ["rush_100_yds", "rush_150_yds", "rush_200_yds"],
        "rush_150_yds": ["rush_150_yds", "rush_200_yds"],
    }


def test_named_vector_rda_keeps_order(tmp_path):
    """data/nfl_cols.rda is a plain named character vector."""
    path = sysdata.sysdata_path().parents[1] / "data" / "nfl_cols.rda"
    if not path.exists():
        pytest.skip("data/nfl_cols.rda not present")
    nfl_cols = read_rdata(path)["nfl_cols"]
    assert nfl_cols[:3] == [("GP", "1"), ("Pass Att", "2"), ("Pass Comp", "3")]


def test_files_holding_r_code_objects_raise_clearly():
    """projection_sources.rda holds retired R6 objects; refuse it explicitly."""
    path = sysdata.sysdata_path().parents[1] / "data" / "projection_sources.rda"
    if not path.exists():
        pytest.skip("data/projection_sources.rda not present")
    with pytest.raises(RDataError, match="closure/environment"):
        read_rdata(path)
