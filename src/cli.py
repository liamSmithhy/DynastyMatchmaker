"""Command line interface.

    demo                   full end-to-end run on the bundled sample league
    leagues  <username>    every league the user is in this season
    load     <league_id>   the normalized league: format, rosters, picks
    doctor   <username>    scores every team and reports value coverage
    report   <username>    shareable HTML page for the whole league
    snapshot <username>    save a league to disk for offline replay
    trades   <league_id>   ranked trade proposals (the actual product)
    history  <league_id>   completed trades across the league's history

Global flags:
    --fixture PATH     read Sleeper payloads from a JSON file instead of the API
    --values-dir PATH  read values from local CSVs instead of DynastyProcess
    --offline          never fetch; use cached data only
    --season YEAR      which season's leagues to list

Free, non-commercial use only. See LICENSING.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .scoring import LeagueScore, TeamScore, score_league
from .sleeper import DictTransport, League, SleeperClient, SleeperError, label_pick
from .values import ValueBook

BAR = "-"

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_PAYLOADS = REPO_ROOT / "tests/fixtures/league/payloads.json"
DEMO_VALUES = REPO_ROOT / "tests/fixtures/league/values"
DEMO_LEAGUE_ID = "1048291736450000000"

CREDIT = (
    "Values: DynastyProcess (Tan Ho, Joe Sydlowski). "
    "League data: Sleeper.\nFree, non-commercial use only -- see LICENSING.md."
)


def _client(args: argparse.Namespace) -> SleeperClient:
    if getattr(args, "fixture", None):
        payloads = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
        return SleeperClient(transport=DictTransport(payloads))
    return SleeperClient(offline=args.offline)


def _book(args: argparse.Namespace) -> ValueBook:
    values_dir = getattr(args, "values_dir", None)
    if values_dir:
        return ValueBook(source_dir=Path(values_dir))
    book = ValueBook(offline=args.offline)
    for note in book.notes:
        print(f"[values] {note}", file=sys.stderr)
    return book


def _credit() -> None:
    print(f"\n{BAR * 74}\n{CREDIT}")


def _rule(width: int) -> str:
    return BAR * width


def _money(value: float) -> str:
    return f"{value:,.0f}"


# --------------------------------------------------------------------------
# commands
# --------------------------------------------------------------------------

def cmd_leagues(args: argparse.Namespace) -> int:
    client = _client(args)
    leagues = client.leagues(args.username, season=args.season)
    if not leagues:
        print(f"no leagues for {args.username}"
              f"{f' in {args.season}' if args.season else ''}")
        return 1
    print(f"{len(leagues)} league(s) for {args.username}\n")
    header = f"{'league_id':<22}{'season':<8}{'teams':>6}  name"
    print(header)
    print(_rule(len(header)))
    for lg in leagues:
        print(
            f"{lg.get('league_id',''):<22}{lg.get('season',''):<8}"
            f"{lg.get('total_rosters',0):>6}  {lg.get('name','')}"
        )
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    client = _client(args)
    book = _book(args)
    league = client.load_league(args.league_id)
    settings = league.settings

    print(f"\n{league.name}   [{league.league_id}]")
    print(f"season {league.season} / {league.status}")
    print(f"FORMAT: {league.format_label}")
    print(
        f"  starters {settings.starters_per_team} "
        f"{[s.code for s in settings.starter_slots]}"
    )
    print(
        f"  bench {settings.bench_slots}  taxi {settings.taxi_slots}  "
        f"ir {settings.ir_slots}  draft rounds {settings.draft_rounds}"
    )

    header = (
        f"\n{'#':>2}  {'team':<26}{'manager':<14}{'rec':>7}"
        f"{'ST':>4}{'BN':>4}{'TX':>4}{'IR':>4}{'picks':>7}"
    )
    print(header)
    print(_rule(len(header)))
    for team in sorted(league.teams, key=lambda t: t.roster_id):
        print(
            f"{team.roster_id:>2}  {team.team_name[:25]:<26}{team.manager[:13]:<14}"
            f"{team.record:>7}{len(team.starters):>4}{len(team.bench):>4}"
            f"{len(team.taxi):>4}{len(team.ir):>4}{len(team.picks):>7}"
        )

    if args.roster:
        team = league.team(args.roster)
        if not team:
            print(f"\nno roster {args.roster}")
            return 1
        print(f"\n=== roster {team.roster_id}: {team.team_name} ({team.manager}) ===")
        for group, label in (
            (team.starters, "STARTER"),
            (team.bench, "BENCH"),
            (team.taxi, "TAXI"),
            (team.ir, "IR"),
        ):
            for player in group:
                value = book.value_by_sleeper(player.sleeper_id, settings.superflex)
                age = f"{player.age:.0f}" if player.age else "  "
                print(
                    f"  {label:<8}{player.position:<4}{player.name[:24]:<26}"
                    f"{age:>4}{_money(value):>9}"
                )
        print("  picks:")
        for pick in sorted(team.picks, key=lambda p: (p.season, p.round)):
            lbl = label_pick(pick, book, settings.teams)
            origin = "own" if pick.is_own else f"from r{pick.original_roster_id}"
            print(
                f"    {lbl:<20}{origin:<12}"
                f"{_money(book.pick_value(lbl, settings.superflex, settings.teams)):>9}"
            )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    client = _client(args)
    book = _book(args)

    if args.league_id:
        league = client.load_league(args.league_id)
    else:
        leagues = client.leagues(args.username, season=args.season)
        if not leagues:
            print(f"no leagues for {args.username}")
            return 1
        league = client.load_league(leagues[0]["league_id"])

    scored = score_league(league, book)
    print_doctor(scored, verbose=args.verbose)
    return 0 if scored.coverage_skill > 0.90 else 1


def print_doctor(scored: LeagueScore, verbose: bool = False) -> None:
    league = scored.league
    settings = scored.settings

    print(f"\n{league.name}   [{league.league_id}]")
    print(f"FORMAT: {league.format_label}    season {league.season}\n")

    print("value coverage")
    print(
        f"  skill positions (QB/RB/WR/TE): {scored.coverage_skill:6.1%}"
        f"   {'PASS' if scored.coverage_skill > 0.90 else 'FAIL (<90%)'}"
    )
    print(f"  all rostered players:          {scored.coverage:6.1%}")
    if scored.unmatched:
        by_pos: dict[str, int] = {}
        for player in scored.unmatched:
            by_pos[player.position or "?"] = by_pos.get(player.position or "?", 0) + 1
        summary = "  ".join(f"{k}:{v}" for k, v in sorted(by_pos.items()))
        print(f"  unmatched: {len(scored.unmatched)}   ({summary})")
        if verbose:
            for player in scored.unmatched[:25]:
                print(f"      {player.position:<4}{player.name}")

    print("\npositional demand (dedicated slots + measured flex share)")
    for pos, need in scored.demand.items():
        print(
            f"  {pos:<4}{need:>5.2f} starters/team"
            f"   replacement level {_money(scored.replacement[pos]):>8}"
        )

    header = (
        f"\n{'#':>2}  {'team':<24}{'rec':>6}{'starters':>10}{'players':>10}"
        f"{'picks':>9}{'TOTAL':>10}{'ovr':>5}{'st':>4}{'gap':>5}"
        f"  {'window':<10}{'age':>5}{'cap%':>6}"
    )
    print(header)
    print(_rule(len(header)))
    for score in sorted(scored.teams, key=lambda s: s.overall_rank):
        print(
            f"{score.roster_id:>2}  {score.name[:23]:<24}{score.team.record:>6}"
            f"{_money(score.starter_value):>10}{_money(score.player_value):>10}"
            f"{_money(score.pick_value):>9}{_money(score.total_value):>10}"
            f"{score.overall_rank:>5}{score.starter_rank:>4}{score.rank_gap:>+5}"
            f"  {score.window:<10}{score.age:>5.1f}{score.capital_share:>6.0%}"
        )

    print("\nwindow summary")
    for score in sorted(scored.teams, key=lambda s: (s.window, s.overall_rank)):
        surplus = ", ".join(
            f"{p} +{_money(v)}" for p, v in sorted(score.surplus.items(), key=lambda kv: -kv[1])
        ) or "none"
        deficit = ", ".join(
            f"{p} -{_money(v)}" for p, v in sorted(score.deficit.items(), key=lambda kv: -kv[1])
        ) or "none"
        print(f"  {score.name[:22]:<24}{score.window:<11}{score.posture:<22}")
        print(f"      surplus: {surplus}")
        print(f"      needs:   {deficit}")

    if verbose:
        for score in sorted(scored.teams, key=lambda s: s.overall_rank):
            print(f"\n  --- {score.name} optimal lineup ---")
            for slot, player in score.lineup:
                if player:
                    print(
                        f"    {slot.code:<12}{player.position:<4}"
                        f"{player.name[:24]:<26}{_money(player.value):>9}"
                    )
                else:
                    print(f"    {slot.code:<12}(empty)")


def cmd_history(args: argparse.Namespace) -> int:
    client = _client(args)
    trades = client.load_trades(args.league_id)
    if not trades:
        print("no completed trades found")
        return 0
    print(f"{len(trades)} completed trade(s)\n")
    header = f"{'season':<8}{'wk':>4}  {'rosters':<14}{'players':>9}{'picks':>7}{'faab':>6}"
    print(header)
    print(_rule(len(header)))
    for trade in trades:
        rosters = ",".join(str(r) for r in trade.roster_ids)
        print(
            f"{trade.season:<8}{trade.week:>4}  {rosters:<14}"
            f"{len(trade.adds):>9}{len(trade.draft_picks):>7}"
            f"{len(trade.waiver_budget):>6}"
        )
    return 0


def _resolve_league(
    client: SleeperClient, target: str, season: int | None = None
) -> tuple[League, int | None]:
    """Accept either a league_id or a username; report which roster is theirs.

    Sleeper league ids are long numeric strings, which makes for a good guess at
    which kind of thing was passed -- but only a guess, so both are tried before
    giving up. Deciding on the shape of the string alone meant any id that was
    not all digits was looked up as a username and failed.
    """
    looks_like_id = target.isdigit() and len(target) >= 12
    attempts = ("league", "user") if looks_like_id else ("user", "league")
    errors: list[str] = []

    for kind in attempts:
        try:
            if kind == "league":
                return client.load_league(target), None
            user = client.user(target)
            leagues = client.leagues(target, season=season)
            if not leagues:
                raise SleeperError(f"{target} is in no leagues this season")
            league = client.load_league(leagues[0]["league_id"])
            focus = next(
                (t.roster_id for t in league.teams if t.owner_id == user.get("user_id")),
                None,
            )
            return league, focus
        except SleeperError as exc:
            errors.append(str(exc))

    raise SleeperError(
        f"{target!r} is neither a Sleeper username nor a league id ({'; '.join(errors)})"
    )


def cmd_report(args: argparse.Namespace) -> int:
    """Write the shareable HTML page for a league.

    This is the same renderer the demo uses, so a real league produces exactly
    the page the demo shows -- there is no separate demo codepath to drift.
    """
    from .report import build_report_data, write_report

    if args.sample:
        payloads = json.loads(DEMO_PAYLOADS.read_text(encoding="utf-8"))
        client = SleeperClient(transport=DictTransport(payloads))
        book = ValueBook(source_dir=DEMO_VALUES)
        league = client.load_league(DEMO_LEAGUE_ID)
        focus, source = None, "sample"
    else:
        if not args.target:
            print("give a sleeper username or league_id, or pass --sample", file=sys.stderr)
            return 2
        client = _client(args)
        book = _book(args)
        league, focus = _resolve_league(client, args.target, args.season)
        source = "live"

    if args.roster is not None:
        focus = args.roster

    scored = score_league(league, book)
    data = build_report_data(
        scored, book, focus_roster=focus, source=source, limit=args.limit,
        title=args.title,
    )
    out = write_report(data, args.out)

    print(f"wrote {out}  ({out.stat().st_size / 1024:.0f} KB)")
    print(f"  {league.name} -- {league.format_label}")
    print(
        f"  {len(scored.teams)} teams, {len(data['proposals'])} proposals, "
        f"coverage {scored.coverage_skill:.1%}"
    )
    if focus is not None:
        team = league.team(focus)
        print(f"  your team: {team.team_name}" if team else f"  roster {focus}")
    print("  open it in a browser, or share the file -- it is self-contained")
    return 0


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Capture a league to disk so it can be replayed offline.

    Writes the Sleeper payloads and the values they were scored against, which
    is everything needed to reproduce a run byte for byte -- useful for sharing
    a league with someone who cannot reach the API.
    """
    import sys as _sys

    _sys.path.insert(0, str(REPO_ROOT))
    from scripts.make_fixture import write_values_snapshot

    client = _client(args)
    league, focus = _resolve_league(client, args.target, args.season)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    lid = league.league_id
    payloads: dict[str, object] = {}
    for path in (
        "state/nfl",
        f"league/{lid}",
        f"league/{lid}/rosters",
        f"league/{lid}/users",
        f"league/{lid}/traded_picks",
        f"league/{lid}/drafts",
        "players/nfl",
    ):
        try:
            payloads[path] = client.http.get(path)
        except SleeperError as exc:
            print(f"  skipped {path}: {exc}", file=sys.stderr)

    if not args.target.isdigit():
        user = client.user(args.target)
        payloads[f"user/{args.target}"] = user
        key = f"user/{user['user_id']}/leagues/nfl/{league.season}"
        payloads[key] = [payloads[f"league/{lid}"]]

    (out / "payloads.json").write_text(json.dumps(payloads), encoding="utf-8")
    write_values_snapshot(out / "values")

    print(f"wrote {out}/payloads.json and {out}/values/")
    print(f"  {league.name} -- {league.format_label}, your roster {focus}")
    print("  replay it offline with:")
    print(
        f"    python3 -m src.cli --fixture {out}/payloads.json "
        f"--values-dir {out}/values doctor {args.target}"
    )
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    """The whole product, end to end, with no account and no network.

    Runs on a sample league bundled with the repo: real players, real values,
    invented managers. Everything the tool would do against a live Sleeper
    league it does here, on data you can inspect in tests/fixtures/.
    """
    from .matchmaker import find_trades, render_proposal

    if not DEMO_PAYLOADS.exists():
        print(f"sample league missing at {DEMO_PAYLOADS}", file=sys.stderr)
        return 2

    payloads = json.loads(DEMO_PAYLOADS.read_text(encoding="utf-8"))
    client = SleeperClient(transport=DictTransport(payloads))
    book = ValueBook(source_dir=DEMO_VALUES)

    print("=" * 74)
    print("  DYNASTY TRADE MATCHMAKER -- DEMO")
    print("=" * 74)
    print("  Sample league. Real players and real DynastyProcess values;")
    print("  the managers and rosters are invented. No network, no account.")

    league = client.load_league(DEMO_LEAGUE_ID)
    scored = score_league(league, book)

    print(f"\n  Detected format: {league.format_label}")
    print(f"  Nothing below is configured -- team count, PPR level, starting")
    print(f"  slots and superflex are all read off the league itself.")

    print_doctor(scored, verbose=False)

    proposals = find_trades(scored, book, limit=args.limit, multi_team=True)
    print("\n" + "=" * 74)
    print(f"  {len(proposals)} TRADES THAT SHOULD HAPPEN, RANKED")
    print("=" * 74)
    for i, proposal in enumerate(proposals, start=1):
        print(render_proposal(proposal, i))

    print("=" * 74)
    print("  Run it on a real league:")
    print("    python3 -m src.cli doctor <your-sleeper-username>")
    print("    python3 -m src.cli trades <league_id>")
    print("    python3 -m src.cli report <your-sleeper-username>   # shareable page")
    _credit()

    if args.html:
        from .report import build_report_data, write_report

        data = build_report_data(scored, book, proposals=proposals, source="sample")
        print(f"wrote {write_report(data, args.html)}")
    return 0


