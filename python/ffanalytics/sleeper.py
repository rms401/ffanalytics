"""Reading a league out of Sleeper.

Everything this package computes is generic until it knows your league.  This
module supplies the three things that make it yours:

* **scoring** -- Sleeper's settings translated into a :class:`ScoringRules`,
  along with an honest list of the settings that have no projectable stat
  behind them;
* **roster structure** -- how many teams, and which slots they start, which is
  what replacement level actually depends on;
* **ownership** -- who holds which player, so a projection can be filtered down
  to who is available.

Only public, read-only endpoints are used; no login or token is involved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd
import requests

from .impute import FIRST_DOWN_STATS
from .players import resolve_ids
from .scoring import PointsAllowedTier, ScoringRules

__all__ = [
    "SleeperLeague",
    "fetch_league",
    "leagues_for_user",
    "scoring_rules_from_sleeper",
    "sleeper_player_map",
    "STARTING_SLOTS",
]

_API = "https://api.sleeper.app/v1"
_TIMEOUT = 60

#: Roster slots that start a player, mapped to the positions eligible for them.
STARTING_SLOTS: dict[str, tuple[str, ...]] = {
    "QB": ("QB",),
    "RB": ("RB",),
    "WR": ("WR",),
    "TE": ("TE",),
    "K": ("K",),
    "DEF": ("DST",),
    "DST": ("DST",),
    "FLEX": ("RB", "WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "REC_FLEX": ("WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
    "IDP_FLEX": ("DL", "LB", "DB"),
    "DL": ("DL",),
    "LB": ("LB",),
    "DB": ("DB",),
}

#: Slots that hold a player without starting them.
_BENCH_SLOTS = frozenset({"BN", "IR", "TAXI"})


def _get(path: str) -> Any:
    response = requests.get(f"{_API}/{path}", timeout=_TIMEOUT)
    response.raise_for_status()
    return response.json()


# ---------------------------------------------------------------------------
# The league
# ---------------------------------------------------------------------------

@dataclass
class SleeperLeague:
    """One Sleeper league: its settings, its teams and who they hold."""

    league_id: str
    name: str
    season: int
    teams: int
    roster_slots: list[str]
    scoring_settings: dict[str, float]
    settings: dict[str, Any] = field(default_factory=dict)
    rosters: pd.DataFrame = field(default_factory=pd.DataFrame)
    managers: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def starting_slots(self) -> list[str]:
        return [slot for slot in self.roster_slots if slot not in _BENCH_SLOTS]

    @property
    def bench_size(self) -> int:
        return sum(1 for slot in self.roster_slots if slot in _BENCH_SLOTS)

    @property
    def is_superflex(self) -> bool:
        return "SUPER_FLEX" in self.roster_slots

    @property
    def rostered_positions(self) -> tuple[str, ...]:
        """Positions this league can actually start, in scoring order."""
        eligible: list[str] = []
        for slot in self.starting_slots:
            for position in STARTING_SLOTS.get(slot, ()):
                if position not in eligible:
                    eligible.append(position)
        return tuple(eligible)

    def scoring_rules(self) -> tuple[ScoringRules, dict[str, float]]:
        """This league's scoring, plus the settings that could not be mapped."""
        return scoring_rules_from_sleeper(self.scoring_settings, name=self.name)

    def describe(self) -> str:
        slots = ", ".join(self.starting_slots)
        held = len(self.rosters) if len(self.rosters) else 0
        return (
            f"{self.name} ({self.season}) -- {self.teams} teams\n"
            f"  starting slots: {slots}\n"
            f"  bench: {self.bench_size}"
            + (", plus IR" if "IR" in self.roster_slots else "")
            + f"\n  players rostered: {held}"
        )


def fetch_league(league_id: str | int, with_rosters: bool = True) -> SleeperLeague:
    """Load a league, its managers and (by default) its rosters."""
    league_id = str(league_id)
    payload = _get(f"league/{league_id}")
    if not payload:
        raise ValueError(f"Sleeper has no league {league_id!r}")

    league = SleeperLeague(
        league_id=league_id,
        name=payload.get("name") or league_id,
        season=int(payload.get("season") or 0),
        teams=int(payload.get("total_rosters") or 0),
        roster_slots=list(payload.get("roster_positions") or []),
        scoring_settings=dict(payload.get("scoring_settings") or {}),
        settings=dict(payload.get("settings") or {}),
    )
    if with_rosters:
        league.managers = _fetch_managers(league_id)
        league.rosters = _fetch_rosters(league_id, league.managers)
    return league


