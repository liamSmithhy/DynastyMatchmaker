"""Stage 2 verification: normalized League from Sleeper payloads.

Tries the live API first with the real username. Falls back to the fixture when
the network refuses, and says loudly which one it used.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.sleeper import (  # noqa: E402
    DictTransport,
    SleeperClient,
    SleeperError,
    detect_settings,
    label_pick,
)
from src.values import ValueBook  # noqa: E402

USERNAME = sys.argv[1] if len(sys.argv) > 1 else "liamsmithh"
FIXTURE = Path(__file__).resolve().parents[1] / "tests/fixtures/league/payloads.json"


def get_client() -> tuple[SleeperClient, str]:
    live = SleeperClient()
    try:
        live.user(USERNAME)
        return live, "LIVE api.sleeper.app"
    except SleeperError as exc:
        print("!" * 72)
        print(f"! LIVE SLEEPER UNREACHABLE: {exc}")
        print("! Falling back to the fixture league. Re-run this script where")
        print("! api.sleeper.app is reachable to verify against the real league.")
        print("!" * 72)
        payloads = json.loads(FIXTURE.read_text())
        return SleeperClient(transport=DictTransport(payloads)), "FIXTURE"


client, source = get_client()
print(f"\nsource: {source}\n")

leagues = client.leagues(USERNAME)
print(f"leagues for {USERNAME}: {len(leagues)}")
for lg in leagues:
    print(f"  {lg['league_id']}  {lg['name']}  ({lg.get('season')})")

league = client.load_league(leagues[0]["league_id"])
s = league.settings

print(f"\n=== {league.name} ===")
print(f"  league_id        {league.league_id}")
print(f"  season / status  {league.season} / {league.status}")
print(f"  previous_league  {league.previous_league_id}")
print(f"\n  FORMAT: {league.format_label}")
print(f"    teams          {s.teams}")
print(f"    superflex      {s.superflex}  (qb slots {s.qb_slots:g})")
print(f"    ppr            {s.ppr:g} -> {s.ppr_label}")
print(f"    te premium     {s.te_premium:g}")
print(f"    starters       {s.starters_per_team}  {[x.code for x in s.starter_slots]}")
print(f"    bench/taxi/ir  {s.bench_slots} / {s.taxi_slots} / {s.ir_slots}")
print(f"    draft rounds   {s.draft_rounds}")

print(f"\n=== teams ({len(league.teams)}) ===")
hdr = f"{'#':>2} {'team':<26}{'manager':<14}{'rec':>7}{'ST':>4}{'BN':>4}{'TX':>3}{'IR':>3}{'TOT':>5}{'PICKS':>7}"
print(hdr)
print("-" * len(hdr))
for t in sorted(league.teams, key=lambda t: t.roster_id):
    print(
        f"{t.roster_id:>2} {t.team_name[:25]:<26}{t.manager[:13]:<14}{t.record:>7}"
        f"{len(t.starters):>4}{len(t.bench):>4}{len(t.taxi):>3}{len(t.ir):>3}"
        f"{len(t.all_players):>5}{len(t.picks):>7}"
    )

print("\n=== gotcha 5: roster arrays are disjoint after splitting ===")
bad = []
for t in league.teams:
    groups = [
        {p.sleeper_id for p in t.starters},
        {p.sleeper_id for p in t.bench},
        {p.sleeper_id for p in t.taxi},
        {p.sleeper_id for p in t.ir},
    ]
    for i in range(len(groups)):
        for j in range(i + 1, len(groups)):
            if groups[i] & groups[j]:
                bad.append((t.roster_id, groups[i] & groups[j]))
    if any(p.sleeper_id == "0" for p in t.all_players):
        bad.append((t.roster_id, {"0 placeholder leaked"}))
print(f"  overlaps / placeholders: {len(bad)}", "PASS" if not bad else f"FAIL {bad}")

print("\n=== gotcha 3: every team has picks, including non-traders ===")
zero = [t.roster_id for t in league.teams if not t.picks]
print(f"  teams with zero picks: {zero or 'none'}", "PASS" if not zero else "FAIL")
counts = {len(t.picks) for t in league.teams}
print(f"  pick counts across league: {sorted(counts)}")
total = sum(len(t.picks) for t in league.teams)
expected = s.teams * s.draft_rounds * len({p.season for t in league.teams for p in t.picks})
print(f"  conservation: {total} owned vs {expected} issued", "PASS" if total == expected else "FAIL")

print("\n=== pick detail: a team that traded, and one that never did ===")
book = ValueBook()
traders = [t for t in league.teams if any(not p.is_own for p in t.picks)]
quiet = [t for t in league.teams if all(p.is_own for p in t.picks)]
for t in (traders[:1] + quiet[:1]):
    kind = "HAS TRADED" if any(not p.is_own for p in t.picks) else "NEVER TRADED"
    print(f"\n  roster {t.roster_id} {t.team_name} [{kind}] -- {len(t.picks)} picks")
    for p in sorted(t.picks, key=lambda p: (p.season, p.round, p.original_roster_id))[:8]:
        origin = "own" if p.is_own else f"from r{p.original_roster_id}"
        lbl = label_pick(p, book, s.teams)
        print(f"      {p.season} r{p.round} {origin:<10} -> {lbl:<20} {book.pick_value(lbl, s.superflex, s.teams):>8,.0f}")

print("\n=== gotcha 4: trade history across previous_league_id ===")
chain = client.league_chain(league.league_id)
print(f"  chain: {[c.get('season') for c in chain]}")
trades = client.load_trades(league.league_id)
print(f"  completed trades found: {len(trades)}")
for tr in trades[:5]:
    print(f"    {tr.season} wk{tr.week} rosters={tr.roster_ids} picks={len(tr.draft_picks)}")

print("\n=== format detection across formats (never assume) ===")
base = json.loads(FIXTURE.read_text())[f"league/{league.league_id}"] if source == "FIXTURE" else None
variants = {
    "1QB Full PPR 12": (["QB","RB","RB","WR","WR","WR","TE","FLEX","FLEX"], {"rec":1.0}, 12),
    "Superflex Half PPR 12": (["QB","RB","RB","WR","WR","TE","FLEX","SUPER_FLEX"], {"rec":0.5}, 12),
    "1QB Non-PPR 10": (["QB","RB","RB","WR","WR","TE","FLEX"], {"rec":0.0}, 10),
    "SF TEP 14": (["QB","QB","RB","RB","WR","WR","TE","FLEX"], {"rec":1.0,"bonus_rec_te":0.5}, 14),
}
for name, (positions, scoring, teams) in variants.items():
    cfg = {
        "total_rosters": teams,
        "roster_positions": positions + ["BN"]*10 + ["TAXI"]*3 + ["IR"]*2,
        "scoring_settings": scoring,
        "settings": {"taxi_slots": 3, "reserve_slots": 2},
    }
    print(f"  {name:<24} -> {detect_settings(cfg, 4).format_label}")
