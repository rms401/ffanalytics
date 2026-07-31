"""Resolving each site's player ids and spellings onto one id."""

import pandas as pd
import pytest

from ffanalytics.players import TEAMS, ids_from_source, normalize, player_ids


def test_the_bundled_crosswalk_has_a_column_per_source():
    crosswalk = player_ids()
    assert len(crosswalk) > 1000
    for column in ("id", "cbs_id", "espn_id", "nfl_id", "fftoday_id", "rts_id",
                   "stats_id", "fantasypro_num_id", "sleeper_id"):
        assert column in crosswalk.columns


def test_ids_keep_their_leading_zeros():
    """Team defenses are ids like 0501; read as numbers they would collide."""
    assert player_ids()["id"].str.startswith("0").any()


def test_normalize_collapses_the_ways_sites_spell_a_name():
    assert (normalize(["A.J. Brown", "AJ Brown", "aj brown"]) == "ajbrown").all()
    assert normalize(["Odell Beckham Jr."])[0] == "odellbeckham"
    assert normalize(["Buffalo Defense"])[0] == "buffalo"


def test_normalize_applies_the_team_and_position_corrections():
    assert normalize(["SFO", "SF", "NWE", "NE"]).tolist() == ["sf", "sf", "ne", "ne"]
    assert normalize(["D/ST", "DEF", "DST"]).tolist() == ["dst", "dst", "dst"]
    assert normalize(["PK"])[0] == "k"


def test_team_corrections_all_land_on_a_real_team():
    from ffanalytics.players import TEAM_CORRECTIONS

    assert set(TEAM_CORRECTIONS.values()) <= set(TEAMS)


def test_source_ids_resolve_through_the_crosswalk():
    crosswalk = player_ids().dropna(subset=["cbs_id"]).head(5)
    resolved = ids_from_source(crosswalk["cbs_id"].tolist(), "cbs_id")
    assert resolved.tolist() == crosswalk["id"].tolist()


def test_an_unknown_source_id_resolves_to_nothing_rather_than_a_guess():
    assert ids_from_source(["not-a-real-id"], "cbs_id").isna().all()


def test_asking_for_a_column_that_does_not_exist_is_an_error():
    with pytest.raises(KeyError, match="made_up_id"):
        ids_from_source(["1"], "made_up_id")


def test_sleeper_ids_are_in_the_crosswalk():
    """The Sleeper join depends on this column being populated."""
    assert player_ids()["sleeper_id"].notna().sum() > 500


@pytest.mark.network
def test_name_matching_finds_players_the_crosswalk_does_not_have():
    from ffanalytics.players import resolve_ids

    resolved = resolve_ids(
        pd.Series(["definitely-not-an-id"] * 2), "cbs_id",
        name=pd.Series(["Josh Allen", "Ja'Marr Chase"]),
        pos=pd.Series(["QB", "WR"]),
        team=pd.Series(["BUF", "CIN"]),
    )
    assert resolved.notna().all()
    assert resolved.nunique() == 2


@pytest.mark.network
def test_team_defenses_resolve_from_the_team_alone():
    from ffanalytics.players import resolve_ids

    resolved = resolve_ids(pos="DST", team=pd.Series(["BUF", "KC", "SF"]))
    assert resolved.notna().all()
    assert resolved.nunique() == 3
