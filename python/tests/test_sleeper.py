"""Translating Sleeper's scoring settings into rules we can apply.

The settings below are a real superflex, TE-premium league's, trimmed only of
settings that behave identically to ones already covered.
"""

import pytest

from ffanalytics.sleeper import STARTING_SLOTS, SleeperLeague, scoring_rules_from_sleeper

SETTINGS = {
    "pass_yd": 0.05, "pass_td": 3.0, "pass_int": -1.0, "pass_2pt": 2.0,
    "bonus_pass_yd_400": 0.0,
    "rush_yd": 0.1, "rush_td": 6.0, "rush_2pt": 2.0, "rush_40p": 0.5,
    "bonus_rush_yd_200": 0.0,
    "rec": 1.0, "rec_yd": 0.1, "rec_td": 6.0, "rec_2pt": 2.0, "rec_40p": 0.5,
    "bonus_rec_te": 1.5, "bonus_rec_yd_200": 0.0,
    "fum": -1.0, "fum_lost": -2.0,
    "fgm": 3.0, "fgm_0_19": 0.0, "fgm_20_29": 0.0, "fgm_30_39": 0.0,
    "fgm_40_49": 0.0, "fgm_50_59": 0.5, "fgm_60p": 1.0,
    "fgmiss": 0.0, "fgmiss_0_19": -3.0, "fgmiss_20_29": -2.0,
    "fgmiss_30_39": -1.0, "fgmiss_40_49": -0.5,
    "xpm": 1.0, "xpmiss": -3.0,
    "sack": 1.0, "int": 2.0, "safe": 2.0, "blk_kick": 2.0,
    "fum_rec": 2.0, "ff": 1.0, "def_st_ff": 1.0, "def_st_fum_rec": 1.0,
    "def_td": 6.0, "def_st_td": 6.0, "st_td": 6.0,
    "pts_allow_0": 10.0, "pts_allow_1_6": 7.0, "pts_allow_7_13": 4.0,
    "pts_allow_14_20": 1.0, "pts_allow_21_27": 0.0, "pts_allow_28_34": -1.0,
    "pts_allow_35p": -4.0,
    # points this league awards that no projection source can supply
    "bonus_fd_qb": 0.5, "bonus_fd_rb": 0.5, "pass_td_50p": 2.0,
    "rec_td_40p": 1.0, "pass_cmp_40p": 0.5,
}


@pytest.fixture(scope="module")
def translated():
    return scoring_rules_from_sleeper(SETTINGS, name="test league")


def test_basic_offensive_scoring_carries_over(translated):
    rules, _ = translated
    qb = rules.for_position("QB")
    assert qb["pass_yds"] == 0.05
    assert qb["pass_tds"] == 3.0
    assert qb["pass_int"] == -1.0
    assert qb["rush_tds"] == 6.0
    assert qb["fumbles_lost"] == -2.0
    assert qb["fumbles_total"] == -1.0


def test_the_three_two_point_settings_collapse_into_one_stat(translated):
    rules, _ = translated
    assert rules.for_position("RB")["two_pts"] == 2.0


def test_tight_end_premium_lands_only_on_tight_ends(translated):
    rules, _ = translated
    assert rules.points_per_reception("WR") == 1.0
    assert rules.points_per_reception("RB") == 1.0
    assert rules.points_per_reception("TE") == 2.5
    assert rules.format_label("TE") == "PPR"


def test_field_goals_combine_the_flat_value_with_the_distance_bonus(translated):
    rules, _ = translated
    kicker = rules.for_position("K")
    assert kicker["fg_0019"] == 3.0
    assert kicker["fg_4049"] == 3.0
    assert kicker["fg_50"] == 3.5  # 3 for the make, 0.5 for being a 50-yarder
    assert kicker["xp"] == 1.0
    assert kicker["xp_miss"] == -3.0


def test_per_distance_miss_penalties_blend_into_one_figure(translated):
    """Sources project a single miss count, so the penalties have to be pooled."""
    rules, _ = translated
    blended = rules.for_position("K")["fg_miss"]
    assert -3.0 < blended < 0.0


def test_points_allowed_brackets_come_across_in_order(translated):
    rules, _ = translated
    tiers = [(tier.max_allowed, tier.points) for tier in rules.pts_bracket]
    assert tiers[0] == (0.0, 10.0)
    assert tiers[2] == (13.0, 4.0)
    assert tiers[-1][0] == float("inf")
    assert tiers[-1][1] == -4.0


def test_defensive_settings_map_onto_team_defense_stats(translated):
    rules, _ = translated
    dst = rules.for_position("DST")
    assert dst["dst_sacks"] == 1.0
    assert dst["dst_int"] == 2.0
    assert dst["dst_blk"] == 2.0
    assert dst["dst_td"] == 6.0


