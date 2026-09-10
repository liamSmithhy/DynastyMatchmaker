"""Build a Sleeper-shaped fixture league from real player data.

This exists because the sandbox this was built in cannot reach api.sleeper.app
(egress policy, 403 on CONNECT). The payloads below match the schemas at
https://docs.sleeper.com field for field, and the rosters are filled with real
players carrying real sleeper_ids, so every downstream stage is exercised
against real values rather than invented numbers.

Rosters are deliberately shaped -- one WR-rich team, one RB-rich team, a
contender, a rebuilder -- so the matchmaker has something real to find. The
shapes live here in the fixture, never in the algorithm.

Run:  python3 scripts/make_fixture.py [--out tests/fixtures/league]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.values import ValueBook  # noqa: E402

LEAGUE_ID = "1048291736450000000"
PREV_LEAGUE_ID = "0938271645390000000"
DRAFT_ID = "1048291736450000001"
SEASON = "2026"
TEAMS = 12

ROSTER_POSITIONS = (
    ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "FLEX"]
    + ["BN"] * 15
    + ["TAXI"] * 4
    + ["IR"] * 2
)

MANAGERS = [
    ("liamsmithh", "Gridiron Gamblers"),
    ("dfrancis", "Tape Don't Lie"),
    ("mkowalski", "Regression Candidates"),
    ("tobrien", "Zero RB Zealots"),
    ("apatel", "The Taxi Squad"),
    ("jnguyen", "Process Trusters"),
    ("rcastillo", "Expected Points"),
    ("swhitfield", "Snake Draft Sickos"),
    ("bthompson", "Corner Blitz"),
    ("kmurray", "Air Raid Enjoyers"),
    ("ehernandez", "Contract Year Guys"),
    ("pvoss", "Rebuild Forever"),
]

# roster_id -> how this team is built. Weights bias the draft pools.
ARCHETYPES = {
    1: "balanced",
    2: "wr_rich",       # surplus WR, hole at RB
    3: "rb_rich",       # surplus RB, hole at WR
    4: "contender",     # strong starters, picks spent
    5: "rebuild",       # young + hoarding picks
    6: "balanced",
    7: "te_rich",
    8: "top_heavy",     # elite top, nothing behind it
    9: "balanced",
    10: "qb_rich",
    11: "rebuild",
    12: "balanced",
}


def build(out_dir: Path, seed: int = 7) -> None:
    rng = random.Random(seed)
    book = ValueBook()

    # Real players, ranked, with sleeper ids -- pooled by position.
    pools: dict[str, list] = {"QB": [], "RB": [], "WR": [], "TE": []}
    for player in sorted(book.players, key=lambda p: -p.value_1qb):
        if player.sleeper_id and player.position in pools:
            pools[player.position].append(player)

    # Roster targets: 2 QB, 6 RB, 8 WR, 3 TE = 19, leaving room on a 28-man
    # roster for taxi and IR.
    base_targets = {"QB": 2, "RB": 6, "WR": 8, "TE": 3}
    tilts = {
        "wr_rich": {"WR": +4, "RB": -3},
        "rb_rich": {"RB": +4, "WR": -3},
        "te_rich": {"TE": +3, "WR": -2},
        "qb_rich": {"QB": +3, "RB": -2},
        "contender": {},
        "rebuild": {},
        "top_heavy": {},
        "balanced": {},
    }

    # Draft order per position: strong archetypes pick earlier from their pool.
    priority = {
        "contender": 0,
        "top_heavy": 0,
        "wr_rich": 1,
        "rb_rich": 1,
        "te_rich": 1,
        "qb_rich": 1,
        "balanced": 2,
        "rebuild": 3,
    }

    rosters_players: dict[int, list[str]] = {rid: [] for rid in ARCHETYPES}
    cursors = {pos: 0 for pos in pools}

    for pos in ("QB", "RB", "WR", "TE"):
        order = sorted(
            ARCHETYPES,
            key=lambda rid: (priority[ARCHETYPES[rid]], rng.random()),
        )
        wanted = {
            rid: max(0, base_targets[pos] + tilts[ARCHETYPES[rid]].get(pos, 0))
            for rid in ARCHETYPES
        }
        # Snake through the wanted counts so the good pools drain top-down.
        for round_idx in range(max(wanted.values())):
            sweep = order if round_idx % 2 == 0 else list(reversed(order))
            for rid in sweep:
                if round_idx >= wanted[rid]:
                    continue
                if cursors[pos] >= len(pools[pos]):
                    continue
                # Rebuilders reach for youth over the board.
                pick_from = cursors[pos]
                if ARCHETYPES[rid] == "rebuild":
                    window = pools[pos][cursors[pos]: cursors[pos] + 12]
                    young = [
                        i
                        for i, p in enumerate(window)
                        if p.age is not None and p.age <= 25
                    ]
                    if young:
                        pick_from = cursors[pos] + young[0]
                player = pools[pos].pop(pick_from)
                rosters_players[rid].append(player.sleeper_id)

    # ---- rosters -------------------------------------------------------
    rosters = []
    for rid in sorted(ARCHETYPES):
        ids = rosters_players[rid]
        by_pos: dict[str, list[str]] = {}
        for pid in ids:
            player = book.get_by_sleeper(pid)
            by_pos.setdefault(player.position, []).append(pid)
        for pos in by_pos:
            by_pos[pos].sort(key=lambda p: -book.get_by_sleeper(p).value_1qb)

        def take(pos: str, n: int) -> list[str]:
            got = by_pos.get(pos, [])[:n]
            by_pos[pos] = by_pos.get(pos, [])[n:]
            return got

        # Fill the lineup in roster_positions order, "0" where a team is short
        # at a position -- exactly how Sleeper reports an unfilled slot.
        starters = (
            take("QB", 1) + take("RB", 2) + take("WR", 3) + take("TE", 1)
        )
        flex_pool = sorted(
            by_pos.get("RB", []) + by_pos.get("WR", []) + by_pos.get("TE", []),
            key=lambda p: -book.get_by_sleeper(p).value_1qb,
        )[:2]
        starters += flex_pool
        for pid in flex_pool:
            for pos in ("RB", "WR", "TE"):
                if pid in by_pos.get(pos, []):
                    by_pos[pos].remove(pid)
        while len(starters) < 9:
            starters.append("0")  # placeholder, gotcha 5

        remaining = [p for pos in by_pos for p in by_pos[pos]]
        rng.shuffle(remaining)
        taxi = [
            p for p in remaining
            if (book.get_by_sleeper(p).age or 30) <= 23
        ][:2]
        rest = [p for p in remaining if p not in taxi]
        ir = rest[:1]
        bench = rest[1:]

        wins = {1: 6, 2: 5, 3: 5, 4: 9, 5: 2, 6: 6, 7: 4,
                8: 8, 9: 5, 10: 4, 11: 2, 12: 3}[rid]

        rosters.append(
            {
                "roster_id": rid,
                "owner_id": f"user_{rid:03d}",
                "league_id": LEAGUE_ID,
                # players is the full roster; the others are views into it.
                "players": [p for p in ids],
                "starters": starters,
                "taxi": taxi,
                "reserve": ir,
                "settings": {
                    "wins": wins,
                    "losses": 13 - wins,
                    "ties": 0,
                    "fpts": 1200 + rid * 17 + wins * 31,
                    "fpts_decimal": 44,
                    "waiver_budget_used": 12,
                    "total_moves": 4,
                },
            }
        )

    users = [
        {
            "user_id": f"user_{rid:03d}",
            "username": MANAGERS[rid - 1][0],
            "display_name": MANAGERS[rid - 1][0],
            "avatar": None,
            "metadata": {"team_name": MANAGERS[rid - 1][1]},
        }
        for rid in sorted(ARCHETYPES)
    ]

    league = {
        "league_id": LEAGUE_ID,
        "previous_league_id": PREV_LEAGUE_ID,
        "name": "Dynasty Warfare",
        "season": SEASON,
        "season_type": "regular",
        "sport": "nfl",
        "status": "in_season",
        "total_rosters": TEAMS,
        "draft_id": DRAFT_ID,
        "roster_positions": ROSTER_POSITIONS,
        "settings": {
            "num_teams": TEAMS,
            "taxi_slots": 4,
            "reserve_slots": 2,
            "playoff_week_start": 15,
            "type": 2,  # dynasty
            "best_ball": 0,
        },
        "scoring_settings": {
            "rec": 1.0,
            "pass_td": 4.0,
            "rush_td": 6.0,
            "rec_td": 6.0,
            "pass_yd": 0.04,
            "rush_yd": 0.1,
            "rec_yd": 0.1,
            "fum_lost": -2.0,
        },
    }

    drafts = [
        {
            "draft_id": DRAFT_ID,
            "league_id": LEAGUE_ID,
            "season": SEASON,
            "status": "complete",
            "type": "linear",
            "settings": {"rounds": 4, "teams": TEAMS},
            "slot_to_roster_id": {str(i): i for i in range(1, TEAMS + 1)},
        }
    ]

    # Picks that MOVED. Everything else must still be seeded (gotcha 3):
    # rosters 6, 7, 9 and 12 never appear here and must still show full capital.
    traded_picks = [
        {"season": "2027", "round": 1, "roster_id": 5, "previous_owner_id": 5, "owner_id": 4},
        {"season": "2027", "round": 1, "roster_id": 11, "previous_owner_id": 11, "owner_id": 8},
        {"season": "2027", "round": 2, "roster_id": 4, "previous_owner_id": 4, "owner_id": 5},
        {"season": "2027", "round": 2, "roster_id": 8, "previous_owner_id": 8, "owner_id": 11},
        {"season": "2028", "round": 1, "roster_id": 4, "previous_owner_id": 4, "owner_id": 5},
        {"season": "2028", "round": 1, "roster_id": 8, "previous_owner_id": 8, "owner_id": 11},
        {"season": "2028", "round": 2, "roster_id": 2, "previous_owner_id": 2, "owner_id": 3},
        {"season": "2028", "round": 3, "roster_id": 10, "previous_owner_id": 10, "owner_id": 1},
        {"season": "2029", "round": 1, "roster_id": 4, "previous_owner_id": 4, "owner_id": 5},
    ]

    prev_league = dict(league)
    prev_league.update(
        {
            "league_id": PREV_LEAGUE_ID,
            "previous_league_id": None,
            "season": "2025",
            "status": "complete",
        }
    )

    transactions = {
        (LEAGUE_ID, 3): [
            {
                "transaction_id": "txn_0001",
                "type": "trade",
                "status": "complete",
                "created": 1_756_000_000_000,
                "roster_ids": [4, 5],
                "adds": {},
                "drops": {},
                "draft_picks": [
                    {
                        "season": "2027",
                        "round": 1,
                        "roster_id": 5,
                        "previous_owner_id": 5,
                        "owner_id": 4,
                    }
                ],
                "waiver_budget": [],
            },
            {
                "transaction_id": "txn_0002",
                "type": "waiver",
                "status": "complete",
                "created": 1_756_100_000_000,
                "roster_ids": [7],
                "adds": {},
                "drops": {},
                "draft_picks": [],
                "waiver_budget": [],
            },
        ],
        (PREV_LEAGUE_ID, 9): [
            {
                "transaction_id": "txn_9001",
                "type": "trade",
                "status": "complete",
                "created": 1_730_000_000_000,
                "roster_ids": [8, 11],
                "adds": {},
                "drops": {},
                "draft_picks": [
                    {
                        "season": "2028",
                        "round": 1,
                        "roster_id": 8,
                        "previous_owner_id": 8,
                        "owner_id": 11,
                    }
                ],
                "waiver_budget": [{"sender": 8, "receiver": 11, "amount": 15}],
            }
        ],
    }

    # players/nfl, shaped like the real dump (a subset of its fields).
    directory = {}
    for player in book.players:
        if not player.sleeper_id:
            continue
        first, _, last = player.name.partition(" ")
        directory[player.sleeper_id] = {
            "player_id": player.sleeper_id,
            "first_name": first,
            "last_name": last,
            "full_name": player.name,
            "position": player.position,
            "fantasy_positions": [player.position],
            "team": player.team,
            "age": player.age,
            "status": "Active",
        }

    payloads: dict[str, object] = {
        "state/nfl": {
            "week": 2,
            "season": SEASON,
            "season_type": "regular",
            "league_season": SEASON,
        },
        "user/liamsmithh": {
            "user_id": "user_001",
            "username": "liamsmithh",
            "display_name": "liamsmithh",
        },
        f"user/user_001/leagues/nfl/{SEASON}": [league],
        f"league/{LEAGUE_ID}": league,
        f"league/{LEAGUE_ID}/rosters": rosters,
        f"league/{LEAGUE_ID}/users": users,
        f"league/{LEAGUE_ID}/traded_picks": traded_picks,
        f"league/{LEAGUE_ID}/drafts": drafts,
        f"league/{PREV_LEAGUE_ID}": prev_league,
        f"league/{PREV_LEAGUE_ID}/rosters": rosters,
        f"league/{PREV_LEAGUE_ID}/users": users,
        f"league/{PREV_LEAGUE_ID}/traded_picks": [],
        f"league/{PREV_LEAGUE_ID}/drafts": drafts,
        "players/nfl": directory,
    }
    for (lid, week), txns in transactions.items():
        payloads[f"league/{lid}/transactions/{week}"] = txns

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "payloads.json").write_text(json.dumps(payloads, indent=1), encoding="utf-8")
    print(f"wrote {out_dir / 'payloads.json'}")
    print(f"  {len(payloads)} endpoints, {len(directory)} players, {TEAMS} rosters")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="tests/fixtures/league")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args()
    build(Path(args.out), seed=args.seed)


if __name__ == "__main__":
    main()
