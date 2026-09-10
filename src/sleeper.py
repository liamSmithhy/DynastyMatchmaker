"""Sleeper league adapter.

This is the ONLY module that speaks Sleeper. Everything downstream sees
``League`` / ``Team`` / ``Pick`` and never a raw Sleeper payload, so an ESPN or
Yahoo adapter slots in by producing the same objects.

Read-only, no token. Note the licensing position recorded in CLAUDE.md: the
public API is free for NON-COMMERCIAL use, which is an open blocker on the paid
tier and not something this module can resolve.

Endpoints used (https://docs.sleeper.com):
    /v1/user/{username}                      -> user_id
    /v1/user/{user_id}/leagues/nfl/{season}  -> league list
    /v1/league/{id}                          -> settings, scoring, roster_positions
    /v1/league/{id}/rosters                  -> players / starters / taxi / reserve
    /v1/league/{id}/users                    -> display names, metadata.team_name
    /v1/league/{id}/traded_picks             -> picks that MOVED (gotcha 3)
    /v1/league/{id}/drafts                   -> round count, draft slots, status
    /v1/league/{id}/transactions/{week}      -> per-week, no bulk endpoint (gotcha 4)
    /v1/players/nfl                          -> ~5MB, cached 24h
    /v1/state/nfl                            -> current season / week
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .values import ValueBook, tier_for_slot

API_BASE = "https://api.sleeper.app/v1"

PLAYERS_TTL = 24 * 60 * 60  # gotcha: ~5MB payload, once daily max
DEFAULT_TTL = 15 * 60

# Roster slots that do not hold a starting lineup player.
NON_STARTER_SLOTS = {"BN", "TAXI", "IR"}

# Slots that accept more than one position. SUPER_FLEX is the superflex tell:
# a league is superflex if any lineup slot can hold a second QB.
FLEX_ELIGIBILITY: dict[str, frozenset[str]] = {
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"RB", "WR"}),
    "WRRB_WRT": frozenset({"RB", "WR", "TE"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
    "IDP_FLEX": frozenset({"DL", "LB", "DB"}),
}

SUPERFLEX_SLOTS = {"SUPER_FLEX", "QB/RB/WR/TE", "OP"}

OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE")


# --------------------------------------------------------------------------
# transport
# --------------------------------------------------------------------------

class SleeperError(RuntimeError):
    pass


class HttpTransport:
    """Cached JSON GET with retry and backoff.

    Injectable so the offline test suite can hand the adapter fixtures without
    a network stack. ``offline=True`` serves whatever is cached and refuses to
    make a request.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        offline: bool = False,
        base_url: str = API_BASE,
        retries: int = 4,
    ) -> None:
        from .values import default_cache_dir

        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir() / "sleeper"
        self.offline = offline
        self.base_url = base_url.rstrip("/")
        self.retries = retries
        self.calls = 0

    def _cache_path(self, path: str) -> Path:
        safe = path.strip("/").replace("/", "__") or "root"
        return self.cache_dir / f"{safe}.json"

    def get(self, path: str, ttl: int = DEFAULT_TTL) -> Any:
        cache_path = self._cache_path(path)
        if cache_path.exists():
            age = time.time() - cache_path.stat().st_mtime
            if age < ttl or self.offline:
                try:
                    return json.loads(cache_path.read_text(encoding="utf-8"))
                except ValueError:
                    pass

        if self.offline:
            raise SleeperError(f"offline and no cache for {path}")

        payload = self._fetch(path)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(payload), encoding="utf-8")
        return payload

    def _fetch(self, path: str) -> Any:
        url = f"{self.base_url}/{path.lstrip('/')}"
        last: Exception | None = None
        for attempt in range(self.retries):
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "dynasty-matchmaker/0.1"}
                )
                with urllib.request.urlopen(req, timeout=45) as resp:
                    self.calls += 1
                    body = resp.read().decode("utf-8")
                return json.loads(body) if body.strip() else None
            except urllib.error.HTTPError as exc:
                if exc.code == 404:
                    return None
                last = exc
            except Exception as exc:
                last = exc
            if attempt < self.retries - 1:
                time.sleep(2 ** (attempt + 1))
        raise SleeperError(f"GET {url} failed after {self.retries} attempts: {last}")


