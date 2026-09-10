"""Stage 1 verification: live DynastyProcess load, player and pick values."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.values import ValueBook

t0 = time.time()
vb = ValueBook()
print(f"loaded {len(vb.players)} players in {time.time()-t0:.2f}s")
for n in vb.notes:
    print("  note:", n)

print("\n=== REQUIRED CHECK: 1QB vs Superflex ===")
print(f"{'player':<22}{'pos':<5}{'age':>5}{'1QB':>9}{'SF':>9}{'SF/1QB':>9}")
for name in ["Ja'Marr Chase", "Bijan Robinson", "Justin Herbert"]:
    p = vb.get(name)
    if not p:
        print(f"{name:<22} NOT FOUND"); continue
    r = p.value_2qb / p.value_1qb if p.value_1qb else float('inf')
    print(f"{p.name:<22}{p.position:<5}{p.age:>5}{p.value_1qb:>9,.0f}{p.value_2qb:>9,.0f}{r:>9.2f}x")

chase, bijan, herb = vb.get("Ja'Marr Chase"), vb.get("Bijan Robinson"), vb.get("Justin Herbert")
ok = herb.value_1qb < chase.value_1qb * 0.5 and herb.value_2qb > herb.value_1qb * 1.5
print("\nHerbert dramatically lower in 1QB:", "PASS" if ok else "FAIL")

print("\n=== sleeper crosswalk ===")
for name in ["Ja'Marr Chase", "Bijan Robinson", "Justin Herbert"]:
    p = vb.get(name)
    print(f"  {p.name:<20} sleeper_id={p.sleeper_id} fp_id={p.fp_id}")
    assert vb.get_by_sleeper(p.sleeper_id) is p
covered = sum(1 for p in vb.players if p.sleeper_id)
print(f"  crosswalk coverage: {covered}/{len(vb.players)} = {covered/len(vb.players):.1%}")

print("\n=== picks on the player scale (gotcha 1) ===")
print(f"{'label':<22}{'1QB':>9}{'SF':>9}  nearest-player comparison (1QB)")
def nearest(v):
    return min(vb.players, key=lambda p: abs(p.value_1qb - v))
for label in ["2026 Pick 1.01","2026 Pick 1.05","2026 Pick 1.12","2026 Pick 2.06",
              "2027 Early 1st","2027 Mid 1st","2027 Late 1st","2027 2nd","2028 1st","2028 3rd","2029 1st"]:
    v1, v2 = vb.pick_value(label), vb.pick_value(label, superflex=True)
    n = nearest(v1)
    print(f"{label:<22}{v1:>9,.0f}{v2:>9,.0f}  ~ {n.name} ({n.position}, {n.value_1qb:,.0f})")

print("\n=== non-12-team leagues (no hardcoding) ===")
for teams in (10, 12, 14):
    lbl = vb.pick_label(2026, 1, teams, teams=teams)
    print(f"  {teams}-team, last pick of round 1 -> {lbl:<18} = {vb.pick_value(lbl, teams=teams):>8,.0f}")
print("  14-team 1.13 (no such row in file):", f"{vb.pick_value('2026 Pick 1.13', teams=14):,.0f}")

print("\n=== monotonicity: value must never rise as picks get later ===")
seq = [vb.pick_value(f"2026 Pick {r}.{s:02d}") for r in (1,2,3,4,5) for s in range(1,13)]
bad = [i for i in range(1,len(seq)) if seq[i] > seq[i-1] + 1e-9]
print("  violations:", len(bad), "PASS" if not bad else "FAIL")

print("\n=== label horizons (gotcha 2) ===")
for season in (2026, 2027, 2028, 2029):
    print(f"  {season} r1 slot3 of 12 -> {vb.pick_label(season,1,3,12)}")

print("\n=== 12h cache ===")
t1 = time.time(); ValueBook(); print(f"  second load (cache hit): {time.time()-t1:.3f}s")
