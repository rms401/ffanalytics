"""Default and empty scoring definitions.

Transcribed from ``R/scoring_rules.R``.  ``scoring`` is the package default
(exported in R); ``scoring_empty`` is the full skeleton of every legal slot,
which :func:`ffanalytics.custom_scoring.custom_scoring` fills in and prunes.
See the R vignette ``scoring_settings`` for the user-facing description.
"""

from __future__ import annotations

from .rcompat.stats import NamedVec

__all__ = ["scoring", "scoring_empty", "scoring_type_for_cols"]


#: Default scoring rules (``R/scoring_rules.R:5-49``).
scoring = {
    "pass": {
        "pass_att": 0, "pass_comp": 0, "pass_inc": 0, "pass_yds": 0.04,
        "pass_tds": 4, "pass_int": -3, "pass_40_yds": 0, "pass_300_yds": 0,
        "pass_350_yds": 0, "pass_400_yds": 0,
    },
    "rush": {
        "all_pos": True,
        "rush_yds": 0.1, "rush_att": 0, "rush_40_yds": 0, "rush_tds": 6,
        "rush_100_yds": 0, "rush_150_yds": 0, "rush_200_yds": 0,
    },
    "rec": {
        "all_pos": True,
        "rec": 0, "rec_yds": 0.1, "rec_tds": 6, "rec_40_yds": 0,
        "rec_100_yds": 0, "rec_150_yds": 0, "rec_200_yds": 0,
    },
    "misc": {
        "all_pos": True,
        "fumbles_lost": -3, "fumbles_total": 0, "sacks": 0, "two_pts": 2,
    },
    "kick": {
        "xp": 1.0, "fg_0019": 3.0, "fg_2029": 3.0, "fg_3039": 3.0,
        "fg_4049": 4.0, "fg_50": 5.0, "fg_miss": 0.0,
    },
    "ret": {
        "all_pos": True,
        "return_tds": 6, "return_yds": 0,
    },
    "idp": {
        "all_pos": True,
        "idp_solo": 1, "idp_asst": 0.5, "idp_sack": 2, "idp_int": 3,
        "idp_fum_force": 3, "idp_fum_rec": 2, "idp_pd": 1, "idp_td": 6,
        "idp_safety": 2,
    },
    "dst": {
        "dst_fum_rec": 2, "dst_int": 2, "dst_safety": 2, "dst_sacks": 1,
        "dst_td": 6, "dst_blk": 1.5, "dst_ret_yds": 0, "dst_pts_allowed": 0,
    },
    "pts_bracket": [
        {"threshold": 0, "points": 10},
        {"threshold": 6, "points": 7},
        {"threshold": 20, "points": 4},
        {"threshold": 34, "points": 0},
        {"threshold": 99, "points": -4},
    ],
}


def _empty(keys, positions=(), all_pos=False):
    """One ``scoring_empty`` category: bare stat slots plus per-position copies."""
    out = {}
    if all_pos:
        out["all_pos"] = None
    out.update({key: None for key in keys})
    for position in positions:
        out[position] = {key: None for key in keys}
    return out


_PASS = ("pass_att", "pass_comp", "pass_inc", "pass_yds", "pass_tds", "pass_int",
         "pass_40_yds", "pass_300_yds", "pass_350_yds", "pass_400_yds")
_RUSH = ("rush_yds", "rush_att", "rush_40_yds", "rush_tds", "rush_100_yds",
         "rush_150_yds", "rush_200_yds")
_REC = ("rec", "rec_yds", "rec_tds", "rec_40_yds", "rec_100_yds", "rec_150_yds",
        "rec_200_yds")
_MISC = ("fumbles_lost", "fumbles_total", "sacks", "two_pts")
_KICK = ("xp", "fg_0019", "fg_2029", "fg_3039", "fg_4049", "fg_50", "fg_miss")
_RET = ("return_tds", "return_yds")
_IDP = ("idp_solo", "idp_asst", "idp_sack", "idp_int", "idp_fum_force",
        "idp_fum_rec", "idp_pd", "idp_td", "idp_safety")
_DST = ("dst_fum_rec", "dst_int", "dst_safety", "dst_sacks", "dst_td", "dst_blk",
        "dst_ret_yds", "dst_pts_allowed")

_OFFENSE = ("QB", "RB", "WR", "TE")
_DEFENSE = ("DL", "LB", "DB")

#: Every legal scoring slot (``R/scoring_rules.R:54-118``).  Note ``pass`` has
#: no ``all_pos`` slot of its own, matching R.
scoring_empty = {
    "pass": _empty(_PASS, _OFFENSE),
    "rush": _empty(_RUSH, _OFFENSE, all_pos=True),
    "rec": _empty(_REC, _OFFENSE, all_pos=True),
    "misc": _empty(_MISC, _OFFENSE, all_pos=True),
    "kick": _empty(_KICK),
    "ret": _empty(_RET, all_pos=True),
    "idp": _empty(_IDP, _DEFENSE, all_pos=True),
    "dst": _empty(_DST),
}


#: Stat column -> scoring category (``R/scoring_rules.R:122-139``).
# duplicate names (R returns the first match): ['all_pos']
scoring_type_for_cols = NamedVec([
    ('pass_att', 'pass'),
    ('pass_comp', 'pass'),
    ('pass_inc', 'pass'),
    ('pass_yds', 'pass'),
    ('pass_tds', 'pass'),
    ('pass_int', 'pass'),
    ('pass_40_yds', 'pass'),
    ('pass_300_yds', 'pass'),
    ('pass_350_yds', 'pass'),
    ('pass_400_yds', 'pass'),
    ('all_pos', 'rush'),
    ('rush_yds', 'rush'),
    ('rush_att', 'rush'),
    ('rush_40_yds', 'rush'),
    ('rush_tds', 'rush'),
    ('rush_100_yds', 'rush'),
    ('rush_150_yds', 'rush'),
    ('rush_200_yds', 'rush'),
    ('all_pos', 'rec'),
    ('rec', 'rec'),
    ('rec_yds', 'rec'),
    ('rec_tds', 'rec'),
    ('rec_40_yds', 'rec'),
    ('rec_100_yds', 'rec'),
    ('rec_150_yds', 'rec'),
    ('rec_200_yds', 'rec'),
    ('all_pos', 'misc'),
    ('fumbles_lost', 'misc'),
    ('fumbles_total', 'misc'),
    ('sacks', 'misc'),
    ('two_pts', 'misc'),
    ('xp', 'kick'),
    ('fg_0019', 'kick'),
    ('fg_2029', 'kick'),
    ('fg_3039', 'kick'),
    ('fg_4049', 'kick'),
    ('fg_50', 'kick'),
    ('fg_miss', 'kick'),
    ('all_pos', 'ret'),
    ('return_tds', 'ret'),
    ('return_yds', 'ret'),
    ('all_pos', 'idp'),
    ('idp_solo', 'idp'),
    ('idp_asst', 'idp'),
    ('idp_sack', 'idp'),
    ('idp_int', 'idp'),
    ('idp_fum_force', 'idp'),
    ('idp_fum_rec', 'idp'),
    ('idp_pd', 'idp'),
    ('idp_td', 'idp'),
    ('idp_safety', 'idp'),
    ('dst_fum_rec', 'dst'),
    ('dst_int', 'dst'),
    ('dst_safety', 'dst'),
    ('dst_sacks', 'dst'),
    ('dst_td', 'dst'),
    ('dst_blk', 'dst'),
    ('dst_ret_yds', 'dst'),
    ('dst_pts_allowed', 'dst'),
])