def test_settings_with_no_projectable_stat_are_reported_not_dropped(translated):
    rules, unscored = translated
    assert unscored["pass_td_50p"] == 2.0      # nobody projects TD length
    assert unscored["rec_td_40p"] == 1.0
    # ...and nothing that *was* scored shows up here
    assert "pass_yd" not in unscored
    assert "bonus_rec_te" not in unscored
    # a 40+ yard completion is projectable, so it is scored rather than reported
    assert "pass_cmp_40p" not in unscored
    assert rules.stats["pass_40_yds"] == 0.5
    # first downs are estimated from yardage, so they are scored too
    assert "bonus_fd_qb" not in unscored
    assert rules.for_position("QB")["rush_fd"] == 0.5


def test_settings_worth_zero_points_are_not_reported_as_missing(translated):
    _, unscored = translated
    assert "bonus_pass_yd_400" not in unscored


def test_empty_settings_give_empty_rules():
    rules, unscored = scoring_rules_from_sleeper({})
    assert rules.stats == {}
    assert rules.pts_bracket == ()
    assert unscored == {}


def _league(slots):
    return SleeperLeague(league_id="1", name="test", season=2026, teams=12,
                         roster_slots=slots, scoring_settings={})


def test_bench_slots_do_not_count_as_starters():
    league = _league(["QB", "RB", "WR", "BN", "BN", "IR"])
    assert league.starting_slots == ["QB", "RB", "WR"]
    assert league.bench_size == 3


def test_rostered_positions_expand_flex_slots():
    league = _league(["QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "K", "BN"])
    assert league.is_superflex
    assert set(league.rostered_positions) == {"QB", "RB", "WR", "TE", "K"}
    assert "DST" not in league.rostered_positions


def test_every_starting_slot_maps_to_at_least_one_position():
    for slot, positions in STARTING_SLOTS.items():
        assert positions, slot


def test_defensive_fumble_recovery_wins_over_the_special_teams_value():
    """Sleeper prices these separately; the sources publish one DST figure.

    A league paying 2 for a defensive recovery and 1 for a special-teams one
    must score the defensive value, not whichever key happened to be read last.
    """
    rules, _ = scoring_rules_from_sleeper(
        {"fum_rec": 2.0, "def_st_fum_rec": 1.0, "st_fum_rec": 1.0}
    )
    assert rules.stats["dst_fum_rec"] == 2.0


def test_forced_fumbles_resolve_the_same_way():
    rules, _ = scoring_rules_from_sleeper({"ff": 3.0, "st_ff": 1.0})
    assert rules.stats["dst_fum_force"] == 3.0


def test_special_teams_value_is_used_when_there_is_no_defensive_one():
    rules, _ = scoring_rules_from_sleeper({"st_fum_rec": 1.5})
    assert rules.stats["dst_fum_rec"] == 1.5


def test_idp_tackle_aliases_do_not_fight_each_other():
    rules, _ = scoring_rules_from_sleeper({"idp_tkl_solo": 1.0, "tkl_solo": 1.0})
    assert rules.stats["idp_solo"] == 1.0


def test_long_completion_bonus_is_scored_like_the_rushing_and_receiving_ones():
    """pass_40_yds is projectable, so a 40+ yard completion bonus must count."""
    rules, unprojectable = scoring_rules_from_sleeper(
        {"pass_cmp_40p": 0.5, "rush_40p": 0.5, "rec_40p": 0.5}
    )
    assert rules.stats["pass_40_yds"] == 0.5
    assert rules.stats["rush_40_yds"] == 0.5
    assert rules.stats["rec_40_yds"] == 0.5
    assert "pass_cmp_40p" not in unprojectable


def test_touchdown_length_bonuses_remain_unprojectable():
    """No source projects how long a touchdown was."""
    _, unprojectable = scoring_rules_from_sleeper(
        {"pass_td_50p": 2.0, "rush_td_40p": 1.0, "pass_int_td": -3.0}
    )
    assert set(unprojectable) == {"pass_td_50p", "rush_td_40p", "pass_int_td"}


def test_first_down_bonuses_are_scored_per_position():
    rules, unprojectable = scoring_rules_from_sleeper({
        "bonus_fd_qb": 0.5, "bonus_fd_rb": 0.5,
        "bonus_fd_wr": 0.5, "bonus_fd_te": 0.5,
    })
    assert rules.for_position("QB")["rush_fd"] == 0.5
    assert rules.for_position("RB")["rec_fd"] == 0.5
    assert rules.for_position("WR")["rec_fd"] == 0.5
    assert rules.for_position("TE")["rec_fd"] == 0.5
    assert not any(key.startswith("bonus_fd_") for key in unprojectable)


def test_a_first_down_bonus_does_not_clobber_the_tight_end_premium():
    rules, _ = scoring_rules_from_sleeper(
        {"rec": 1.0, "bonus_rec_te": 1.5, "bonus_fd_te": 0.5}
    )
    te = rules.for_position("TE")
    assert te["rec"] == 2.5
    assert te["rec_fd"] == 0.5


def test_passing_first_downs_stay_unprojectable():
    """No passing first-down rate was supplied, so it is reported not guessed."""
    _, unprojectable = scoring_rules_from_sleeper({"pass_fd": 0.5})
    assert unprojectable["pass_fd"] == 0.5