def cmd_trades(args: argparse.Namespace) -> int:
    from .matchmaker import find_trades, render_proposal

    client = _client(args)
    book = _book(args)
    league = client.load_league(args.league_id)
    scored = score_league(league, book)

    proposals = find_trades(
        scored,
        book,
        roster_id=args.roster,
        limit=args.limit,
        multi_team=args.multi_team,
    )
    if not proposals:
        print("no trades cleared both sides. Nobody's surplus lines up with "
              "anybody's hole right now.")
        return 0

    focus = f" for roster {args.roster}" if args.roster else ""
    print(f"\n{league.name} -- {league.format_label}")
    print(f"{len(proposals)} proposal(s){focus}, ranked\n")
    for i, proposal in enumerate(proposals, start=1):
        print(render_proposal(proposal, i))
    return 0


# --------------------------------------------------------------------------
# entry
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="matchmaker", description="Dynasty fantasy football trade matchmaker"
    )
    parser.add_argument("--fixture", help="read Sleeper payloads from a JSON file")
    parser.add_argument(
        "--values-dir", dest="values_dir", help="read values from local CSVs"
    )
    parser.add_argument("--offline", action="store_true", help="use cached data only")
    parser.add_argument("--season", type=int, help="season for league lookup")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("demo", help="end-to-end run on the bundled sample league")
    p.add_argument("--limit", type=int, default=5)
    p.add_argument("--html", help="also write the shareable HTML page here")
    p.set_defaults(func=cmd_demo)

    p = sub.add_parser("report", help="write a shareable HTML page for a league")
    p.add_argument("target", nargs="?", help="sleeper username or league_id")
    p.add_argument("--sample", action="store_true", help="use the bundled sample league")
    p.add_argument("--out", default="matchmaker-report.html")
    p.add_argument("--roster", type=int, help="highlight this roster as yours")
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--title", help="page name (defaults to the tool name)")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("snapshot", help="save a league to disk for offline replay")
    p.add_argument("target", help="sleeper username or league_id")
    p.add_argument("--out", default="league-snapshot")
    p.set_defaults(func=cmd_snapshot)

    p = sub.add_parser("leagues", help="list a user's leagues")
    p.add_argument("username")
    p.set_defaults(func=cmd_leagues)

    p = sub.add_parser("load", help="show a normalized league")
    p.add_argument("league_id")
    p.add_argument("--roster", type=int, help="dump this roster in full")
    p.set_defaults(func=cmd_load)

    p = sub.add_parser("doctor", help="score every team and check coverage")
    p.add_argument("username")
    p.add_argument("--league-id", dest="league_id", help="skip lookup, score this league")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("trades", help="ranked trade proposals")
    p.add_argument("league_id")
    p.add_argument("--roster", type=int, help="only proposals involving this roster")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--multi-team", action="store_true", help="include 3-team deals")
    p.set_defaults(func=cmd_trades)

    p = sub.add_parser("history", help="completed trades across league history")
    p.add_argument("league_id")
    p.set_defaults(func=cmd_history)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except SleeperError as exc:
        print(f"sleeper: {exc}", file=sys.stderr)
        return 2
    except FileNotFoundError as exc:
        print(f"missing data: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
