"""The draft advisor: plan the rest of the draft, recommend the next pick.

The opponent model is deliberate and fixed: every other team drafts the best
available player by this league's own weighted value board (no assuming
faulty play).  That makes the future deterministic, so instead of scoring one
pick at a time the advisor plays the whole remaining draft forward for each
candidate move and compares the finished starting lineups.

Rules the engine enforces:

* **Starters only.**  A candidate must fill an open starting slot; a position
  falls out of the running the moment every slot that could take it is full.
  Once a roster's starters are complete, picks are bench picks -- best
  available value.
* **Value decides, alone.**  Plans are ranked purely by the finished
  lineup's projected points.  Spread, floor and ceiling are context the
  page displays; they carry no weight here.

Everything reads from the SQLite file a full run wrote; nothing here touches
the network.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from .sleeper import STARTING_SLOTS

__all__ = ["recommend"]

#: Board columns the advisor needs per player.
_BOARD_COLUMNS = ("id", "player", "pos", "points", "points_vor", "sd_pts",
                  "floor", "ceiling", "dropoff", "pos_rank", "sleeper_id")


# ---------------------------------------------------------------------------
# Roster bookkeeping


@dataclass
class _Roster:
    """One team's starting slots, filled greedily in pick order."""

    open_slots: list[str]
    bench: int = 0
    players: list[dict] = field(default_factory=list)

    def slot_for(self, position: str) -> str | None:
        for slot in self.open_slots:
            if position in STARTING_SLOTS.get(slot, ()):
                return slot
        return None

    def startable(self, position: str) -> bool:
        return self.slot_for(position) is not None

    @property
    def starters_full(self) -> bool:
        return not self.open_slots

    def take(self, player: dict) -> None:
        slot = self.slot_for(player["pos"])
        if slot is not None:
            self.open_slots.remove(slot)
            self.players.append(player)
        else:
            self.bench += 1


def _starting_slots(slots: pd.DataFrame) -> list[str]:
    expanded: list[str] = []
    for row in slots[slots["kind"] == "slot"].itertuples():
        expanded.extend([row.name] * int(row.count))
    return expanded


# ---------------------------------------------------------------------------
# Snake arithmetic


def _slot_of_pick(pick_no: int, teams: int, snake: bool) -> int:
    round_index, offset = divmod(pick_no - 1, teams)
    return teams - offset if snake and round_index % 2 else offset + 1


def _picks_of_slot(slot: int, teams: int, rounds: int, snake: bool) -> list[int]:
    return [pick for pick in range(1, teams * rounds + 1)
            if _slot_of_pick(pick, teams, snake) == slot]


def _pick_label(pick_no: int, teams: int) -> str:
    round_index, offset = divmod(pick_no - 1, teams)
    return f"{round_index + 1}.{offset + 1:02d}"


# ---------------------------------------------------------------------------
# The simulation


class _Pool:
    """Available players, best value first."""

    def __init__(self, rows: list[dict]):
        self.rows = sorted(rows, key=lambda r: -r["points_vor"])
        self.taken: set[str] = set()

    def best(self, wants=None) -> dict | None:
        for row in self.rows:
            if row["id"] in self.taken:
                continue
            if wants is None or wants(row):
                return row
        return None

    def best_at(self, position: str) -> dict | None:
        return self.best(lambda r, p=position: r["pos"] == p)

    def take(self, row: dict) -> None:
        self.taken.add(row["id"])

    def untake(self, rows) -> None:
        self.taken.difference_update(r["id"] for r in rows)


def _model_pick(pool: _Pool, roster: _Roster) -> dict | None:
    """What a value drafter takes: best VOR that fills an open starting slot,
    best VOR outright once their starters are done."""
    if roster.starters_full:
        return pool.best()
    return pool.best(lambda row: roster.startable(row["pos"])) or pool.best()


def _lineup_total(roster: _Roster) -> float:
    return sum(player["points"] for player in roster.players)


def _simulate(pool: _Pool, rosters: dict[int, _Roster], picks: list[int],
              teams: int, snake: bool, my_slot: int,
              my_first: dict | None) -> tuple[float, list[dict]]:
    """Play ``picks`` forward; I open with ``my_first``, then autopilot.

    Returns my finished starting-lineup total and my planned picks.  The pool
    and rosters are restored before returning, so callers can branch freely.
    """
    chosen: list[dict] = []
    plan: list[dict] = []
    states = {slot: (list(r.open_slots), r.bench, list(r.players))
              for slot, r in rosters.items()}

    first_used = False
    for pick_no in picks:
        slot = _slot_of_pick(pick_no, teams, snake)
        roster = rosters[slot]
        if slot == my_slot:
            if not first_used and my_first is not None:
                player = my_first
                first_used = True
            else:
                player = _model_pick(pool, roster)
            if player is None:
                continue
            plan.append({"pick": pick_no, "player": player,
                         "starter": roster.startable(player["pos"])})
        else:
            player = _model_pick(pool, roster)
            if player is None:
                continue
        pool.take(player)
        chosen.append(player)
        roster.take(player)

    total = _lineup_total(rosters[my_slot])

    pool.untake(chosen)
    for slot, (open_slots, bench, players) in states.items():
        rosters[slot].open_slots = open_slots
        rosters[slot].bench = bench
        rosters[slot].players = players
    return total, plan