def leagues_for_user(username: str, season: int) -> pd.DataFrame:
    """Every NFL league a Sleeper user is in for a season."""
    user = _get(f"user/{username}")
    if not user:
        raise ValueError(f"Sleeper has no user {username!r}")
    leagues = _get(f"user/{user['user_id']}/leagues/nfl/{season}") or []
    return pd.DataFrame([
        {
            "league_id": league.get("league_id"),
            "name": league.get("name"),
            "teams": league.get("total_rosters"),
            "status": league.get("status"),
        }
        for league in leagues
    ])


def _fetch_managers(league_id: str) -> pd.DataFrame:
    users = _get(f"league/{league_id}/users") or []
    return pd.DataFrame([
        {
            "owner_id": user.get("user_id"),
            "manager": user.get("display_name"),
            "team_name": (user.get("metadata") or {}).get("team_name")
            or user.get("display_name"),
        }
        for user in users
    ])


def _fetch_rosters(league_id: str, managers: pd.DataFrame) -> pd.DataFrame:
    """One row per rostered player: who owns them and whether they start."""
    rosters = _get(f"league/{league_id}/rosters") or []

    rows = []
    for roster in rosters:
        starters = {str(p) for p in (roster.get("starters") or []) if p and p != "0"}
        reserve = {str(p) for p in (roster.get("reserve") or []) if p}
        for player in roster.get("players") or []:
            player = str(player)
            rows.append({
                "sleeper_id": player,
                "roster_id": roster.get("roster_id"),
                "owner_id": roster.get("owner_id"),
                "is_starter": player in starters,
                "is_reserve": player in reserve,
            })

    frame = pd.DataFrame(
        rows,
        columns=["sleeper_id", "roster_id", "owner_id", "is_starter", "is_reserve"],
    )
    if not frame.empty and not managers.empty:
        frame = frame.merge(managers, on="owner_id", how="left")
    else:
        frame["manager"] = pd.NA
        frame["team_name"] = pd.NA
    return frame


# ---------------------------------------------------------------------------
# Sleeper's player list
# ---------------------------------------------------------------------------

def sleeper_player_map() -> pd.DataFrame:
    """Sleeper's player list, keyed to our ``id``.

    Sleeper's own crosswalk is used where the bundled one already knows the
    player; the rest are matched on name, position and team.  This is a
    several-megabyte download on every call.
    """
    print("Downloading Sleeper's player list")
    payload = _get("players/nfl") or {}

    rows = []
    for sleeper_id, player in payload.items():
        if not isinstance(player, dict):
            continue
        position = player.get("position")
        if position is None:
            continue
        rows.append({
            "sleeper_id": str(sleeper_id),
            "sleeper_name": player.get("full_name")
            or " ".join(filter(None, [player.get("first_name"), player.get("last_name")])),
            "sleeper_pos": "DST" if position == "DEF" else position,
            "sleeper_team": player.get("team"),
            "status": player.get("status"),
            "injury_status": player.get("injury_status"),
            "years_exp": player.get("years_exp"),
            "age": player.get("age"),
        })

    frame = pd.DataFrame(rows)
    frame["id"] = resolve_ids(
        frame["sleeper_id"], "sleeper_id",
        name=frame["sleeper_name"],
        pos=frame["sleeper_pos"],
        team=frame["sleeper_team"],
    ).to_numpy()

    return frame


# ---------------------------------------------------------------------------
# Scoring translation
# ---------------------------------------------------------------------------

#: Sleeper setting -> our stat, for everything that maps one to one.
_DIRECT = {
    "pass_att": "pass_att",
    "pass_cmp": "pass_comp",
    "pass_inc": "pass_inc",
    "pass_yd": "pass_yds",
    "pass_td": "pass_tds",
    "pass_int": "pass_int",
    "pass_sack": "sacks",
    "bonus_pass_yd_300": "pass_300_yds",
    "bonus_pass_yd_400": "pass_400_yds",
    "rush_att": "rush_att",
    "rush_yd": "rush_yds",
    "rush_td": "rush_tds",
    "rush_40p": "rush_40_yds",
    "bonus_rush_yd_100": "rush_100_yds",
    "bonus_rush_yd_200": "rush_200_yds",
    "pass_cmp_40p": "pass_40_yds",
    "rec": "rec",
    "rec_yd": "rec_yds",
    "rec_td": "rec_tds",
    "rec_40p": "rec_40_yds",
    "bonus_rec_yd_100": "rec_100_yds",
    "bonus_rec_yd_200": "rec_200_yds",
    "fum": "fumbles_total",
    "fum_lost": "fumbles_lost",
    "xpm": "xp",
    "xpmiss": "xp_miss",
    "sack": "dst_sacks",
    "int": "dst_int",
    "safe": "dst_safety",
    "blk_kick": "dst_blk",
    "pts_allow": "dst_pts_allowed",
    "idp_sack": "idp_sack",
    "idp_int": "idp_int",
    "idp_ff": "idp_fum_force",
    "idp_fum_rec": "idp_fum_rec",
    "idp_def_td": "idp_td",
    "idp_safe": "idp_safety",
}