class DictTransport:
    """Serves a fixed ``{path: payload}`` map. Used by the offline tests."""

    def __init__(self, payloads: dict[str, Any]) -> None:
        self.payloads = payloads
        self.calls = 0

    def get(self, path: str, ttl: int = DEFAULT_TTL) -> Any:
        self.calls += 1
        return self.payloads.get(path.strip("/"))


# --------------------------------------------------------------------------
# normalized objects
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class RosterSlot:
    code: str
    eligible: frozenset[str]


@dataclass
class LeagueSettings:
    """What the league actually is. Every field is detected, never assumed."""

    teams: int
    roster_positions: list[str]
    starter_slots: list[RosterSlot]
    bench_slots: int
    taxi_slots: int
    ir_slots: int
    superflex: bool
    qb_slots: float
    ppr: float
    te_premium: float
    scoring: dict[str, float]
    draft_rounds: int
    best_ball: bool = False

    @property
    def ppr_label(self) -> str:
        if self.ppr >= 1.0:
            return "Full PPR"
        if self.ppr >= 0.5:
            return "Half PPR"
        if self.ppr <= 0.0:
            return "Non-PPR"
        return f"{self.ppr:g} PPR"

    @property
    def qb_label(self) -> str:
        return "Superflex" if self.superflex else "1QB"

    @property
    def format_label(self) -> str:
        label = f"{self.teams}-team {self.qb_label} {self.ppr_label}"
        if self.te_premium > 0:
            label += f" TE+{self.te_premium:g}"
        if self.best_ball:
            label += " Best Ball"
        return label

    def starter_counts(self) -> dict[str, int]:
        """How many dedicated slots each position has (flex counted separately)."""
        counts: dict[str, int] = {}
        for slot in self.starter_slots:
            counts[slot.code] = counts.get(slot.code, 0) + 1
        return counts

    def flex_slots(self) -> list[RosterSlot]:
        return [s for s in self.starter_slots if len(s.eligible) > 1]

    @property
    def starters_per_team(self) -> int:
        return len(self.starter_slots)


@dataclass(frozen=True)
class RosterPlayer:
    sleeper_id: str
    name: str
    position: str
    team: str
    age: float | None
    slot: str  # STARTER | BENCH | TAXI | IR


@dataclass(frozen=True)
class Pick:
    """A rookie draft pick, owned by ``owner_roster_id``, originally
    ``original_roster_id``'s."""

    season: int
    round: int
    original_roster_id: int
    owner_roster_id: int
    slot: int | None = None
    slot_estimated: bool = True

    @property
    def is_own(self) -> bool:
        return self.original_roster_id == self.owner_roster_id


@dataclass
class Team:
    roster_id: int
    owner_id: str | None
    manager: str
    team_name: str
    starters: list[RosterPlayer] = field(default_factory=list)
    bench: list[RosterPlayer] = field(default_factory=list)
    taxi: list[RosterPlayer] = field(default_factory=list)
    ir: list[RosterPlayer] = field(default_factory=list)
    picks: list[Pick] = field(default_factory=list)
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0

    @property
    def all_players(self) -> list[RosterPlayer]:
        return [*self.starters, *self.bench, *self.taxi, *self.ir]

    @property
    def rostered(self) -> list[RosterPlayer]:
        """Everyone who counts toward roster construction (taxi included, IR not)."""
        return [*self.starters, *self.bench, *self.taxi]

    @property
    def record(self) -> str:
        base = f"{self.wins}-{self.losses}"
        return f"{base}-{self.ties}" if self.ties else base


@dataclass
class Trade:
    transaction_id: str
    season: str
    week: int
    created: int
    roster_ids: list[int]
    adds: dict[str, int]
    drops: dict[str, int]
    draft_picks: list[dict[str, Any]]
    waiver_budget: list[dict[str, Any]]


@dataclass
class League:
    league_id: str
    name: str
    season: str
    status: str
    settings: LeagueSettings
    teams: list[Team]
    previous_league_id: str | None = None

    def team(self, roster_id: int) -> Team | None:
        return next((t for t in self.teams if t.roster_id == roster_id), None)

    @property
    def format_label(self) -> str:
        return self.settings.format_label


# --------------------------------------------------------------------------
# settings detection
# --------------------------------------------------------------------------