# ---------------------------------------------------------------------------
# Loading and identity


def _frames(connection: sqlite3.Connection) -> dict[str, pd.DataFrame]:
    read = lambda sql: pd.read_sql_query(sql, connection)  # noqa: E731
    frames = {
        "board": read("SELECT * FROM projections WHERE avg_type = 'weighted'"),
        "ownership": read("SELECT * FROM ownership"),
        "draft": read("SELECT * FROM draft"),
        "slots": read("SELECT kind, name, count FROM slots"),
    }
    if len(frames["ownership"]):
        for column in ("draft_pick", "draft_round"):
            frames["ownership"][column] = pd.to_numeric(
                frames["ownership"][column], errors="coerce")
    return frames


def _my_slot(me: str, draft: pd.DataFrame, picks: pd.DataFrame,
             teams: int, snake: bool) -> int | None:
    """My draft slot: from the published order, else inferred from my picks."""
    for row in draft.itertuples():
        if me and (getattr(row, "manager", None) == me
                   or getattr(row, "team_name", None) == me):
            return int(row.slot)
    mine = picks[(picks["manager"] == me) | (picks["team_name"] == me)]
    mine = mine[mine["draft_pick"].notna()]
    if len(mine):
        return _slot_of_pick(int(mine["draft_pick"].min()), teams, snake)
    return None


# ---------------------------------------------------------------------------
# Entry point


