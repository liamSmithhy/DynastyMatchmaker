"""Stage 4 verification: the matchmaker finds real trades, deterministically,
and its format sensitivity is derived rather than written down."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.matchmaker import find_trades, pair_proposals, ring_proposals  # noqa: E402
from src.scoring import score_league  # noqa: E402
from src.sleeper import DictTransport, SleeperClient  # noqa: E402
from src.values import ValueBook  # noqa: E402

FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/league/payloads.json"
LEAGUE_ID = "1048291736450000000"

payloads = json.loads(FIXTURE.read_text())
book = ValueBook()


def load(payloads_: dict) -> tuple:
    league = SleeperClient(transport=DictTransport(payloads_)).load_league(LEAGUE_ID)
    return league, score_league(league, book)


league, scored = load(payloads)
slots = scored.settings.starter_slots
print(f"{league.name} -- {league.format_label}\n")

# ---------------------------------------------------------------- required
print("=" * 74)
print("REQUIRED: a WR-surplus team trading into an RB-surplus team")
print("=" * 74)

wr_teams = sorted(
    (t for t in scored.teams if t.surplus.get("WR", 0) > 0),
    key=lambda t: -t.surplus["WR"],
)
rb_teams = sorted(
    (t for t in scored.teams if t.surplus.get("RB", 0) > 0),
    key=lambda t: -t.surplus["RB"],
)
print(f"  WR surplus: {[(t.roster_id, round(t.surplus['WR'])) for t in wr_teams]}")
print(f"  RB surplus: {[(t.roster_id, round(t.surplus['RB'])) for t in rb_teams]}")

found = False
for a in wr_teams:
    for b in rb_teams:
        if a.roster_id == b.roster_id:
            continue
        for proposal in sorted(
            pair_proposals(a, b, slots, scored.replacement), key=lambda p: -p.score
        ):
            sa, sb = proposal.sides
            out_a = {x.position for x in sa.sends}
            out_b = {x.position for x in sb.sends}
            if "WR" in out_a and "RB" in out_b:
                print(f"\n  FOUND: r{a.roster_id} {a.name} -> r{b.roster_id} {b.name}")
                print(f"    {a.name} sends {[x.label for x in sa.sends]}")
                print(f"    {b.name} sends {[x.label for x in sb.sends]}")
                print(f"    positions: {sorted(out_a)} for {sorted(out_b)}")
                print(f"    {sa.team.name}: lineup {sa.lineup_gain:+,.0f}")
                print(f"    {sb.team.name}: lineup {sb.lineup_gain:+,.0f}")
                found = True
                break
        if found:
            break
    if found:
        break
print(f"\n  WR-for-RB emerges from the math: {'PASS' if found else 'FAIL'}")

# ------------------------------------------------------------ determinism
print("\n" + "=" * 74)
print("DETERMINISM: same input, byte-identical output")
print("=" * 74)


def digest(payloads_: dict) -> str:
    _, sc = load(payloads_)
    props = find_trades(sc, book, limit=10, multi_team=True)
    return "|".join(
        f"{p.score:.6f}:" + ",".join(sorted(p.assets)) for p in props
    )


runs = [digest(copy.deepcopy(payloads)) for _ in range(3)]
print(f"  3 runs identical: {'PASS' if len(set(runs)) == 1 else 'FAIL'}")

# ------------------------------------------ format sensitivity is derived
print("\n" + "=" * 74)
print("NO HARDCODING: flipping the league to superflex inverts QB handling")
print("=" * 74)

sf_payloads = copy.deepcopy(payloads)
positions = sf_payloads[f"league/{LEAGUE_ID}"]["roster_positions"]
positions[positions.index("FLEX")] = "SUPER_FLEX"
sf_league, sf_scored = load(sf_payloads)

print(f"  1QB : {league.format_label}")
print(f"  SF  : {sf_league.format_label}")

hdr = f"\n  {'roster':<24}{'QB surplus 1QB':>16}{'QB surplus SF':>16}"
print(hdr)
print("  " + "-" * (len(hdr) - 3))
for one in sorted(scored.teams, key=lambda t: -t.surplus.get("QB", 0))[:5]:
    sf = sf_scored.by_roster(one.roster_id)
    print(
        f"  {one.name[:23]:<24}{one.surplus.get('QB', 0):>16,.0f}"
        f"{sf.surplus.get('QB', 0):>16,.0f}"
    )

def qb_deployment(sc) -> tuple[int, float]:
    """QBs in lineups, and the share of QB value that is actually deployed.

    Comparing raw surplus across formats is meaningless -- value_2qb is a
    different scale from value_1qb, so idle QB value can rise in superflex just
    because every QB is priced higher. The scale-invariant question is what
    FRACTION of the QB value a league holds actually reaches a lineup.
    """
    started = [p for t in sc.teams for _, p in t.lineup if p is not None and p.position == "QB"]
    held = [p for t in sc.teams for p in t.roster if p.position == "QB"]
    total = sum(p.value for p in held)
    return len(started), (sum(p.value for p in started) / total if total else 0.0)

one_n, one_share = qb_deployment(scored)
sf_n, sf_share = qb_deployment(sf_scored)
print(f"\n  QBs in starting lineups: 1QB {one_n}  ->  SF {sf_n}")
print(f"  share of QB value deployed: 1QB {one_share:.1%}  ->  SF {sf_share:.1%}")
print(
    f"  superflex pulls QBs into lineups: "
    f"{'PASS' if sf_n > one_n and sf_share > one_share else 'FAIL'}"
)

qb_moved_1qb = sum(
    1
    for p in find_trades(scored, book, limit=20)
    for s in p.sides
    for a in s.sends
    if a.position == "QB"
)
qb_moved_sf = sum(
    1
    for p in find_trades(sf_scored, book, limit=20)
    for s in p.sides
    for a in s.sends
    if a.position == "QB"
)
print(f"  QBs appearing in top-20 proposals: 1QB {qb_moved_1qb}  ->  SF {qb_moved_sf}")

# -------------------------------------------------------- both-sides rule
print("\n" + "=" * 74)
print("BOTH SIDES IMPROVE: every proposal, every side, positive weighted gain")
print("=" * 74)
props = find_trades(scored, book, limit=25, multi_team=True)
bad = [
    (i, s.team.name, s.gain)
    for i, p in enumerate(props, 1)
    for s in p.sides
    if s.gain <= 0
]
print(f"  proposals checked: {len(props)}   sides failing: {len(bad)}",
      "PASS" if not bad else f"FAIL {bad}")
mutual = [p.mutuality for p in props]
if mutual:
    print(f"  mutuality range: {min(mutual):.2f} - {max(mutual):.2f}")

windows = {}
for p in props:
    key = " <-> ".join(sorted(s.window for s in p.sides))
    windows[key] = windows.get(key, 0) + 1
print("\n  window pairings found:")
for key, count in sorted(windows.items(), key=lambda kv: -kv[1]):
    print(f"    {key:<28}{count}")

# ------------------------------------------------------------ multi-team
print("\n" + "=" * 74)
print("MULTI-TEAM (paid tier): 3-team rings")
print("=" * 74)
rings = ring_proposals(sorted(scored.teams, key=lambda t: t.roster_id), slots, scored.replacement)
print(f"  rings found: {len(rings)}")
for ring in sorted(rings, key=lambda p: -p.score)[:2]:
    print(f"\n  score {ring.score:,.0f}  mutuality {ring.mutuality:.2f}")
    for side in ring.sides:
        print(
            f"    r{side.team.roster_id} {side.team.name[:20]:<22}[{side.window:<10}]"
            f" sends {side.sends[0].label:<32} gets {side.receives[0].label}"
        )