def parse_roster_positions(roster_positions: Sequence[str]) -> tuple[list[RosterSlot], int, int, int]:
    """Split ``roster_positions`` into starting slots, bench, taxi and IR counts."""
    starters: list[RosterSlot] = []
    bench = taxi = ir = 0
    for raw in roster_positions or []:
        code = str(raw).strip().upper()
        if code == "BN":
            bench += 1
        elif code == "TAXI":
            taxi += 1
        elif code in ("IR", "RESERVE"):
            ir += 1
        else:
            eligible = FLEX_ELIGIBILITY.get(code, frozenset({code}))
            starters.append(RosterSlot(code=code, eligible=eligible))
    return starters, bench, taxi, ir


def detect_settings(league_json: dict[str, Any], draft_rounds: int = 0) -> LeagueSettings:
    """Read the league's actual format off its own payload."""
    settings = league_json.get("settings") or {}
    scoring = {
        k: float(v)
        for k, v in (league_json.get("scoring_settings") or {}).items()
        if isinstance(v, (int, float))
    }
    roster_positions = league_json.get("roster_positions") or []
    starter_slots, bench, taxi, ir = parse_roster_positions(roster_positions)

    # Sleeper reports taxi and IR capacity in settings; roster_positions only
    # lists them in some league configurations, so take the larger of the two.
    taxi = max(taxi, int(settings.get("taxi_slots") or 0))
    ir = max(ir, int(settings.get("reserve_slots") or 0))

    # How much QB a lineup demands: a dedicated QB slot is one, a flex that can
    # hold a QB is counted as half since it competes with RB/WR/TE.
    qb_slots = sum(
        1.0 if s.code == "QB" else 0.5
        for s in starter_slots
        if s.code == "QB" or s.code in SUPERFLEX_SLOTS or ("QB" in s.eligible and len(s.eligible) > 1)
    )
    # "Superflex" here means "price QBs off value_2qb", which is true of any
    # league starting more than one QB -- a straight 2QB league included, not
    # just leagues with a slot literally named SUPER_FLEX.
    superflex = qb_slots > 1.0 or any(s.code in SUPERFLEX_SLOTS for s in starter_slots)

    teams = int(
        league_json.get("total_rosters")
        or settings.get("num_teams")
        or 0
    )

    return LeagueSettings(
        teams=teams,
        roster_positions=[str(p).upper() for p in roster_positions],
        starter_slots=starter_slots,
        bench_slots=bench,
        taxi_slots=taxi,
        ir_slots=ir,
        superflex=superflex,
        qb_slots=qb_slots,
        ppr=float(scoring.get("rec", 0.0)),
        te_premium=float(scoring.get("bonus_rec_te", 0.0)),
        scoring=scoring,
        draft_rounds=draft_rounds,
        best_ball=bool(settings.get("best_ball")),
    )


# --------------------------------------------------------------------------
# roster splitting (gotcha 5)
# --------------------------------------------------------------------------

def _clean_ids(raw: Iterable[Any] | None) -> list[str]:
    """Sleeper pads roster arrays with "0" placeholders for empty slots."""
    out: list[str] = []
    for item in raw or []:
        value = str(item).strip()
        if value and value not in ("0", "None", "null"):
            out.append(value)
    return out


def split_roster(roster_json: dict[str, Any]) -> dict[str, list[str]]:
    """Separate a roster's overlapping arrays into disjoint groups.

    ``players`` is the full roster and ``starters``/``taxi``/``reserve`` are
    *views into it*, so bench is a set difference, not a slice (gotcha 5).
    Order within each group is preserved -- ``starters`` is positional and lines
    up with ``roster_positions``.
    """
    players = _clean_ids(roster_json.get("players"))
    starters = _clean_ids(roster_json.get("starters"))
    taxi = _clean_ids(roster_json.get("taxi"))
    ir = _clean_ids(roster_json.get("reserve"))

    # A player can appear in starters/taxi/reserve without appearing in players
    # on rare malformed rosters; union so nobody is silently dropped.
    known = set(players) | set(starters) | set(taxi) | set(ir)
    assigned = set(starters) | set(taxi) | set(ir)
    bench = [p for p in players if p not in assigned]
    bench += [p for p in sorted(known - set(players) - assigned)]

    return {"starters": starters, "bench": bench, "taxi": taxi, "ir": ir}