#: Stats several Sleeper settings can feed, in the order they should win.
#:
#: Sleeper splits some events by unit -- a fumble recovered by the defense is
#: ``fum_rec``, one recovered on special teams is ``st_fum_rec`` -- while the
#: projection sources publish a single figure for the whole DST unit.  Leagues
#: often price those differently (this one pays 2 for a defensive recovery and
#: 1 for a special-teams one), so the defensive setting is taken first, since
#: that is where nearly all of a unit's recoveries come from.  The rest are
#: aliases Sleeper uses interchangeably depending on league type.
_FIRST_PRESENT: dict[str, tuple[str, ...]] = {
    "dst_fum_rec": ("fum_rec", "def_st_fum_rec", "st_fum_rec"),
    "dst_fum_force": ("ff", "def_st_ff", "st_ff"),
    "idp_solo": ("idp_tkl_solo", "tkl_solo"),
    "idp_asst": ("idp_tkl_ast", "tkl_ast"),
    "idp_pd": ("idp_pass_def", "pass_def"),
}

#: Several settings feed one stat; the largest wins, since these are almost
#: always set to the same number.
_MAX_OF = {
    "two_pts": ("pass_2pt", "rush_2pt", "rec_2pt"),
    "dst_td": ("def_td", "def_st_td", "st_td"),
    "return_tds": ("kr_td", "pr_td"),
    "return_yds": ("kr_yd", "pr_yd"),
}

#: Field goals: a flat value for any make, plus a bonus by distance.
_FG_BONUSES = {
    "fg_0019": ("fgm_0_19",),
    "fg_2029": ("fgm_20_29",),
    "fg_3039": ("fgm_30_39",),
    "fg_4049": ("fgm_40_49",),
    "fg_50": ("fgm_50p", "fgm_50_59"),
}

#: Roughly how missed field goals split by distance.  Used to collapse
#: Sleeper's per-distance miss penalties into the single ``fg_miss`` figure the
#: projection sources publish.
_MISS_DISTRIBUTION = {
    "fgmiss_0_19": 0.01,
    "fgmiss_20_29": 0.04,
    "fgmiss_30_39": 0.13,
    "fgmiss_40_49": 0.30,
    "fgmiss_50p": 0.52,
}

#: Sleeper's per-position first-down bonus keys.
_FIRST_DOWN_BONUS = {
    "QB": "bonus_fd_qb",
    "RB": "bonus_fd_rb",
    "WR": "bonus_fd_wr",
    "TE": "bonus_fd_te",
}

_POINTS_ALLOWED = [
    ("pts_allow_0", 0.0),
    ("pts_allow_1_6", 6.0),
    ("pts_allow_7_13", 13.0),
    ("pts_allow_14_20", 20.0),
    ("pts_allow_21_27", 27.0),
    ("pts_allow_28_34", 34.0),
    ("pts_allow_35p", float("inf")),
]

#: Settings we knowingly ignore because no source projects the underlying
#: event.  Anything outside this list that goes unmapped is reported loudly.
_NO_PROJECTABLE_STAT = {
    "fd",
    "pass_td_40p", "pass_td_50p", "pass_int_td",
    "rush_td_40p", "rush_td_50p", "rec_td_40p", "rec_td_50p",
    "rec_0_4", "rec_5_9", "rec_10_19", "rec_20_29", "rec_30_39",
    "fgm_60p", "fgm_yds", "fgm_yds_over_30", "fgmiss_60p",
    "fum_rec_td", "def_2pt", "def_st_2pt", "def_forced_punts",
    "def_pass_def", "def_3_and_out", "def_4_and_stop", "def_kr_yd", "def_pr_yd",
    "yds_allow", "sack_yd", "qb_hit", "tkl", "tkl_loss", "idp_tkl", "idp_tkl_loss",
    "idp_qb_hit", "idp_blk_kick", "idp_blk_kick_ret_yd", "idp_int_ret_yd",
    "idp_fum_ret_yd", "idp_pass_def_3p", "idp_tkl_solo_2p", "st_tkl_solo",
}
_IGNORED_PREFIXES = ("yds_allow_", "bonus_def_", "bonus_sack_")


def _value(settings: dict, key: str) -> float | None:
    value = settings.get(key)
    return None if value is None else float(value)


