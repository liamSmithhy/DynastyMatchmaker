"""Build the FMB league from the roster sheet the commissioner supplied.

Player names are resolved against db_playerids.csv -- the full ~12,500-row
crosswalk, not the ~640-row values file -- so a name that DynastyProcess spells
differently ("Chig Okonkwo" vs "Chigoziem Okonkwo") still lands on the right
sleeper_id and therefore the right value. Anything that fails to resolve is
reported rather than silently priced at zero.

Two things in the sheet are summaries rather than data, and are approximated
here. Both are printed at the end so they can be corrected:

  * Pick ownership is given per team in prose ("2027: 4x good 1st"). It is
    expanded into a conserving ledger below -- every pick has exactly one owner.
  * No win/loss records were supplied, so records are left at 0-0 and the
    projected finish drives the projected rookie draft order.

Run: python3 scripts/build_fmb_league.py --out tests/fixtures/fmb
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.values import CsvCache, IDS_FILE, normalize_name  # noqa: E402

LEAGUE_ID = "1126400000000000001"
SEASON = "2026"
TEAMS = 12
DRAFT_ROUNDS = 4
HORIZON = [2027, 2028, 2029]

ROSTER_POSITIONS = (
    ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]
    + ["BN"] * 7
    + ["TAXI"] * 7
)

# roster_id, team name, manager, projected finish, starters, bench, taxi
TEAMS_DATA = [
    (1, "Eastbay Muffin Eaters", "eastbay", 1,
     ["Justin Herbert", "Bijan Robinson", "Jahmyr Gibbs", "Puka Nacua",
      "CeeDee Lamb", "Brock Bowers", "Tee Higgins"],
     ["Baker Mayfield", "Ashton Jeanty", "Quinshon Judkins", "Rashee Rice",
      "Ladd McConkey", "Luther Burden III", "George Kittle"],
     ["De'Zhaun Stribling", "Kaelon Black", "Jordan James", "Mike Washington",
      "Dylan Sampson", "Ollie Gordon II", "Jaylen Wright"]),

    (2, "Finklestein Israelites", "finklestein", 4,
     ["Joe Burrow", "De'Von Achane", "Travis Etienne Jr.", "Amon-Ra St. Brown",
      "Davante Adams", "Sam LaPorta", "D'Andre Swift"],
     ["Jordan Love", "Jordan Mason", "Matthew Golden", "Wan'Dale Robinson",
      "Rashid Shaheed", "Josh Downs", "Mark Andrews"],
     ["Oronde Gadsden II", "Kyle Monangai", "Omar Cooper Jr.", "Demond Claiborne",
      "Brenton Strange", "Dontayvion Wicks", "Tank Dell"]),

    (3, "Team Poop", "poop", 2,
     ["Drake Maye", "Jeremiyah Love", "TreVeyon Henderson", "Drake London",
      "A.J. Brown", "Colston Loveland", "Garrett Wilson"],
     ["Tony Pollard", "Tyler Allgeier", "Marvin Harrison Jr.", "Chris Godwin",
      "Romeo Doubs", "Jake Ferguson"],
     ["Jalen McMillan", "Pat Bryant", "Cyrus Allen", "Bryce Lance",
      "Eli Raridon", "Tank Bigsby", "Tyjae Spears"]),

    (4, "Rollin' Roc University", "rollinroc", 5,
     ["Caleb Williams", "Kenneth Walker III", "Cam Skattebo", "Chris Olave",
      "Tetairoa McMillan", "Kyle Pitts", "Emeka Egbuka"],
     ["Bo Nix", "Tyrone Tracy Jr.", "Jaylen Warren", "Brian Thomas Jr.",
      "Jalen Coker", "AJ Barner", "Cade Otton", "Zach Charbonnet"],
     ["Emari Demercado", "Jonathon Brooks", "Kaleb Johnson", "Ty Simpson",
      "Max Klare", "Ted Hurst", "Bryce Young"]),

    (5, "3rdlegGreg02", "3rdleggreg02", 12,
     ["C.J. Stroud", "Rico Dowdle", "Breece Hall", "Carnell Tate",
      "DK Metcalf", "Dalton Schultz", "Jacory Croskey-Merritt"],
     ["Cam Ward", "Matthew Stafford", "Kyler Murray", "Jayden Higgins",
      "Jordyn Tyson", "Darnell Mooney", "T.J. Hockenson"],
     ["Tre Tucker", "Braelon Allen", "Keon Coleman", "Isaiah Bond",
      "Isaac TeSlaa", "Malachi Fields", "Denzel Boston"]),

    (6, "Colwell's Corner", "colwell", 11,
     ["Patrick Mahomes", "Bucky Irving", "Aaron Jones", "Zay Flowers",
      "Alec Pierce", "Travis Kelce", "Xavier Worthy"],
     ["Brock Purdy", "RJ Harvey", "J.K. Dobbins", "Ricky Pearsall",
      "Jakobi Meyers", "Jerry Jeudy", "Harold Fannin Jr."],
     ["Tre Harris", "Travis Hunter", "Fernando Mendoza", "Ja'Kobi Lane",
      "Jonah Coleman", "Eli Stowers", "DeMario Douglas"]),

    (7, "Nutz N Boltz", "nutznboltz", 8,
     ["Dak Prescott", "Josh Jacobs", "Jaydon Blue", "George Pickens",
      "Malik Nabers", "Tyler Warren", "Rome Odunze"],
     ["Trevor Lawrence", "Michael Wilson", "Ray Davis", "Rachaad White",
      "Deebo Samuel", "Brandon Aiyuk", "David Njoku"],
     ["Theo Johnson", "Shedeur Sanders", "Chimere Dike", "Emmett Johnson",
      "Kevin Coleman Jr.", "Kendre Miller"]),

    (8, "Gallons of Grease", "gallons", 3,
     ["Jaxson Dart", "Christian McCaffrey", "Omarion Hampton",
      "Jaxon Smith-Njigba", "Nico Collins", "Tucker Kraft", "David Montgomery"],
     ["Malik Willis", "Brian Robinson Jr.", "Tory Horton", "Michael Pittman Jr.",
      "Jameson Williams", "Jalen Nailor", "Gunnar Helm"],
     ["Chris Brooks", "Malik Washington", "Troy Franklin", "Terrance Ferguson",
      "Efton Chism III", "Chris Bell", "Zachariah Branch"]),

    (9, "Victoria Cake Crew", "victoria", 6,
     ["Jalen Hurts", "Kyren Williams", "Chase Brown", "Justin Jefferson",
      "DeVonta Smith", "Kenyon Sadiq", "Bhayshul Tuten"],
     ["Sam Darnold", "Chris Rodriguez Jr.", "Blake Corum", "Khalil Shakir",
      "Jordan Addison", "Isaiah Likely"],
     ["Elic Ayomanor", "Tahj Brooks", "KC Concepcion", "Skyler Bell",
      "Kaytron Allen"]),

    (10, "cgasser", "cgasser", 10,
     ["Josh Allen", "Saquon Barkley", "Rhamondre Stevenson", "Jaylen Waddle",
      "Christian Watson", "Trey McBride", "Chuba Hubbard"],
     ["Jared Goff", "Jayden Reed", "Germie Bernard", "Makai Lemon",
      "Stefon Diggs", "Christian Kirk", "Courtland Sutton", "Michael Trigg"],
     []),

    (11, "Dab Kit A&M", "dabkit", 9,
     ["Lamar Jackson", "Derrick Henry", "Jonathan Taylor", "Parker Washington",
      "DJ Moore", "Hunter Henry", "Mike Evans"],
     ["James Conner", "Kenneth Gainwell", "Ryan Flournoy", "Tyreek Hill",
      "Jauan Jennings", "Quentin Johnston", "Dalton Kincaid", "Chris Brazzell II"],
     ["Jalen Milroe", "Elijah Arroyo", "Dont'e Thornton Jr.", "Tyler Shough",
      "Elijah Sarratt", "Nicholas Singleton", "Eli Heidenreich"]),

    (12, "Redskins Rock", "redskins", 7,
     ["Jayden Daniels", "James Cook", "Javonte Williams", "Ja'Marr Chase",
      "Terry McLaurin", "Dallas Goedert", "Chigoziem Okonkwo"],
     ["Daniel Jones", "Caleb Douglas", "Odell Beckham Jr.", "Calvin Ridley",
      "Darius Slayton", "Kayshon Boutte", "Darren Waller"],
     ["MarShawn Lloyd", "Antonio Williams", "Dean Connors", "Zavion Thomas",
      "Robert Henry", "Barion Brown", "Camden Brown"]),
]

# The sheet's pick lines expanded into a ledger where every pick has exactly one
# owner. (season, round, original_roster_id) -> new owner.
#
# Sellers are the teams the sheet says are MISSING picks; buyers are the teams
# it says hold extras. The sheet implies seven extra 2027 firsts but names only
# five teams without one, so that shortfall is spread across the three buyers
# rather than invented.
PICK_TRADES = [
    # 2027 firsts: sold by 1, 8, 9, 11, 12
    (2027, 1, 1, 2), (2027, 1, 11, 2),      # -> Finklestein (3 total)
    (2027, 1, 12, 4), (2027, 1, 9, 4),      # -> Rollin' Roc (3 total)
    (2027, 1, 8, 5),                        # -> 3rdlegGreg02 (2 total)
    # 2027 seconds: sold by 1, 8, 9 -> Colwell's (4 total)
    (2027, 2, 1, 6), (2027, 2, 8, 6), (2027, 2, 9, 6),
    # 2028 firsts: sold by 8, 12
    (2028, 1, 8, 2),                        # -> Finklestein (2 total)
    (2028, 1, 12, 6),                       # -> Colwell's (2 total)
    # Gallons holds thirds and fourths only, so its remaining 1sts/2nds move
    (2028, 2, 8, 1), (2029, 1, 8, 1),       # -> Eastbay (accumulating)
    (2029, 2, 8, 11),                       # -> Dab Kit
]


def load_crosswalk() -> tuple[dict[str, dict], dict[str, dict]]:
    rows = list(csv.DictReader(CsvCache().read(IDS_FILE).splitlines()))
    by_name: dict[str, dict] = {}
    by_id: dict[str, dict] = {}
    for row in rows:
        sleeper_id = (row.get("sleeper_id") or "").strip()
        if not sleeper_id or sleeper_id in ("NA", "0"):
            continue
        key = normalize_name(row.get("name") or "")
        if not key:
            continue
        by_id[sleeper_id] = row
        prev = by_name.get(key)
        # Prefer the more recent season when two players share a folded name.
        if prev is None or (row.get("db_season") or "") > (prev.get("db_season") or ""):
            by_name[key] = row
    return by_name, by_id


def build(out_dir: Path) -> None:
    by_name, _ = load_crosswalk()
    unresolved: list[tuple[str, str]] = []
    seen: dict[str, tuple[str, str]] = {}
    duplicates: list[str] = []
    directory: dict[str, dict] = {}

    def resolve(name: str, team: str) -> str | None:
        clean = name.replace("(INJ)", "").strip()
        row = by_name.get(normalize_name(clean))
        if row is None:
            unresolved.append((clean, team))
            return None
        sid = row["sleeper_id"].strip()
        if sid in seen:
            duplicates.append(f"{clean}: {seen[sid][1]} and {team}")
            return None
        seen[sid] = (clean, team)
        age = row.get("age") or ""
        directory[sid] = {
            "player_id": sid,
            "full_name": row.get("name") or clean,
            "first_name": (row.get("name") or clean).split(" ")[0],
            "last_name": " ".join((row.get("name") or clean).split(" ")[1:]),
            "position": (row.get("position") or "").upper(),
            "fantasy_positions": [(row.get("position") or "").upper()],
            "team": (row.get("team") or "").upper(),
            "age": float(age) if age.replace(".", "", 1).isdigit() else None,
            "status": "Active",
        }
        return sid

    rosters = []
    for rid, name, manager, finish, starters, bench, taxi in TEAMS_DATA:
        s_ids = [resolve(p, name) for p in starters]
        # Lineup order must match roster_positions; K and DEF go unfilled
        # because the sheet lists no kickers or defenses.
        lineup = [i for i in s_ids if i] + ["0", "0"]
        b_ids = [i for i in (resolve(p, name) for p in bench) if i]
        t_ids = [i for i in (resolve(p, name) for p in taxi) if i]
        rosters.append({
            "roster_id": rid,
            "owner_id": f"user_{rid:03d}",
            "league_id": LEAGUE_ID,
            "players": [i for i in (*[x for x in s_ids if x], *b_ids, *t_ids)],
            "starters": lineup,
            "taxi": t_ids,
            "reserve": [],
            "settings": {
                "wins": 0, "losses": 0, "ties": 0,
                # No records supplied; projected finish drives draft order.
                "fpts": 2000 - finish * 50, "fpts_decimal": 0,
            },
        })

    users = [
        {"user_id": f"user_{rid:03d}", "username": manager, "display_name": manager,
         "metadata": {"team_name": name}}
        for rid, name, manager, *_ in TEAMS_DATA
    ]

    traded = [
        {"season": str(season), "round": rnd, "roster_id": origin,
         "previous_owner_id": origin, "owner_id": owner}
        for season, rnd, origin, owner in PICK_TRADES
    ]

    league = {
        "league_id": LEAGUE_ID, "previous_league_id": None,
        "name": "FMB League", "season": SEASON, "season_type": "regular",
        "sport": "nfl", "status": "in_season", "total_rosters": TEAMS,
        "draft_id": LEAGUE_ID + "d", "roster_positions": ROSTER_POSITIONS,
        "settings": {"num_teams": TEAMS, "taxi_slots": 7, "reserve_slots": 0,
                     "playoff_week_start": 15, "type": 2, "best_ball": 0},
        # 1QB, full PPR, no TE premium.
        "scoring_settings": {"rec": 1.0, "pass_td": 4.0, "rush_td": 6.0,
                             "rec_td": 6.0, "pass_yd": 0.04, "rush_yd": 0.1,
                             "rec_yd": 0.1, "fum_lost": -2.0},
    }
    drafts = [{"draft_id": LEAGUE_ID + "d", "league_id": LEAGUE_ID,
               "season": SEASON, "status": "complete", "type": "linear",
               "settings": {"rounds": DRAFT_ROUNDS, "teams": TEAMS}}]

    payloads = {
        "state/nfl": {"week": 2, "season": SEASON, "season_type": "regular",
                      "league_season": SEASON},
        f"league/{LEAGUE_ID}": league,
        f"league/{LEAGUE_ID}/rosters": rosters,
        f"league/{LEAGUE_ID}/users": users,
        f"league/{LEAGUE_ID}/traded_picks": traded,
        f"league/{LEAGUE_ID}/drafts": drafts,
        "players/nfl": directory,
    }
    for rid, name, manager, *_ in TEAMS_DATA:
        payloads[f"user/{manager}"] = {
            "user_id": f"user_{rid:03d}", "username": manager, "display_name": manager}
        payloads[f"user/user_{rid:03d}/leagues/nfl/{SEASON}"] = [league]

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "payloads.json").write_text(json.dumps(payloads), encoding="utf-8")

    from scripts.make_fixture import write_values_snapshot
    write_values_snapshot(out_dir / "values")

    total = sum(len(r["players"]) for r in rosters)
    print(f"\nwrote {out_dir}/payloads.json")
    print(f"  {TEAMS} teams, {total} players placed, {len(traded)} traded picks")
    if duplicates:
        print(f"\n  DUPLICATE PLAYERS (kept the first, dropped the second):")
        for d in duplicates:
            print(f"    {d}")
    if unresolved:
        print(f"\n  UNRESOLVED NAMES ({len(unresolved)}) -- no Sleeper id, priced at 0:")
        for n, t in unresolved:
            print(f"    {n:<26} ({t})")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="tests/fixtures/fmb")
    build(Path(ap.parse_args().out))
