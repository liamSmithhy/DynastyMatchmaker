"""Roster scoring: deployed value, held value, draft capital, window.

Everything here is arithmetic over the normalized league and the value book.
No network, no model calls, no league-specific constants.

The two numbers that matter are ``starter_value`` (what a roster can actually
deploy this week) and ``total_value`` (what it is worth on the open market).
Pillar 4 lives in the gap between their ranks: two teams can hold nearly the
same total value and be in opposite situations.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .sleeper import (
    OFFENSE_POSITIONS,
    League,
    LeagueSettings,
    Pick,
    RosterPlayer,
    RosterSlot,
    Team,
    label_pick,
)
from .values import PlayerValue, ValueBook

# Windows, in the order the framework states them.
CONTENDER = "CONTENDER"
TOP_HEAVY = "TOP-HEAVY"
RETOOLER = "RETOOLER"
STUCK = "STUCK"
REBUILD = "REBUILD"

WINDOW_POSTURE = {
    CONTENDER: "push",
    TOP_HEAVY: "mortgaged, no reload",
    RETOOLER: "consolidate",
    STUCK: "pick a direction",
    REBUILD: "hold",
}

# Age at which a player stops being an appreciating asset, by position. RBs
# fall off a cliff, receivers age gracefully, quarterbacks barely age at all.
PEAK_AGE = {"QB": 30.0, "RB": 25.0, "WR": 27.0, "TE": 27.0}
YOUNG_AGE = 25.0


@dataclass
class ValuedPlayer:
    """A rostered player joined to a value, with the join method recorded."""

    player: RosterPlayer
    value: float
    matched_by: str  # sleeper | name | unmatched
    record: PlayerValue | None = None

    @property
    def position(self) -> str:
        return self.player.position or (self.record.position if self.record else "")

    @property
    def name(self) -> str:
        return self.player.name

    @property
    def age(self) -> float | None:
        if self.record and self.record.age is not None:
            return self.record.age
        return self.player.age


@dataclass
class ValuedPick:
    pick: Pick
    label: str
    value: float


@dataclass
class TeamScore:
    team: Team
    starters: list[ValuedPlayer]          # the optimal lineup, not the set lineup
    lineup: list[tuple[RosterSlot, ValuedPlayer | None]]
    roster: list[ValuedPlayer]
    picks: list[ValuedPick]

    starter_value: float = 0.0
    player_value: float = 0.0
    pick_value: float = 0.0
    total_value: float = 0.0

    overall_rank: int = 0
    starter_rank: int = 0
    pick_rank: int = 0
    window: str = STUCK

    age: float = 0.0            # value-weighted
    young_share: float = 0.0    # share of player value held by under-25s
    capital_share: float = 0.0  # picks as a share of total value

    surplus: dict[str, float] = field(default_factory=dict)
    deficit: dict[str, float] = field(default_factory=dict)

    @property
    def roster_id(self) -> int:
        return self.team.roster_id

    @property
    def name(self) -> str:
        return self.team.team_name

    @property
    def rank_gap(self) -> int:
        """Positive means the lineup outranks the assets (mortgaged)."""
        return self.overall_rank - self.starter_rank

    @property
    def posture(self) -> str:
        return WINDOW_POSTURE[self.window]


@dataclass
class LeagueScore:
    league: League
    teams: list[TeamScore]
    demand: dict[str, float]
    replacement: dict[str, float]
    coverage: float
    coverage_skill: float
    unmatched: list[RosterPlayer]
    position_weights: dict[str, float] = field(default_factory=dict)

    def by_roster(self, roster_id: int) -> TeamScore | None:
        return next((t for t in self.teams if t.roster_id == roster_id), None)

    @property
    def settings(self) -> LeagueSettings:
        return self.league.settings


# --------------------------------------------------------------------------
# joining rosters to values (gotcha 6)
# --------------------------------------------------------------------------

def value_player(
    player: RosterPlayer,
    book: ValueBook,
    superflex: bool,
    weights: dict[str, float] | None = None,
) -> ValuedPlayer:
    """Join on sleeper_id, fall back to a normalized name.

    ``weights`` scales value by position. The consensus board prices a league
    format; it cannot price a league's *market*, and those differ. A commissioner
    who knows nobody in their league will pay for a quarterback can say so here
    rather than have the tool keep proposing quarterback trades nobody accepts.

    It is a knob, not a rule: no position is named anywhere in this module, the
    default is 1.0 for everything, and the same mechanism raises tight ends in a
    TE-premium league as easily as it lowers quarterbacks in this one.
    """
    scale = 1.0
    record = book.get_by_sleeper(player.sleeper_id)
    matched = "sleeper"
    if record is None:
        record = book.get(player.name)
        matched = "name"
    if record is None:
        return ValuedPlayer(player, 0.0, "unmatched", None)
    if weights:
        scale = weights.get(record.position or player.position, 1.0)
    return ValuedPlayer(player, record.value(superflex) * scale, matched, record)


# --------------------------------------------------------------------------
# optimal lineup
# --------------------------------------------------------------------------

def optimal_lineup(
    players: Sequence[ValuedPlayer], slots: Sequence[RosterSlot]
) -> list[tuple[RosterSlot, ValuedPlayer | None]]:
    """Fill the starting slots to maximise total value.

    This is a transversal matroid, so taking players in descending value and
    augmenting is exactly optimal -- no need to trust that the manager set a
    good lineup, and no assumption that flex eligibility sets nest (REC_FLEX and
    WRRB_FLEX genuinely cross).
    """
    ranked = sorted(players, key=lambda p: -p.value)
    assigned: dict[int, int] = {}  # slot index -> player index

    def augment(player_idx: int, seen: set[int]) -> bool:
        position = ranked[player_idx].position
        for slot_idx, slot in enumerate(slots):
            if position not in slot.eligible or slot_idx in seen:
                continue
            seen.add(slot_idx)
            if slot_idx not in assigned or augment(assigned[slot_idx], seen):
                assigned[slot_idx] = player_idx
                return True
        return False

    for idx, player in enumerate(ranked):
        if player.value <= 0:
            continue
        augment(idx, set())

    return [
        (slot, ranked[assigned[i]] if i in assigned else None)
        for i, slot in enumerate(slots)
    ]


# --------------------------------------------------------------------------
# positional demand and replacement level
# --------------------------------------------------------------------------

def positional_demand(
    lineups: Iterable[list[tuple[RosterSlot, ValuedPlayer | None]]],
    settings: LeagueSettings,
) -> dict[str, float]:
    """How many startable bodies at each position the league actually demands.

    Dedicated slots are counted directly. Flex demand is measured rather than
    assumed: we look at which positions actually win flex slots across the whole
    league under this scoring. In full PPR that lands on receivers, in non-PPR it
    shifts to backs, and neither outcome is written down anywhere -- it falls out
    of the values under the league's own settings.
    """
    demand = {pos: 0.0 for pos in OFFENSE_POSITIONS}
    for slot in settings.starter_slots:
        if len(slot.eligible) == 1:
            pos = next(iter(slot.eligible))
            if pos in demand:
                demand[pos] += 1.0

    flex_wins: dict[str, int] = {pos: 0 for pos in OFFENSE_POSITIONS}
    flex_total = 0
    flex_slot_count = len(settings.flex_slots())
    for lineup in lineups:
        for slot, player in lineup:
            if len(slot.eligible) > 1 and player is not None:
                if player.position in flex_wins:
                    flex_wins[player.position] += 1
                    flex_total += 1

    if flex_total:
        for pos in demand:
            demand[pos] += flex_slot_count * (flex_wins[pos] / flex_total)
    elif flex_slot_count:
        for slot in settings.flex_slots():
            eligible = [p for p in slot.eligible if p in demand]
            for pos in eligible:
                demand[pos] += 1.0 / len(eligible)

    return demand


def replacement_levels(
    scores: Sequence[TeamScore], demand: dict[str, float], teams: int
) -> dict[str, float]:
    """Value of the first player at each position the league cannot start.

    This is the baseline that makes "surplus" mean something. A team's fourth
    startable receiver is only surplus if receivers of that quality are scarce
    league-wide, and scarcity is a property of the league, not of the position.
    """
    levels: dict[str, float] = {}
    for pos in OFFENSE_POSITIONS:
        pool = sorted(
            (p.value for s in scores for p in s.roster if p.position == pos and p.value > 0),
            reverse=True,
        )
        if not pool:
            levels[pos] = 0.0
            continue
        idx = int(round(demand.get(pos, 0.0) * teams))
        idx = max(0, min(idx, len(pool) - 1))
        levels[pos] = pool[idx]
    return levels


# --------------------------------------------------------------------------
# surplus and deficit
# --------------------------------------------------------------------------

def positional_shape(
    score: TeamScore, demand: dict[str, float], replacement: dict[str, float]
) -> tuple[dict[str, float], dict[str, float]]:
    """Where a roster holds value it cannot deploy, and where it is short.

    Surplus is the market value of startable-quality players who do not make
    this team's optimal lineup. That framing does the format work by itself: in
    a 1QB league a good backup quarterback never reaches the lineup, so his
    entire value is surplus, which is exactly what "backup QBs are dead roster
    spots" means in arithmetic. In superflex the same player starts and his
    surplus is zero. Nothing about either case is written down.

    "Startable-quality" is the league's own replacement level, so a fifth
    receiver who would not start anywhere is not counted as an asset. Deficit is
    measured in the same units -- how far below replacement a starting slot is
    filled -- so the two sides of a trade compare directly.
    """
    surplus: dict[str, float] = {}
    deficit: dict[str, float] = {}

    starting_ids = {id(p) for _, p in score.lineup if p is not None}

    for player in score.roster:
        pos = player.position
        if pos not in OFFENSE_POSITIONS or id(player) in starting_ids:
            continue
        floor = replacement.get(pos, 0.0)
        # IR is not deployable value anybody is trading for right now.
        if player.player.slot == "IR":
            continue
        if player.value > floor:
            surplus[pos] = surplus.get(pos, 0.0) + player.value

    for slot, player in score.lineup:
        # Charge a slot against the position it most naturally serves; for a
        # flex that is whichever eligible position is scarcest in this league.
        eligible = [p for p in slot.eligible if p in OFFENSE_POSITIONS]
        if not eligible:
            continue
        pos = player.position if player is not None else max(
            eligible, key=lambda p: replacement.get(p, 0.0)
        )
        floor = replacement.get(pos, 0.0)
        have = player.value if player is not None else 0.0
        if have < floor:
            deficit[pos] = deficit.get(pos, 0.0) + (floor - have)

    return surplus, deficit


# --------------------------------------------------------------------------
# window classification (pillar 4)
# --------------------------------------------------------------------------

def classify_window(
    starter_rank: int, overall_rank: int, teams: int, capital_share: float
) -> str:
    """Read the window off the GAP between deployed and held rank.

    The raw numbers only break the tie once the gap says a roster is balanced.
    A team ranked 4th in starters and 4th in total value is a different animal
    from one ranked 4th and 11th, however similar their totals look.
    """
    threshold = max(2, round(teams / 5))
    top = max(1, round(teams / 3))
    bottom = teams - top + 1
    gap = overall_rank - starter_rank

    # Lineup outruns the assets: winning now with nothing left to reload with.
    if gap >= threshold and overall_rank > top:
        return TOP_HEAVY
    # Assets outrun the lineup: value sitting on the bench or in picks.
    if gap <= -threshold and starter_rank > top:
        return RETOOLER
    if starter_rank <= top and overall_rank <= top:
        return CONTENDER
    if starter_rank >= bottom and overall_rank >= bottom:
        return REBUILD if capital_share >= 0.2 else STUCK
    return STUCK


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def score_league(
    league: League,
    book: ValueBook,
    position_weights: dict[str, float] | None = None,
) -> LeagueScore:
    settings = league.settings
    superflex = settings.superflex
    weights = {k.upper(): float(v) for k, v in (position_weights or {}).items()}
    scores: list[TeamScore] = []
    unmatched: list[RosterPlayer] = []

    for team in league.teams:
        roster = [value_player(p, book, superflex, weights) for p in team.all_players]
        unmatched += [vp.player for vp in roster if vp.matched_by == "unmatched"]

        lineup = optimal_lineup(roster, settings.starter_slots)
        starters = [p for _, p in lineup if p is not None]

        picks = []
        for pick in team.picks:
            label = label_pick(pick, book, settings.teams)
            picks.append(
                ValuedPick(pick, label, book.pick_value(label, superflex, settings.teams))
            )

        score = TeamScore(
            team=team, starters=starters, lineup=lineup, roster=roster, picks=picks
        )
        score.starter_value = sum(p.value for p in starters)
        score.player_value = sum(p.value for p in roster)
        score.pick_value = sum(p.value for p in picks)
        score.total_value = score.player_value + score.pick_value

        weighted = [(p.value, p.age) for p in roster if p.value > 0 and p.age is not None]
        total_w = sum(v for v, _ in weighted)
        score.age = (sum(v * a for v, a in weighted) / total_w) if total_w else 0.0
        score.young_share = (
            sum(v for v, a in weighted if a < YOUNG_AGE) / total_w if total_w else 0.0
        )
        score.capital_share = (
            score.pick_value / score.total_value if score.total_value else 0.0
        )
        scores.append(score)

    _rank(scores, "total_value", "overall_rank")
    _rank(scores, "starter_value", "starter_rank")
    _rank(scores, "pick_value", "pick_rank")

    demand = positional_demand((s.lineup for s in scores), settings)
    replacement = replacement_levels(scores, demand, settings.teams)

    for score in scores:
        score.window = classify_window(
            score.starter_rank, score.overall_rank, settings.teams, score.capital_share
        )
        score.surplus, score.deficit = positional_shape(score, demand, replacement)

    all_players = [p for s in scores for p in s.roster]
    skill = [p for p in all_players if p.position in OFFENSE_POSITIONS]
    coverage = (
        sum(1 for p in all_players if p.matched_by != "unmatched") / len(all_players)
        if all_players else 0.0
    )
    coverage_skill = (
        sum(1 for p in skill if p.matched_by != "unmatched") / len(skill) if skill else 0.0
    )

    return LeagueScore(
        league=league,
        teams=scores,
        position_weights=weights,
        demand=demand,
        replacement=replacement,
        coverage=coverage,
        coverage_skill=coverage_skill,
        unmatched=unmatched,
    )


def _rank(scores: list[TeamScore], attr: str, target: str) -> None:
    for i, score in enumerate(sorted(scores, key=lambda s: -getattr(s, attr)), start=1):
        setattr(score, target, i)


__all__ = [
    "score_league",
    "LeagueScore",
    "TeamScore",
    "ValuedPlayer",
    "ValuedPick",
    "optimal_lineup",
    "positional_demand",
    "replacement_levels",
    "positional_shape",
    "classify_window",
    "value_player",
    "CONTENDER",
    "TOP_HEAVY",
    "RETOOLER",
    "STUCK",
    "REBUILD",
    "WINDOW_POSTURE",
]