# --------------------------------------------------------------------------
# pick ownership (gotcha 3)
# --------------------------------------------------------------------------

def build_pick_ownership(
    roster_ids: Sequence[int],
    seasons: Sequence[int],
    rounds: int,
    traded_picks: Sequence[dict[str, Any]],
) -> dict[int, list[Pick]]:
    """Seed every roster with its own picks, then apply the trades.

    Sleeper's ``traded_picks`` only reports picks that MOVED. Reading it alone
    gives every team that has never traded a pick zero draft capital, which is
    the single most damaging way to mis-grade a dynasty roster (gotcha 3).
    """
    owner: dict[tuple[int, int, int], int] = {}
    for roster_id in roster_ids:
        for season in seasons:
            for rnd in range(1, rounds + 1):
                owner[(season, rnd, roster_id)] = roster_id

    for traded in traded_picks or []:
        try:
            season = int(traded["season"])
            rnd = int(traded["round"])
            original = int(traded["roster_id"])
            new_owner = int(traded["owner_id"])
        except (KeyError, TypeError, ValueError):
            continue
        key = (season, rnd, original)
        # Only apply trades inside the horizon we seeded; a pick for a season
        # or round we are not modelling is not ours to place.
        if key in owner:
            owner[key] = new_owner

    out: dict[int, list[Pick]] = {rid: [] for rid in roster_ids}
    for (season, rnd, original), holder in sorted(owner.items()):
        if holder in out:
            out[holder].append(
                Pick(
                    season=season,
                    round=rnd,
                    original_roster_id=original,
                    owner_roster_id=holder,
                )
            )
    return out


def estimate_draft_slots(teams: Sequence[Team], league_teams: int) -> dict[int, int]:
    """Project rookie draft order from current standings: worst team picks first.

    Only ever an estimate -- the season is not over -- which is why ``Pick``
    carries ``slot_estimated`` and why anything past the next draft is valued by
    tier or round rather than by slot.
    """
    ordered = sorted(teams, key=lambda t: (t.wins - t.losses, t.points_for))
    return {team.roster_id: i + 1 for i, team in enumerate(ordered)}


# --------------------------------------------------------------------------
# client
# --------------------------------------------------------------------------