def recommend(connection: sqlite3.Connection, me: str) -> dict:
    """The advisor's full read: recommendation, plan, and position table.

    ``me`` is the manager or team name the page's MY TEAM select stores.
    Returns ``{"available": False, "reason": ...}`` while the answer cannot
    be computed (no draft order yet and no picks to infer a slot from, or a
    finished/non-snake draft).
    """
    data = _frames(connection)
    draft, slots = data["draft"], data["slots"]
    ownership, board = data["ownership"], data["board"]

    if not len(draft) or not draft["teams"].notna().any():
        return {"available": False, "reason": "no draft found yet"}
    teams = int(draft["teams"].dropna().iloc[0])
    rounds = int(draft["rounds"].dropna().iloc[0])
    draft_type = str(draft["type"].dropna().iloc[0] or "snake")
    if draft_type == "auction":
        return {"available": False, "reason": "auction drafts not supported"}
    snake = draft_type == "snake"

    picks = ownership[ownership["draft_pick"].notna()] if len(ownership) \
        else ownership
    my_slot = _my_slot(me, draft, ownership, teams, snake)
    if my_slot is None:
        return {"available": False,
                "reason": "draft order not posted yet -- your slot appears "
                          "once Sleeper assigns the order (or after your "
                          "first pick)"}

    current = int(picks["draft_pick"].max()) + 1 if len(picks) else 1
    last = teams * rounds
    if current > last:
        return {"available": False, "reason": "draft complete"}

    my_picks = [p for p in _picks_of_slot(my_slot, teams, rounds, snake)
                if p >= current]
    if not my_picks:
        return {"available": False, "reason": "you have no picks left"}
    my_next = my_picks[0]

    # Rosters as they stand, rebuilt from the picks so far.
    starting = _starting_slots(slots)
    rosters = {slot: _Roster(open_slots=list(starting))
               for slot in range(1, teams + 1)}
    for row in picks.sort_values("draft_pick").itertuples():
        slot = _slot_of_pick(int(row.draft_pick), teams, snake)
        held = board[board["sleeper_id"] == str(row.sleeper_id)]
        rosters[slot].take({
            "pos": held["pos"].iloc[0] if len(held) else "?",
            # My own players carry their points so plan totals include them.
            "points": float(held["points"].iloc[0])
            if len(held) and slot == my_slot else 0.0,
            "id": f"pick{row.draft_pick}",
        })

    owned = set(ownership["sleeper_id"].dropna().astype(str)) if len(ownership) else set()
    available = [
        {key: row[key] for key in _BOARD_COLUMNS if key in row}
        for _, row in board.iterrows()
        if str(row.get("sleeper_id")) not in owned
        and pd.notna(row.get("points_vor"))
    ]
    pool = _Pool(available)

    # Roll the board forward to my turn: everyone before me drafts by value.
    # Candidates and the position table both describe the world at MY pick.
    for pick_no in range(current, my_next):
        slot = _slot_of_pick(pick_no, teams, snake)
        player = _model_pick(pool, rosters[slot])
        if player is not None:
            pool.take(player)
            rosters[slot].take(player)
    todo = list(range(my_next, last + 1))

    my_roster = rosters[my_slot]
    bench_stage = my_roster.starters_full

    # Candidates: the best available player at each position I can still
    # start (starters only -- a filled position has fallen out).
    positions = sorted({row["pos"] for row in available})
    candidates = []
    for position in positions:
        if not bench_stage and not my_roster.startable(position):
            continue
        player = pool.best_at(position)
        if player is None:
            continue
        total, plan = _simulate(pool, rosters, todo, teams, snake,
                                my_slot, player)
        candidates.append({"player": player, "total": total, "plan": plan})

    if not candidates:
        return {"available": False, "reason": "no startable players left"}

    # Best finished lineup wins, full stop.  Exact ties (identical totals)
    # fall to the higher-value player, which keeps the choice deterministic.
    candidates.sort(key=lambda c: (-c["total"], -c["player"]["points_vor"]))
    best_total = candidates[0]["total"]
    choice = candidates[0]

    # Position table: cost of deferring each position to my turn after this
    # one, with me taking my best other-position player and the board
    # playing value in between.
    my_following = my_picks[1] if len(my_picks) > 1 else last + 1
    position_table = []
    for position in positions:
        now = pool.best_at(position)
        if now is None:
            continue
        removed = []
        skip = pool.best(lambda r, p=position: r["pos"] != p)
        if skip is not None:
            pool.take(skip)
            removed.append(skip)
        for pick_no in range(my_next + 1, my_following):
            slot = _slot_of_pick(pick_no, teams, snake)
            if slot == my_slot:
                continue
            player = _model_pick(pool, rosters[slot])
            if player is not None:
                pool.take(player)
                removed.append(player)
        later = pool.best_at(position)
        pool.untake(removed)
        position_table.append({
            "pos": position,
            "startable": bench_stage or my_roster.startable(position),
            "best_now": {"player": now["player"],
                         "points_vor": round(now["points_vor"], 1),
                         "sd_pts": None if pd.isna(now.get("sd_pts"))
                         else round(now["sd_pts"], 1),
                         "ceiling": None if pd.isna(now.get("ceiling"))
                         else round(now["ceiling"], 1)},
            "best_later": None if later is None else
            {"player": later["player"],
             "points_vor": round(later["points_vor"], 1)},
            "waiting_cost": None if later is None else
            round(now["points_vor"] - later["points_vor"], 1),
            "dropoff": None if pd.isna(now.get("dropoff"))
            else round(now["dropoff"], 1),
        })
    position_table.sort(key=lambda entry: -(entry["waiting_cost"] or 0))

    pick_of = lambda c: c["player"]  # noqa: E731
    return {
        "available": True,
        "me": me,
        "my_slot": my_slot,
        "stage": "bench" if bench_stage else "starters",
        "current_pick": current,
        "current_label": _pick_label(current, teams),
        "on_clock_slot": _slot_of_pick(current, teams, snake),
        "i_am_on_clock": _slot_of_pick(current, teams, snake) == my_slot,
        "my_next_pick": my_next,
        "my_next_label": _pick_label(my_next, teams),
        "recommendation": {
            "player": pick_of(choice)["player"],
            "pos": pick_of(choice)["pos"],
            "points": round(pick_of(choice)["points"], 1),
            "points_vor": round(pick_of(choice)["points_vor"], 1),
            "plan_total": round(choice["total"], 1),
            "cost_of_next_best": round(
                choice["total"] - candidates[1]["total"], 1)
            if len(candidates) > 1 else None,
        },
        "alternatives": [{
            "player": pick_of(c)["player"], "pos": pick_of(c)["pos"],
            "plan_total": round(c["total"], 1),
            "cost": round(best_total - c["total"], 1),
        } for c in candidates[:5]],
        "plan": [{
            "pick": step["pick"],
            "label": _pick_label(step["pick"], teams),
            "player": step["player"]["player"],
            "pos": step["player"]["pos"],
            "points": round(step["player"]["points"], 1),
            "starter": step["starter"],
        } for step in choice["plan"]],
        "positions": position_table,
    }