def scoring_rules_from_sleeper(
    settings: dict, name: str = "Sleeper league",
) -> tuple[ScoringRules, dict[str, float]]:
    """Translate Sleeper's scoring settings into rules this package can apply.

    Returns the rules and a dict of the settings that carry points but have no
    stat behind them in any projection source -- first-down bonuses and
    touchdown-length bonuses, mostly.  Those points are real in your league and
    simply cannot be projected here, so they are handed back rather than
    silently dropped.
    """
    settings = {key: float(value) for key, value in (settings or {}).items()
                if value is not None}
    used: set[str] = set()
    stats: dict[str, float] = {}

    for key, stat in _DIRECT.items():
        value = _value(settings, key)
        if value is not None:
            used.add(key)
            if value:
                stats[stat] = value

    for stat, keys in _FIRST_PRESENT.items():
        present = [key for key in keys if key in settings]
        used.update(present)
        if present and settings[present[0]]:
            stats[stat] = settings[present[0]]

    for stat, keys in _MAX_OF.items():
        values = [settings[key] for key in keys if key in settings]
        used.update(key for key in keys if key in settings)
        if values:
            chosen = max(values, key=abs)
            if chosen:
                stats[stat] = chosen

    # Field goals: flat value plus the distance bonus for that bucket.
    flat = _value(settings, "fgm")
    if flat is not None:
        used.add("fgm")
    for stat, bonus_keys in _FG_BONUSES.items():
        bonuses = [settings[key] for key in bonus_keys if key in settings]
        used.update(key for key in bonus_keys if key in settings)
        total = (flat or 0.0) + (max(bonuses, key=abs) if bonuses else 0.0)
        if total:
            stats[stat] = total

    # Missed field goals: one flat figure, so blend the per-distance penalties.
    miss = _value(settings, "fgmiss")
    if miss is not None:
        used.add("fgmiss")
    blended = miss or 0.0
    for key, share in _MISS_DISTRIBUTION.items():
        if key in settings:
            used.add(key)
            blended += settings[key] * share
    if blended:
        stats["fg_miss"] = round(blended, 4)

    # Team defense points allowed.
    bracket = []
    for key, ceiling in _POINTS_ALLOWED:
        if key in settings:
            used.add(key)
            bracket.append(PointsAllowedTier(ceiling, settings[key]))

    # Per-position overrides.  A tight-end reception bonus is the common one.
    by_pos: dict[str, dict[str, float]] = {}
    te_bonus = _value(settings, "bonus_rec_te")
    if te_bonus:
        used.add("bonus_rec_te")
        by_pos["TE"] = {"rec": stats.get("rec", 0.0) + te_bonus}
    elif "bonus_rec_te" in settings:
        used.add("bonus_rec_te")

    # First downs.  Sleeper prices them per position; we can estimate rushing
    # and receiving first downs from yardage, so those are scored.  Passing
    # first downs have no estimate behind them and stay unscored.
    for key in ("pass_fd", "rush_fd", "rec_fd"):
        value = _value(settings, key)
        if value is not None:
            used.add(key)
            if value:
                stats[key] = value

    for position, key in _FIRST_DOWN_BONUS.items():
        value = _value(settings, key)
        if value is None:
            continue
        used.add(key)
        if not value:
            continue
        for stat in FIRST_DOWN_STATS.get(position, ()):
            by_pos.setdefault(position, {})[stat] = stats.get(stat, 0.0) + value

    unmapped = {
        key: value for key, value in settings.items()
        if key not in used and value
        and key not in _NO_PROJECTABLE_STAT
        and not key.startswith(_IGNORED_PREFIXES)
    }
    unprojectable = {
        key: value for key, value in settings.items()
        if key not in used and value
        and (key in _NO_PROJECTABLE_STAT or key.startswith(_IGNORED_PREFIXES))
    }

    rules = ScoringRules(stats=stats, by_pos=by_pos,
                         pts_bracket=tuple(bracket), name=name)
    return rules, {**unprojectable, **unmapped}


def draft_picks(league_id: str | int) -> pd.DataFrame:
    """Every pick made in this league's drafts, newest draft first."""
    drafts = _get(f"league/{league_id}/drafts") or []
    rows = []
    for draft in drafts:
        if draft.get("status") == "pre_draft":
            continue
        for pick in _get(f"draft/{draft['draft_id']}/picks") or []:
            rows.append({
                "sleeper_id": str(pick.get("player_id")),
                "draft_round": pick.get("round"),
                "draft_pick": pick.get("pick_no"),
                "drafted_by": pick.get("picked_by"),
                "season": draft.get("season"),
            })
    return pd.DataFrame(
        rows,
        columns=["sleeper_id", "draft_round", "draft_pick", "drafted_by", "season"],
    )