class SleeperClient:
    """Turns a Sleeper username into normalized league objects."""

    def __init__(self, transport: Any | None = None, offline: bool = False,
                 cache_dir: Path | None = None) -> None:
        self.http = transport or HttpTransport(cache_dir=cache_dir, offline=offline)
        self._players: dict[str, dict[str, Any]] | None = None

    # -- primitives -------------------------------------------------------

    def state(self) -> dict[str, Any]:
        return self.http.get("state/nfl", ttl=60 * 60) or {}

    def current_season(self) -> int:
        state = self.state()
        raw = state.get("league_season") or state.get("season")
        try:
            return int(raw)
        except (TypeError, ValueError):
            return time.gmtime().tm_year

    def user(self, username: str) -> dict[str, Any]:
        payload = self.http.get(f"user/{username}", ttl=24 * 60 * 60)
        if not payload or not payload.get("user_id"):
            raise SleeperError(f"no Sleeper user named {username!r}")
        return payload

    def leagues(self, username: str, season: int | None = None) -> list[dict[str, Any]]:
        user = self.user(username)
        season = season or self.current_season()
        payload = self.http.get(f"user/{user['user_id']}/leagues/nfl/{season}") or []
        return [lg for lg in payload if lg]

    def player_directory(self) -> dict[str, dict[str, Any]]:
        """The ~5MB player dump, cached hard (gotcha: once daily max)."""
        if self._players is None:
            self._players = self.http.get("players/nfl", ttl=PLAYERS_TTL) or {}
        return self._players

    # -- league assembly --------------------------------------------------

    def load_league(self, league_id: str, horizon_years: int = 3) -> League:
        league_json = self.http.get(f"league/{league_id}")
        if not league_json:
            raise SleeperError(f"no league {league_id!r}")

        drafts = self.http.get(f"league/{league_id}/drafts") or []
        rounds, draft_season, draft_complete = self._draft_shape(drafts, league_json)

        settings = detect_settings(league_json, draft_rounds=rounds)
        rosters = self.http.get(f"league/{league_id}/rosters") or []
        users = self.http.get(f"league/{league_id}/users") or []
        traded = self.http.get(f"league/{league_id}/traded_picks") or []
        directory = self.player_directory()

        by_user = {u.get("user_id"): u for u in users if u}
        teams: list[Team] = []
        for roster in rosters:
            teams.append(self._build_team(roster, by_user, directory))

        if not settings.teams:
            settings.teams = len(teams)

        # Pick horizon: the next draft plus the following years, widened to
        # cover any season the league has actually traded into.
        first_season = draft_season + 1 if draft_complete else draft_season
        seasons = list(range(first_season, first_season + horizon_years))
        traded_seasons = {
            int(t["season"])
            for t in traded
            if str(t.get("season", "")).isdigit() and int(t["season"]) >= first_season
        }
        seasons = sorted(set(seasons) | traded_seasons)

        ownership = build_pick_ownership(
            [t.roster_id for t in teams], seasons, rounds, traded
        )
        slots = self._draft_slots(drafts, teams, settings, draft_complete)
        for team in teams:
            team.picks = [
                Pick(
                    season=p.season,
                    round=p.round,
                    original_roster_id=p.original_roster_id,
                    owner_roster_id=p.owner_roster_id,
                    slot=slots.get(p.original_roster_id),
                    slot_estimated=True,
                )
                for p in ownership.get(team.roster_id, [])
            ]

        return League(
            league_id=str(league_json.get("league_id") or league_id),
            name=str(league_json.get("name") or "(unnamed)"),
            season=str(league_json.get("season") or ""),
            status=str(league_json.get("status") or ""),
            settings=settings,
            teams=teams,
            previous_league_id=league_json.get("previous_league_id") or None,
        )

    def _draft_shape(
        self, drafts: Sequence[dict[str, Any]], league_json: dict[str, Any]
    ) -> tuple[int, int, bool]:
        """Rookie draft round count, season and whether it has already run."""
        rookie_drafts = [
            d for d in drafts
            if d and str(d.get("type", "")).lower() != "auction"
        ]
        try:
            league_season = int(league_json.get("season") or 0)
        except (TypeError, ValueError):
            league_season = 0

        if not rookie_drafts:
            return 4, league_season, True

        def season_of(d: dict[str, Any]) -> int:
            try:
                return int(d.get("season") or 0)
            except (TypeError, ValueError):
                return 0

        latest = max(rookie_drafts, key=season_of)
        rounds = int((latest.get("settings") or {}).get("rounds") or 0) or 4
        status = str(latest.get("status") or "").lower()
        return rounds, season_of(latest) or league_season, status == "complete"

    def _draft_slots(
        self,
        drafts: Sequence[dict[str, Any]],
        teams: Sequence[Team],
        settings: LeagueSettings,
        draft_complete: bool,
    ) -> dict[int, int]:
        """Draft slot per roster: real order if Sleeper has one, else projected."""
        if not draft_complete:
            for draft in drafts or []:
                mapping = (draft or {}).get("slot_to_roster_id") or {}
                if mapping:
                    resolved: dict[int, int] = {}
                    for slot, roster_id in mapping.items():
                        try:
                            resolved[int(roster_id)] = int(slot)
                        except (TypeError, ValueError):
                            continue
                    if resolved:
                        return resolved
        return estimate_draft_slots(teams, settings.teams)

    def _build_team(
        self,
        roster: dict[str, Any],
        by_user: dict[str, Any],
        directory: dict[str, dict[str, Any]],
    ) -> Team:
        owner_id = roster.get("owner_id")
        user = by_user.get(owner_id) or {}
        metadata = user.get("metadata") or {}
        manager = str(user.get("display_name") or user.get("username") or "(orphan)")
        team_name = str(metadata.get("team_name") or "").strip() or f"Team {manager}"

        groups = split_roster(roster)
        settings = roster.get("settings") or {}

        def to_players(ids: Sequence[str], slot: str) -> list[RosterPlayer]:
            return [self._to_player(pid, slot, directory) for pid in ids]

        fpts = float(settings.get("fpts") or 0) + float(settings.get("fpts_decimal") or 0) / 100

        return Team(
            roster_id=int(roster.get("roster_id") or 0),
            owner_id=owner_id,
            manager=manager,
            team_name=team_name,
            starters=to_players(groups["starters"], "STARTER"),
            bench=to_players(groups["bench"], "BENCH"),
            taxi=to_players(groups["taxi"], "TAXI"),
            ir=to_players(groups["ir"], "IR"),
            wins=int(settings.get("wins") or 0),
            losses=int(settings.get("losses") or 0),
            ties=int(settings.get("ties") or 0),
            points_for=fpts,
        )

    @staticmethod
    def _to_player(
        sleeper_id: str, slot: str, directory: dict[str, dict[str, Any]]
    ) -> RosterPlayer:
        meta = directory.get(str(sleeper_id)) or {}
        name = str(
            meta.get("full_name")
            or " ".join(
                part for part in (meta.get("first_name"), meta.get("last_name")) if part
            )
            # Team defenses are keyed by team abbreviation, not a numeric id.
            or meta.get("team")
            or sleeper_id
        ).strip()
        position = str(
            meta.get("position")
            or (meta.get("fantasy_positions") or [""])[0]
            or ""
        ).upper()
        age = meta.get("age")
        try:
            age = float(age) if age is not None else None
        except (TypeError, ValueError):
            age = None
        return RosterPlayer(
            sleeper_id=str(sleeper_id),
            name=name,
            position=position,
            team=str(meta.get("team") or "").upper(),
            age=age,
            slot=slot,
        )

    # -- history (gotcha 4) ----------------------------------------------

    def league_chain(self, league_id: str, max_seasons: int = 6) -> list[dict[str, Any]]:
        """Walk ``previous_league_id`` backward. Dynasty leagues get a new id
        every season, so a single league_id is one season of history."""
        chain: list[dict[str, Any]] = []
        current: str | None = league_id
        seen: set[str] = set()
        while current and current not in seen and len(chain) < max_seasons:
            seen.add(current)
            payload = self.http.get(f"league/{current}")
            if not payload:
                break
            chain.append(payload)
            nxt = payload.get("previous_league_id")
            current = str(nxt) if nxt and str(nxt) not in ("0", "None") else None
        return chain

    def load_trades(self, league_id: str, max_seasons: int = 6) -> list[Trade]:
        """Every completed trade across this league's history.

        There is no bulk transactions endpoint, so this pages week by week
        through each season in the chain (gotcha 4).
        """
        trades: list[Trade] = []
        for league_json in self.league_chain(league_id, max_seasons=max_seasons):
            lid = str(league_json.get("league_id"))
            season = str(league_json.get("season") or "")
            settings = league_json.get("settings") or {}
            last_week = int(
                settings.get("playoff_week_start") or 0
            ) or 0
            weeks = range(1, (last_week + 4) if last_week else 19)
            for week in weeks:
                payload = self.http.get(
                    f"league/{lid}/transactions/{week}", ttl=6 * 60 * 60
                ) or []
                for txn in payload:
                    if not txn or txn.get("type") != "trade":
                        continue
                    if str(txn.get("status") or "complete") != "complete":
                        continue
                    trades.append(
                        Trade(
                            transaction_id=str(txn.get("transaction_id") or ""),
                            season=season,
                            week=week,
                            created=int(txn.get("created") or 0),
                            roster_ids=[int(r) for r in (txn.get("roster_ids") or [])],
                            adds=dict(txn.get("adds") or {}),
                            drops=dict(txn.get("drops") or {}),
                            draft_picks=list(txn.get("draft_picks") or []),
                            waiver_budget=list(txn.get("waiver_budget") or []),
                        )
                    )
        trades.sort(key=lambda t: t.created, reverse=True)
        return trades


# --------------------------------------------------------------------------
# pick labelling bridge
# --------------------------------------------------------------------------

def label_pick(pick: Pick, book: ValueBook, teams: int) -> str:
    """Name a pick the way the values file names it at that horizon."""
    return book.pick_label(pick.season, pick.round, pick.slot, teams=teams)


__all__ = [
    "SleeperClient",
    "SleeperError",
    "HttpTransport",
    "DictTransport",
    "League",
    "LeagueSettings",
    "Team",
    "RosterPlayer",
    "RosterSlot",
    "Pick",
    "Trade",
    "detect_settings",
    "parse_roster_positions",
    "split_roster",
    "build_pick_ownership",
    "estimate_draft_slots",
    "label_pick",
    "tier_for_slot",
    "OFFENSE_POSITIONS",
]
