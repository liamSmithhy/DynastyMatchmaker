"""Trade generation.

Implements docs/matchmaker-spec.md. Read that first -- it is the algorithm in
plain English and this file is meant to be checkable against it.

Deterministic throughout: no clock, no RNG, sorted iteration, stable tie-breaks.
No model is consulted to find, validate or rank a trade. ``pitch()`` produces
the message to send from a template; swapping in a model call there is the only
place one belongs, and it stays cacheable because the proposal is deterministic.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from .scoring import (
    CONTENDER,
    PEAK_AGE,
    REBUILD,
    RETOOLER,
    STUCK,
    TOP_HEAVY,
    LeagueScore,
    TeamScore,
    ValuedPick,
    ValuedPlayer,
    optimal_lineup,
)
from .sleeper import RosterSlot
from .values import ValueBook

# How each window scores the four deltas of a trade. These are preferences, not
# prices -- the values themselves come from the market. See spec section 5.
#
#   lineup  : weight on improving the starting lineup now
#   market  : weight on winning the raw value exchange
#   youth   : weight on getting younger
#   capital : weight on gaining draft picks
#   tolerance: how far below market this side will accept, as a fraction of the
#              value it sends. A contender will overpay; a rebuilder will not.
WINDOW_WEIGHTS: dict[str, dict[str, float]] = {
    CONTENDER: {"lineup": 1.00, "market": 0.15, "youth": -0.20, "capital": -0.25, "tolerance": 0.18},
    TOP_HEAVY: {"lineup": 0.30, "market": 0.55, "youth": 0.45, "capital": 0.45, "tolerance": 0.05},
    RETOOLER:  {"lineup": 0.80, "market": 0.35, "youth": 0.15, "capital": 0.10, "tolerance": 0.10},
    REBUILD:   {"lineup": 0.05, "market": 0.60, "youth": 0.55, "capital": 0.55, "tolerance": 0.02},
    STUCK:     {"lineup": 0.45, "market": 0.50, "youth": 0.20, "capital": 0.20, "tolerance": 0.08},
}

# Windows that will part with draft capital, and how deep into their pick
# holdings they will go.
PICK_WILLINGNESS = {
    CONTENDER: 0.85,
    TOP_HEAVY: 0.0,
    RETOOLER: 0.30,
    REBUILD: 0.0,
    STUCK: 0.25,
}

AGE_REFERENCE = 27.0  # youth delta is measured against a neutral dynasty age

MAX_PER_SIDE = 2
SIMPLICITY_PENALTY = 0.87  # per asset beyond the first on each side
REPEAT_PENALTY = 0.55      # per asset already used by a better-ranked proposal


# --------------------------------------------------------------------------
# assets
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Asset:
    """A player or a pick, on one value scale."""

    key: str
    label: str
    value: float
    position: str
    age: float | None
    is_pick: bool
    player: ValuedPlayer | None = None
    pick: ValuedPick | None = None

    @property
    def youth(self) -> float:
        """Positive means younger than a neutral dynasty asset."""
        if self.is_pick:
            return 1.0
        if self.age is None:
            return 0.0
        return (AGE_REFERENCE - self.age) / AGE_REFERENCE


def player_asset(vp: ValuedPlayer) -> Asset:
    return Asset(
        key=f"p:{vp.player.sleeper_id}",
        label=f"{vp.name} ({vp.position}{f', {vp.age:.0f}' if vp.age else ''})",
        value=vp.value,
        position=vp.position,
        age=vp.age,
        is_pick=False,
        player=vp,
    )


def pick_asset(vpick: ValuedPick) -> Asset:
    return Asset(
        key=f"k:{vpick.pick.season}:{vpick.pick.round}:{vpick.pick.original_roster_id}",
        label=vpick.label,
        value=vpick.value,
        position="PICK",
        age=None,
        is_pick=True,
        pick=vpick,
    )


# --------------------------------------------------------------------------
# proposals
# --------------------------------------------------------------------------

@dataclass
class Side:
    team: TeamScore
    sends: list[Asset]
    receives: list[Asset]

    lineup_gain: float = 0.0
    market_delta: float = 0.0
    youth_delta: float = 0.0
    capital_delta: float = 0.0
    gain: float = 0.0

    @property
    def window(self) -> str:
        return self.team.window


@dataclass
class Proposal:
    sides: list[Side]
    score: float = 0.0
    joint_gain: float = 0.0
    mutuality: float = 0.0
    rationale: list[str] = field(default_factory=list)

    @property
    def assets(self) -> set[str]:
        return {a.key for side in self.sides for a in side.sends}

    @property
    def size(self) -> int:
        return sum(len(s.sends) for s in self.sides)

    @property
    def is_multi_team(self) -> bool:
        return len(self.sides) > 2


# --------------------------------------------------------------------------
# core mechanics
# --------------------------------------------------------------------------

def _lineup_value(players: Sequence[ValuedPlayer], slots: Sequence[RosterSlot]) -> float:
    return sum(p.value for _, p in optimal_lineup(players, slots) if p is not None)


def lineup_with(
    team: TeamScore,
    slots: Sequence[RosterSlot],
    incoming: Sequence[Asset],
    outgoing: Sequence[Asset],
) -> float:
    """That team's optimal lineup value after the swap.

    Re-solving the lineup is the whole point: it is what tells us an asset is
    worth a starting slot to one team and a bench spot to another, which is the
    difference the trade monetises.
    """
    removed = {a.player.player.sleeper_id for a in outgoing if a.player is not None}
    roster = [p for p in team.roster if p.player.sleeper_id not in removed]
    roster += [a.player for a in incoming if a.player is not None]
    return _lineup_value(roster, slots)


def outbound_candidates(team: TeamScore, replacement: dict[str, float]) -> list[Asset]:
    """What this team can plausibly move.

    Surplus players always. Plus, for a team whose window says this season does
    not matter, the aging starters it ought to be selling -- without this a
    rebuilding team can never trade its 30-year-old for picks, which is the
    single most common dynasty trade there is, and the generator would only ever
    find deals between teams that are already good.

    "Aging" is per position (pillar 2): a 26-year-old back is on the way down
    and a 26-year-old quarterback has not started yet.
    """
    starting = {id(p) for _, p in team.lineup if p is not None}
    weights = WINDOW_WEIGHTS[team.window]
    sells_production = weights["lineup"] < 0.35
    assets: list[Asset] = []

    for vp in team.roster:
        if vp.value <= 0 or vp.player.slot == "IR":
            continue
        if vp.value <= replacement.get(vp.position, 0.0):
            continue
        if id(vp) in starting:
            past_peak = vp.age is not None and vp.age >= PEAK_AGE.get(vp.position, 27.0)
            if not (sells_production and past_peak):
                continue
        assets.append(player_asset(vp))

    willingness = PICK_WILLINGNESS.get(team.window, 0.0)
    if willingness > 0 and team.picks:
        ranked = sorted(team.picks, key=lambda p: (-p.value, p.label))
        # Contenders spend their best picks; the more reluctant a window, the
        # shallower into its holdings it will go.
        budget = max(1, int(round(len(ranked) * willingness)))
        for vpick in ranked[:budget]:
            if vpick.value > 0:
                assets.append(pick_asset(vpick))

    assets.sort(key=lambda a: (-a.value, a.key))
    return assets


def score_side(
    side: Side, slots: Sequence[RosterSlot], weights: dict[str, float]
) -> None:
    """Score one side of a trade in the terms its own window cares about."""
    before = side.team.starter_value
    after = lineup_with(side.team, slots, side.receives, side.sends)
    side.lineup_gain = after - before

    sent = sum(a.value for a in side.sends)
    got = sum(a.value for a in side.receives)
    side.market_delta = got - sent

    side.youth_delta = (
        sum(a.value * a.youth for a in side.receives)
        - sum(a.value * a.youth for a in side.sends)
    )
    side.capital_delta = (
        sum(a.value for a in side.receives if a.is_pick)
        - sum(a.value for a in side.sends if a.is_pick)
    )

    side.gain = (
        weights["lineup"] * side.lineup_gain
        + weights["market"] * side.market_delta
        + weights["youth"] * side.youth_delta
        + weights["capital"] * side.capital_delta
    )


def side_accepts(side: Side, weights: dict[str, float]) -> bool:
    """Would this manager say yes?

    Three independent hurdles: the deal must be a net gain in that team's own
    terms, the value gap must be inside what its window tolerates, and it must
    not be selling something its own lineup depends on.
    """
    if side.gain <= 0:
        return False

    sent = sum(a.value for a in side.sends)
    if sent > 0 and side.market_delta < -weights["tolerance"] * sent:
        return False

    # Selling a starter is allowed only if the return rebuilds the lineup. This
    # is what stops the generator gutting a roster to balance a spreadsheet.
    if side.lineup_gain < 0 and abs(side.lineup_gain) > 0.35 * max(sent, 1.0):
        return False

    return True


# --------------------------------------------------------------------------
# two-team search
# --------------------------------------------------------------------------

def _useful_to(
    team: TeamScore, slots: Sequence[RosterSlot], asset: Asset
) -> float:
    """Lineup gain this asset would give that team on its own."""
    if asset.is_pick or asset.player is None:
        return 0.0
    return lineup_with(team, slots, [asset], []) - team.starter_value


def _packages(assets: Sequence[Asset], max_size: int) -> Iterable[tuple[Asset, ...]]:
    for size in range(1, max_size + 1):
        yield from itertools.combinations(assets, size)


def _cancel(
    a_pkg: Sequence[Asset], b_pkg: Sequence[Asset]
) -> tuple[list[Asset], list[Asset]]:
    """Drop assets that appear identically on both sides.

    Future picks are fungible by label: two different teams' "2028 1st" are the
    same asset to a trade partner, so a package that sends one and receives the
    other is proposing a no-op. Left in, it turns a sensible deal into something
    no manager would read past.
    """
    a_out, b_rest = [], list(b_pkg)
    for asset in a_pkg:
        match = next((x for x in b_rest if x.label == asset.label), None)
        if match is not None:
            b_rest.remove(match)
        else:
            a_out.append(asset)
    return a_out, b_rest


def pair_proposals(
    a: TeamScore,
    b: TeamScore,
    slots: Sequence[RosterSlot],
    replacement: dict[str, float],
    max_per_side: int = MAX_PER_SIDE,
    beam: int = 6,
) -> list[Proposal]:
    """Every valid trade between two teams, cheapest packages first."""
    a_out = outbound_candidates(a, replacement)
    b_out = outbound_candidates(b, replacement)
    if not a_out or not b_out:
        return []

    # Only carry assets the other side can actually use. This is the pruning
    # that keeps the search small and is also the substance of the algorithm:
    # an asset with no lineup gain and no window appeal is not a trade.
    a_useful = sorted(
        (x for x in a_out if _useful_to(b, slots, x) > 0 or x.is_pick),
        key=lambda x: (-_useful_to(b, slots, x), -x.value, x.key),
    )[:beam]
    b_useful = sorted(
        (x for x in b_out if _useful_to(a, slots, x) > 0 or x.is_pick),
        key=lambda x: (-_useful_to(a, slots, x), -x.value, x.key),
    )[:beam]
    if not a_useful or not b_useful:
        return []

    wa = WINDOW_WEIGHTS[a.window]
    wb = WINDOW_WEIGHTS[b.window]
    out: list[Proposal] = []
    # Cancellation means several raw package pairs collapse to the same net
    # trade; keep one of each.
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()

    for raw_a in _packages(a_useful, max_per_side):
        for raw_b in _packages(b_useful, max_per_side):
            a_pkg, b_pkg = _cancel(raw_a, raw_b)
            if not a_pkg or not b_pkg:
                continue
            signature = (
                tuple(sorted(x.key for x in a_pkg)),
                tuple(sorted(x.key for x in b_pkg)),
            )
            if signature in seen:
                continue
            seen.add(signature)
            a_value = sum(x.value for x in a_pkg)
            b_value = sum(x.value for x in b_pkg)
            bigger = max(a_value, b_value)
            if bigger <= 0:
                continue
            # Cheap pre-filter: anything wider than the loosest tolerance in
            # play cannot clear either side's check.
            slack = max(wa["tolerance"], wb["tolerance"]) + 0.05
            if abs(a_value - b_value) > slack * bigger:
                continue

            side_a = Side(team=a, sends=list(a_pkg), receives=list(b_pkg))
            side_b = Side(team=b, sends=list(b_pkg), receives=list(a_pkg))
            score_side(side_a, slots, wa)
            score_side(side_b, slots, wb)
            if not (side_accepts(side_a, wa) and side_accepts(side_b, wb)):
                continue

            proposal = Proposal(sides=[side_a, side_b])
            _finalize(proposal)
            out.append(proposal)

    return out


# --------------------------------------------------------------------------
# three-team rings (paid tier)
# --------------------------------------------------------------------------

def ring_proposals(
    teams: Sequence[TeamScore],
    slots: Sequence[RosterSlot],
    replacement: dict[str, float],
    beam: int = 6,
    width: int = 4,
) -> list[Proposal]:
    """Three-team cycles: A ships to B, B to C, C to A.

    These exist to catch the case bilateral matching cannot see -- A has what B
    wants, B has what C wants, C has what A wants, and no two of them can trade.

    Candidates per edge are ranked by lineup gain, which does not correlate with
    market value, so the legs of a cycle are rarely value-matched by accident.
    The search therefore keeps a wide beam and leans on each side's own window
    tolerance to decide acceptability; the spread band below is only a cheap
    prune, not the real test.
    """
    out: list[Proposal] = []
    best: dict[tuple[int, int], list[Asset]] = {}
    for sender in teams:
        candidates = outbound_candidates(sender, replacement)
        for receiver in teams:
            if sender.roster_id == receiver.roster_id:
                continue
            useful = sorted(
                (x for x in candidates if _useful_to(receiver, slots, x) > 0),
                key=lambda x: (-_useful_to(receiver, slots, x), -x.value, x.key),
            )[:beam]
            if useful:
                best[(sender.roster_id, receiver.roster_id)] = useful

    ordered = sorted(teams, key=lambda t: t.roster_id)
    for a, b, c in itertools.permutations(ordered, 3):
        # A cycle and its reverse are the same trade; fix an orientation.
        if not (a.roster_id < b.roster_id and a.roster_id < c.roster_id):
            continue
        legs = [
            best.get((a.roster_id, b.roster_id)),
            best.get((b.roster_id, c.roster_id)),
            best.get((c.roster_id, a.roster_id)),
        ]
        if not all(legs):
            continue
        for x, y, z in itertools.product(legs[0][:width], legs[1][:width], legs[2][:width]):
            values = [x.value, y.value, z.value]
            if max(values) <= 0:
                continue
            if (max(values) - min(values)) > 0.35 * max(values):
                continue
            sides = [
                Side(team=a, sends=[x], receives=[z]),
                Side(team=b, sends=[y], receives=[x]),
                Side(team=c, sends=[z], receives=[y]),
            ]
            ok = True
            for side in sides:
                weights = WINDOW_WEIGHTS[side.window]
                score_side(side, slots, weights)
                if not side_accepts(side, weights):
                    ok = False
                    break
            if not ok:
                continue
            proposal = Proposal(sides=sides)
            _finalize(proposal)
            # Rings are materially harder to close than two-team deals.
            proposal.score *= 0.6
            out.append(proposal)
    return out


# --------------------------------------------------------------------------
# scoring and ranking
# --------------------------------------------------------------------------

def _finalize(proposal: Proposal) -> None:
    gains = [s.gain for s in proposal.sides]
    proposal.joint_gain = sum(gains)
    proposal.mutuality = (min(gains) / max(gains)) if max(gains) > 0 else 0.0
    simplicity = SIMPLICITY_PENALTY ** max(0, proposal.size - len(proposal.sides))
    proposal.score = proposal.joint_gain * proposal.mutuality * simplicity
    proposal.rationale = [_reason(side) for side in proposal.sides]


def _reason(side: Side) -> str:
    """Why this side says yes, in the terms its window cares about."""
    bits: list[str] = []
    if side.lineup_gain > 1:
        bits.append(f"starting lineup +{side.lineup_gain:,.0f}")
    elif side.lineup_gain < -1:
        bits.append(f"lineup {side.lineup_gain:,.0f}")
    if abs(side.market_delta) > 1:
        sign = "+" if side.market_delta > 0 else ""
        bits.append(f"market {sign}{side.market_delta:,.0f}")
    if side.capital_delta > 1:
        bits.append(f"capital +{side.capital_delta:,.0f}")
    elif side.capital_delta < -1:
        bits.append(f"capital {side.capital_delta:,.0f}")
    if side.youth_delta > 100:
        bits.append("gets younger")
    elif side.youth_delta < -100:
        bits.append("gets older")
    return f"{side.team.name} [{side.window}]: " + ", ".join(bits or ["marginal"])


def rank(proposals: Sequence[Proposal], limit: int) -> list[Proposal]:
    """Best first, with a penalty for reusing assets already spoken for.

    Without the freshness term the list fills with twelve variations on trading
    the same receiver, which is one idea presented twelve times.
    """
    remaining = sorted(
        proposals,
        key=lambda p: (
            -p.score,
            p.size,
            tuple(sorted(s.team.roster_id for s in p.sides)),
            tuple(sorted(p.assets)),
        ),
    )
    chosen: list[Proposal] = []
    used: set[str] = set()
    while remaining and len(chosen) < limit:
        best_idx, best_adj = 0, float("-inf")
        for i, proposal in enumerate(remaining):
            overlap = len(proposal.assets & used)
            adjusted = proposal.score * (REPEAT_PENALTY ** overlap)
            if adjusted > best_adj:
                best_idx, best_adj = i, adjusted
        picked = remaining.pop(best_idx)
        chosen.append(picked)
        used |= picked.assets
    return chosen


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def find_trades(
    scored: LeagueScore,
    book: ValueBook,
    roster_id: int | None = None,
    limit: int = 10,
    multi_team: bool = False,
    max_per_side: int = MAX_PER_SIDE,
) -> list[Proposal]:
    slots = scored.settings.starter_slots
    replacement = scored.replacement
    teams = sorted(scored.teams, key=lambda t: t.roster_id)

    proposals: list[Proposal] = []
    for a, b in itertools.combinations(teams, 2):
        if roster_id is not None and roster_id not in (a.roster_id, b.roster_id):
            continue
        proposals += pair_proposals(
            a, b, slots, replacement, max_per_side=max_per_side
        )

    if multi_team:
        rings = ring_proposals(teams, slots, replacement)
        if roster_id is not None:
            rings = [
                p for p in rings
                if any(s.team.roster_id == roster_id for s in p.sides)
            ]
        proposals += rings

    return rank(proposals, limit)


# --------------------------------------------------------------------------
# pitch text
# --------------------------------------------------------------------------

def pitch(proposal: Proposal, from_side: int = 0) -> str:
    """The message to send.

    Template-generated and deterministic. This is the ONE place in the product
    where a model call belongs -- swap this body for one and the output stays
    cacheable, because the proposal it describes is reproducible.
    """
    me = proposal.sides[from_side]
    others = [s for i, s in enumerate(proposal.sides) if i != from_side]
    them = others[0]

    giving = " + ".join(a.label for a in me.sends)
    getting = " + ".join(a.label for a in me.receives)

    opener = {
        CONTENDER: "I'm going for it this year and need help in the lineup.",
        TOP_HEAVY: "I'm trying to get younger without falling out of it.",
        RETOOLER: "I've got depth I can't start and I'd rather consolidate.",
        REBUILD: "I'm building for a couple of years out.",
        STUCK: "I'm trying to pick a direction and this helps.",
    }[them.window]

    why = []
    if them.lineup_gain > 1:
        why.append(f"it upgrades your starting lineup by about {them.lineup_gain:,.0f}")
    if them.capital_delta > 1:
        why.append("you pick up draft capital")
    if them.youth_delta > 100:
        why.append("you get younger")
    if them.market_delta > 1:
        why.append("you come out ahead on value")
    reason = "; ".join(why) or "it fits what you're doing"

    lines = [
        f"Hey — {opener}",
        "",
        f"I'd send you: {giving}",
        f"You'd send me: {getting}",
        "",
        f"From your side, {reason}. "
        f"On my end this is about {'the lineup' if me.lineup_gain > 0 else 'the long game'}.",
        "",
        "Open to tweaking either side if the shape isn't right.",
    ]
    return "\n".join(lines)


def render_proposal(proposal: Proposal, index: int) -> str:
    tag = "3-TEAM" if proposal.is_multi_team else "2-TEAM"
    lines = [
        f"{'=' * 74}",
        f"#{index}  [{tag}]  score {proposal.score:,.0f}   "
        f"mutuality {proposal.mutuality:.2f}",
        f"{'=' * 74}",
    ]
    for side in proposal.sides:
        lines.append(
            f"  {side.team.name} (roster {side.team.roster_id}) [{side.window}]"
        )
        for asset in side.sends:
            lines.append(f"      sends    {asset.label:<38}{asset.value:>9,.0f}")
        for asset in side.receives:
            lines.append(f"      gets     {asset.label:<38}{asset.value:>9,.0f}")
    lines.append("")
    for reason in proposal.rationale:
        lines.append(f"  {reason}")
    lines.append("")
    lines.append("  --- message to send ---")
    for line in pitch(proposal).splitlines():
        lines.append(f"  {line}")
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "find_trades",
    "pair_proposals",
    "ring_proposals",
    "rank",
    "pitch",
    "render_proposal",
    "Proposal",
    "Side",
    "Asset",
    "outbound_candidates",
    "lineup_with",
    "WINDOW_WEIGHTS",
]
