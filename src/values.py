"""DynastyProcess values adapter.

This is the ONLY module in the codebase that touches a values CSV. Everything
downstream asks a ``ValueBook`` for numbers and never learns where they came
from, so swapping in KeepTradeCut or a hand-maintained sheet means writing a
new class with the same surface, not editing the matchmaker.

Data: https://github.com/dynastyprocess/data (Tan Ho, Joe Sydlowski).
No key, no auth, auto-updated. Cached to disk for 12h.

The load-bearing piece here is ``pick_value``. ``values-picks.csv`` ships ECR
(expert consensus rank), not values, so picks and players arrive on two
different scales and cannot be compared. We fix that by building a monotone
ECR -> value curve out of the *player* file and reading pick ECR through it.
After that a 2027 1st and a WR2 are the same kind of number.
"""

from __future__ import annotations

import csv
import json
import math
import os
import re
import time
import unicodedata
import urllib.request
from bisect import bisect_left
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

BASE_URL = "https://raw.githubusercontent.com/dynastyprocess/data/master/files/"

PLAYERS_FILE = "values-players.csv"
PICKS_FILE = "values-picks.csv"
IDS_FILE = "db_playerids.csv"

CACHE_TTL_SECONDS = 12 * 60 * 60

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
# Apostrophes and periods are dropped outright: one source writes "Ja'Marr" and
# another may write "JaMarr", and only deletion makes those agree. Everything
# else (hyphens especially) becomes a space, because "Smith-Njigba" and
# "Smith Njigba" both occur in the wild.
_DROP = re.compile(r"[.'’ʼ`]")
_PUNCT = re.compile(r"[^\w\s]")
_WS = re.compile(r"\s+")


# --------------------------------------------------------------------------
# name normalization (gotcha 6)
# --------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Fold a player name to a match key.

    Strips accents, punctuation and generational suffixes, lowercases, and
    collapses whitespace. This still misses roughly 0.5% of the league (Michael
    vs Mike, mid-season name changes), which is why production joins on
    ``sleeper_id`` and only falls back to names.
    """
    if not name:
        return ""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    cleaned = _PUNCT.sub(" ", _DROP.sub("", ascii_only)).lower()
    tokens = _WS.sub(" ", cleaned).strip().split(" ")
    while len(tokens) > 2 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


# --------------------------------------------------------------------------
# records
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PlayerValue:
    """One player's dynasty value in both formats."""

    name: str
    position: str
    team: str
    age: float | None
    draft_year: int | None
    ecr_1qb: float | None
    ecr_2qb: float | None
    ecr_pos: float | None
    value_1qb: float
    value_2qb: float
    fp_id: str
    sleeper_id: str | None = None

    def value(self, superflex: bool = False) -> float:
        return self.value_2qb if superflex else self.value_1qb

    def ecr(self, superflex: bool = False) -> float | None:
        return self.ecr_2qb if superflex else self.ecr_1qb


@dataclass(frozen=True)
class PickValue:
    """A draft pick priced on the same scale as players."""

    label: str
    season: int
    round: int
    slot: int | None
    value: float
    ecr: float
    exact: bool


# --------------------------------------------------------------------------
# disk cache
# --------------------------------------------------------------------------

def default_cache_dir() -> Path:
    env = os.environ.get("DYNASTY_CACHE_DIR")
    if env:
        return Path(env)
    return Path.home() / ".cache" / "dynasty-matchmaker"


class CsvCache:
    """Fetch-and-cache for the DynastyProcess CSVs, 12h TTL.

    A stale cache beats a failed run: if the network is down but we have an old
    copy, we use it. ``offline=True`` refuses to fetch at all, which is what the
    test suite uses.
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        offline: bool = False,
        base_url: str = BASE_URL,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.ttl_seconds = ttl_seconds
        self.offline = offline
        self.base_url = base_url
        self.notes: list[str] = []

    def _paths(self, filename: str) -> tuple[Path, Path]:
        return (
            self.cache_dir / filename,
            self.cache_dir / f"{filename}.meta.json",
        )

    def age_seconds(self, filename: str) -> float | None:
        data_path, meta_path = self._paths(filename)
        if not data_path.exists() or not meta_path.exists():
            return None
        try:
            meta = json.loads(meta_path.read_text())
            return max(0.0, time.time() - float(meta["fetched_at"]))
        except (ValueError, KeyError, OSError):
            return None

    def read(self, filename: str) -> str:
        data_path, meta_path = self._paths(filename)
        age = self.age_seconds(filename)

        if age is not None and age < self.ttl_seconds:
            return data_path.read_text(encoding="utf-8")

        if self.offline:
            if age is not None:
                self.notes.append(f"{filename}: offline, using cache {age / 3600:.1f}h old")
                return data_path.read_text(encoding="utf-8")
            raise FileNotFoundError(
                f"{filename} not in cache at {self.cache_dir} and offline=True"
            )

        try:
            text = self._fetch(filename)
        except Exception as exc:  # network down, proxy denial, GitHub hiccup
            if age is not None:
                self.notes.append(
                    f"{filename}: fetch failed ({exc}); using cache {age / 3600:.1f}h old"
                )
                return data_path.read_text(encoding="utf-8")
            raise

        self.cache_dir.mkdir(parents=True, exist_ok=True)
        data_path.write_text(text, encoding="utf-8")
        meta_path.write_text(json.dumps({"fetched_at": time.time()}), encoding="utf-8")
        return text

    def _fetch(self, filename: str) -> str:
        req = urllib.request.Request(
            self.base_url + filename,
            headers={"User-Agent": "dynasty-matchmaker/0.1"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.read().decode("utf-8")


# Column aliases for an external value sheet. KeepTradeCut, FantasyCalc and a
# hand-kept spreadsheet all name these differently; none of them is wrong.
_NAME_COLS = ("player", "name", "player_name", "playername", "full_name")
_SLEEPER_COLS = ("sleeper_id", "sleeperid", "sleeper")
_ONE_QB_COLS = ("value_1qb", "value", "ktc_value", "1qb", "value1qb", "oneqb_value")
_SUPERFLEX_COLS = ("value_2qb", "sf_value", "superflex", "value_sf", "2qb", "sfvalue")
_KTC_COLS = ("ktc_id", "ktcid", "ktc")


def _pick_column(fieldnames: Sequence[str], candidates: Sequence[str]) -> str | None:
    lowered = {f.strip().lower().replace(" ", "_"): f for f in fieldnames if f}
    for candidate in candidates:
        if candidate in lowered:
            return lowered[candidate]
    return None


def load_value_overrides(path: str | Path) -> dict[str, tuple[float, float | None]]:
    """Read an external value sheet: {key: (value_1qb, value_2qb or None)}.

    Keys are ``sleeper:<id>`` where the sheet supplies one and a normalized name
    otherwise, so a sheet with ids joins exactly and a sheet without still joins
    on names. Column names are sniffed rather than mandated -- a KeepTradeCut
    export, a FantasyCalc download and a hand-kept spreadsheet all label the same
    two numbers differently.
    """
    text = Path(path).read_text(encoding="utf-8")
    if text.lstrip().startswith(("[", "{")):
        return _overrides_from_json(text)
    reader = csv.DictReader(text.splitlines())
    fields = reader.fieldnames or []

    name_col = _pick_column(fields, _NAME_COLS)
    sleeper_col = _pick_column(fields, _SLEEPER_COLS)
    ktc_col = _pick_column(fields, _KTC_COLS)
    one_col = _pick_column(fields, _ONE_QB_COLS)
    sf_col = _pick_column(fields, _SUPERFLEX_COLS)

    if one_col is None and sf_col is None:
        raise ValueError(
            f"{path}: no value column found. Expected one of "
            f"{', '.join(_ONE_QB_COLS + _SUPERFLEX_COLS)}; saw {fields}"
        )
    if name_col is None and sleeper_col is None:
        raise ValueError(
            f"{path}: no player column found. Expected one of "
            f"{', '.join(_NAME_COLS + _SLEEPER_COLS)}; saw {fields}"
        )

    out: dict[str, tuple[float, float | None]] = {}
    for row in reader:
        one = _num(row.get(one_col)) if one_col else None
        sf = _num(row.get(sf_col)) if sf_col else None
        if one is None and sf is None:
            continue
        entry = (one if one is not None else sf, sf)
        _index(out, row.get(sleeper_col) if sleeper_col else None,
               row.get(ktc_col) if ktc_col else None,
               row.get(name_col) if name_col else None, entry)
    return out


def _index(
    out: dict[str, tuple[float, float | None]],
    sleeper_id: str | None,
    ktc_id: str | None,
    name: str | None,
    entry: tuple[float, float | None],
) -> None:
    """File every key the sheet gives us, most reliable first at lookup time."""
    sid = (sleeper_id or "").strip()
    if sid and sid not in ("0", "NA"):
        out[f"sleeper:{sid}"] = entry
    kid = (ktc_id or "").strip()
    if kid and kid not in ("0", "NA"):
        out[f"ktc:{kid}"] = entry
    key = normalize_name(name or "")
    if key:
        out.setdefault(key, entry)


def _overrides_from_json(text: str) -> dict[str, tuple[float, float | None]]:
    """Read the JSON a trade-sourced API returns.

    FantasyCalc derives its numbers from trades that actually completed in real
    leagues, which is the closest thing to a market price this data has. Its
    payload nests the player, so the fields are dug out defensively rather than
    assumed -- this path is written to a documented shape but has not been run
    against the live endpoint from here, because that host is unreachable in
    this environment.
    """
    payload = json.loads(text)
    if isinstance(payload, dict):
        payload = payload.get("players") or payload.get("values") or []
    out: dict[str, tuple[float, float | None]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        inner = item.get("player") if isinstance(item.get("player"), dict) else {}
        value = _num(str(item.get("value", inner.get("value", ""))))
        if value is None:
            continue
        _index(
            out,
            str(inner.get("sleeperId") or item.get("sleeperId") or ""),
            str(inner.get("ktcId") or item.get("ktcId") or ""),
            str(inner.get("name") or item.get("name") or ""),
            (value, None),
        )
    return out


def _rows(text: str) -> list[dict[str, str]]:
    return list(csv.DictReader(text.splitlines()))


def _num(raw: str | None) -> float | None:
    if raw is None:
        return None
    raw = raw.strip()
    if raw in ("", "NA", "NULL", "None", "nan"):
        return None
    try:
        return float(raw)
    except ValueError:
        return None


# --------------------------------------------------------------------------
# the ECR -> value curve (gotcha 1)
# --------------------------------------------------------------------------

class EcrValueCurve:
    """Maps an expert-consensus rank onto the player value scale.

    Built from the player file, where both ECR and value are known, then
    applied to picks, where only ECR is known.

    Two wrinkles handled here:

    * The raw (ecr, value) scatter is not perfectly monotone -- consensus rank
      and consensus value are separate scrapes and disagree at the margins. A
      pool-adjacent-violators pass enforces "worse rank is never worth more",
      which is a property trade balancing depends on.
    * Value decays geometrically with rank, not linearly, so interpolation
      happens in log space. Linear interpolation on raw values overprices the
      middle of every gap, and the gaps at the top of the board are enormous.
    """

    def __init__(self, pairs: Iterable[tuple[float, float]]) -> None:
        usable = sorted((e, v) for e, v in pairs if e is not None and v and v > 0)
        if not usable:
            raise ValueError("cannot build an ECR curve with no (ecr, value) pairs")

        # Average duplicate ECRs before smoothing.
        merged: list[tuple[float, float]] = []
        for ecr, value in usable:
            if merged and math.isclose(merged[-1][0], ecr):
                prev_ecr, prev_val = merged.pop()
                merged.append((prev_ecr, (prev_val + value) / 2))
            else:
                merged.append((ecr, value))

        self._ecrs = [e for e, _ in merged]
        logs = [math.log(v) for _, v in merged]
        self._logs = _isotonic_decreasing(logs)

    def value_at(self, ecr: float) -> float:
        """Value for any rank, interpolated in log space and clamped at the ends."""
        ecrs, logs = self._ecrs, self._logs
        if ecr <= ecrs[0]:
            return math.exp(logs[0])
        if ecr >= ecrs[-1]:
            # Extrapolate along the final slope rather than flat-lining, so the
            # far end of a deep rookie draft still separates.
            if len(ecrs) >= 2 and ecrs[-1] > ecrs[-2]:
                slope = (logs[-1] - logs[-2]) / (ecrs[-1] - ecrs[-2])
                return max(0.0, math.exp(logs[-1] + slope * (ecr - ecrs[-1])))
            return math.exp(logs[-1])

        i = bisect_left(ecrs, ecr)
        lo, hi = ecrs[i - 1], ecrs[i]
        frac = 0.0 if hi == lo else (ecr - lo) / (hi - lo)
        return math.exp(logs[i - 1] + frac * (logs[i] - logs[i - 1]))


def _isotonic_decreasing(values: Sequence[float]) -> list[float]:
    """Least-squares fit of a non-increasing sequence (pool adjacent violators)."""
    levels: list[float] = []
    weights: list[float] = []
    for value in values:
        levels.append(value)
        weights.append(1.0)
        # Merge backwards while the sequence rises.
        while len(levels) > 1 and levels[-2] < levels[-1]:
            v2, w2 = levels.pop(), weights.pop()
            v1, w1 = levels.pop(), weights.pop()
            levels.append((v1 * w1 + v2 * w2) / (w1 + w2))
            weights.append(w1 + w2)

    out: list[float] = []
    for level, weight in zip(levels, weights):
        out.extend([level] * int(weight))
    return out


# --------------------------------------------------------------------------
# pick labels (gotcha 2)
# --------------------------------------------------------------------------

_EXACT_RE = re.compile(r"^(\d{4})\s+pick\s+(\d+)\.(\d+)$")
_TIER_RE = re.compile(r"^(\d{4})\s+(early|mid|late)\s+(\d+)(?:st|nd|rd|th)$")
_ROUND_RE = re.compile(r"^(\d{4})\s+(\d+)(?:st|nd|rd|th)$")

_ORDINALS = {1: "1st", 2: "2nd", 3: "3rd"}


def _ordinal(n: int) -> str:
    return _ORDINALS.get(n, f"{n}th")


def normalize_pick_label(label: str) -> str:
    return _WS.sub(" ", label.strip().lower())


def tier_for_slot(slot: int, teams: int) -> str:
    """Which third of the round a draft slot falls in."""
    if teams <= 0:
        return "mid"
    third = teams / 3.0
    if slot <= math.ceil(third):
        return "early"
    if slot <= math.ceil(2 * third):
        return "mid"
    return "late"


# --------------------------------------------------------------------------
# ValueBook
# --------------------------------------------------------------------------

class ValueBook:
    """Player and pick values on one comparable scale."""

    def __init__(
        self,
        cache_dir: Path | None = None,
        offline: bool = False,
        source_dir: Path | None = None,
        ttl_seconds: int = CACHE_TTL_SECONDS,
        overrides: str | Path | dict[str, tuple[float, float | None]] | None = None,
    ) -> None:
        """
        ``source_dir`` reads three CSVs straight off disk with no network and no
        cache, which is how the offline tests run.
        """
        if source_dir is not None:
            source = Path(source_dir)
            texts = {
                name: (source / name).read_text(encoding="utf-8")
                for name in (PLAYERS_FILE, PICKS_FILE, IDS_FILE)
            }
            self.notes: list[str] = []
        else:
            cache = CsvCache(cache_dir=cache_dir, offline=offline, ttl_seconds=ttl_seconds)
            texts = {name: cache.read(name) for name in (PLAYERS_FILE, PICKS_FILE, IDS_FILE)}
            self.notes = cache.notes

        if overrides is None:
            self._overrides: dict[str, tuple[float, float | None]] = {}
        elif isinstance(overrides, dict):
            self._overrides = dict(overrides)
        else:
            self._overrides = load_value_overrides(overrides)

        ids_rows = _rows(texts[IDS_FILE])
        self._fp_to_sleeper = self._load_crosswalk(ids_rows)
        self._fp_to_ktc = {
            (r.get("fantasypros_id") or "").strip(): (r.get("ktc_id") or "").strip()
            for r in ids_rows
            if (r.get("fantasypros_id") or "").strip()
            and (r.get("ktc_id") or "").strip() not in ("", "NA", "0")
        }
        self._load_players(_rows(texts[PLAYERS_FILE]))
        self._load_picks(_rows(texts[PICKS_FILE]))

    # -- loading ----------------------------------------------------------

    @staticmethod
    def _load_crosswalk(rows: list[dict[str, str]]) -> dict[str, str]:
        out: dict[str, str] = {}
        for row in rows:
            fp_id = (row.get("fantasypros_id") or "").strip()
            sleeper_id = (row.get("sleeper_id") or "").strip()
            if fp_id and sleeper_id and sleeper_id not in ("NA", "0"):
                out[fp_id] = sleeper_id
        return out

    def _load_players(self, rows: list[dict[str, str]]) -> None:
        self._override_hits = 0
        self._by_sleeper: dict[str, PlayerValue] = {}
        self._by_name: dict[str, PlayerValue] = {}
        self._players: list[PlayerValue] = []

        for row in rows:
            fp_id = (row.get("fp_id") or "").strip()
            value_1qb = _num(row.get("value_1qb")) or 0.0
            value_2qb = _num(row.get("value_2qb")) or 0.0
            draft_year = _num(row.get("draft_year"))
            name = (row.get("player") or "").strip()
            sleeper_id = self._fp_to_sleeper.get(fp_id)
            if self._overrides:
                # An external sheet replaces the value but keeps the ECR, so the
                # pick curve refits to the new scale and picks stay comparable
                # to players -- which is the whole point of gotcha 1.
                hit = self._overrides.get(f"sleeper:{sleeper_id}") if sleeper_id else None
                if hit is None:
                    ktc_id = self._fp_to_ktc.get(fp_id)
                    hit = self._overrides.get(f"ktc:{ktc_id}") if ktc_id else None
                if hit is None:
                    hit = self._overrides.get(normalize_name(name))
                if hit is not None:
                    value_1qb = hit[0]
                    value_2qb = hit[1] if hit[1] is not None else hit[0]
                    self._override_hits += 1

            player = PlayerValue(
                name=name,
                position=(row.get("pos") or "").strip().upper(),
                team=(row.get("team") or "").strip().upper(),
                age=_num(row.get("age")),
                draft_year=int(draft_year) if draft_year else None,
                ecr_1qb=_num(row.get("ecr_1qb")),
                ecr_2qb=_num(row.get("ecr_2qb")),
                ecr_pos=_num(row.get("ecr_pos")),
                value_1qb=value_1qb,
                value_2qb=value_2qb,
                fp_id=fp_id,
                sleeper_id=sleeper_id,
            )
            self._players.append(player)
            if player.sleeper_id:
                self._by_sleeper[player.sleeper_id] = player
            key = normalize_name(player.name)
            # First writer wins: the file is ranked, so the more valuable of two
            # players sharing a normalized name keeps the key.
            if key and key not in self._by_name:
                self._by_name[key] = player

        if self._overrides:
            self.notes.append(
                f"value override: {self._override_hits}/{len(self._players)} players "
                f"repriced from {len(self._overrides)} supplied rows"
            )

        self._curve_1qb = EcrValueCurve(
            (p.ecr_1qb, p.value_1qb) for p in self._players if p.ecr_1qb is not None
        )
        self._curve_2qb = EcrValueCurve(
            (p.ecr_2qb, p.value_2qb) for p in self._players if p.ecr_2qb is not None
        )

    def _load_picks(self, rows: list[dict[str, str]]) -> None:
        # label -> (ecr_1qb, ecr_2qb)
        self._pick_ecr: dict[str, tuple[float, float]] = {}
        # season -> overall pick number -> (ecr_1qb, ecr_2qb), for exact seasons
        self._exact_by_overall: dict[int, list[tuple[int, float, float]]] = {}
        # (season, round) -> (ecr_1qb, ecr_2qb) for round-only rows
        self._round_only: dict[tuple[int, int], tuple[float, float]] = {}
        seasons: set[int] = set()

        for row in rows:
            label = (row.get("player") or "").strip()
            ecr1 = _num(row.get("ecr_1qb"))
            ecr2 = _num(row.get("ecr_2qb"))
            if not label or ecr1 is None or ecr2 is None:
                continue
            key = normalize_pick_label(label)
            self._pick_ecr[key] = (ecr1, ecr2)

            exact = _EXACT_RE.match(key)
            if exact:
                season, rnd, slot = int(exact.group(1)), int(exact.group(2)), int(exact.group(3))
                seasons.add(season)
                overall = _num(row.get("pick"))
                # The file indexes exact picks by overall number in a 12-team
                # draft; derive it if the column is missing.
                if overall is None:
                    overall = (rnd - 1) * 12 + slot
                self._exact_by_overall.setdefault(season, []).append((int(overall), ecr1, ecr2))
                continue

            round_only = _ROUND_RE.match(key)
            if round_only:
                season, rnd = int(round_only.group(1)), int(round_only.group(2))
                seasons.add(season)
                self._round_only[(season, rnd)] = (ecr1, ecr2)
                continue

            tier = _TIER_RE.match(key)
            if tier:
                seasons.add(int(tier.group(1)))

        for season in self._exact_by_overall:
            self._exact_by_overall[season].sort()

        self._pick_seasons = sorted(seasons)
        self._exact_seasons = sorted(self._exact_by_overall)
        self._far_season_decay = self._estimate_season_decay()

    def _estimate_season_decay(self) -> float:
        """Per-year discount for seasons past the end of the pick file.

        Derived from the file itself: compare round-only values across the two
        furthest seasons. A 2029 1st is not in the data, but it is worth
        meaningfully less than a 2028 1st, and the data already tells us by how
        much a year of distance costs.
        """
        seasons = sorted({s for s, _ in self._round_only})
        if len(seasons) < 2:
            return 0.85
        near, far = seasons[-2], seasons[-1]
        ratios: list[float] = []
        for (season, rnd), (ecr1, _) in self._round_only.items():
            if season != far:
                continue
            near_ecr = self._round_only.get((near, rnd))
            if not near_ecr:
                continue
            near_val = self._curve_1qb.value_at(near_ecr[0])
            far_val = self._curve_1qb.value_at(ecr1)
            if near_val > 0:
                ratios.append(far_val / near_val)
        if not ratios:
            return 0.85
        return max(0.5, min(1.0, sum(ratios) / len(ratios)))

    # -- player lookups ---------------------------------------------------

    def get_by_sleeper(self, sleeper_id: str | int | None) -> PlayerValue | None:
        if sleeper_id is None:
            return None
        return self._by_sleeper.get(str(sleeper_id).strip())

    def get(self, name: str) -> PlayerValue | None:
        return self._by_name.get(normalize_name(name))

    def value(self, name: str, superflex: bool = False) -> float:
        player = self.get(name)
        return player.value(superflex) if player else 0.0

    def value_by_sleeper(self, sleeper_id: str | int | None, superflex: bool = False) -> float:
        player = self.get_by_sleeper(sleeper_id)
        return player.value(superflex) if player else 0.0

    @property
    def players(self) -> list[PlayerValue]:
        return list(self._players)

    @property
    def pick_seasons(self) -> list[int]:
        return list(self._pick_seasons)

    def curve(self, superflex: bool = False) -> EcrValueCurve:
        return self._curve_2qb if superflex else self._curve_1qb

    # -- pick lookups -----------------------------------------------------

    def pick_label(self, season: int, rnd: int, slot: int | None, teams: int = 12) -> str:
        """Build the label this pick is known by at its horizon (gotcha 2).

        Near seasons are priced pick-by-pick, the next one by tier, and anything
        beyond that by round only, because nobody knows where a 2028 pick lands.
        Which season is which comes from the data, not a constant.
        """
        if self._exact_seasons and season <= self._exact_seasons[-1] and slot:
            return f"{season} Pick {rnd}.{slot:02d}"
        tier_season = (self._exact_seasons[-1] + 1) if self._exact_seasons else season
        if season == tier_season and slot:
            return f"{season} {tier_for_slot(slot, teams).title()} {_ordinal(rnd)}"
        return f"{season} {_ordinal(rnd)}"

    def pick_value(self, label: str, superflex: bool = False, teams: int = 12) -> float:
        """Value of a draft pick, on the player scale.

        Accepts every label shape in the file plus the ones that are not: a
        14-team league's ``2026 Pick 1.13`` has no row, so we interpolate the
        exact-pick ECR curve at that overall selection number. Nothing here
        assumes a 12-team league.
        """
        pick = self.pick(label, superflex=superflex, teams=teams)
        return pick.value if pick else 0.0

    def pick(self, label: str, superflex: bool = False, teams: int = 12) -> PickValue | None:
        key = normalize_pick_label(label)
        curve = self.curve(superflex)
        idx = 1 if superflex else 0

        season, rnd, slot, exact = self._parse_label(key)

        # Exact picks resolve by overall selection number rather than by label,
        # because a 10-team 1.10 is the last pick of the round and a 12-team
        # 1.10 is third from last -- different assets that share a label. What
        # actually holds across league sizes is "the Nth rookie off the board",
        # and for a 12-team league this reproduces the file rows exactly.
        if exact and season in self._exact_by_overall and slot:
            ecr = self._interp_overall(
                self._exact_by_overall[season], (rnd - 1) * teams + slot, idx
            )
            return PickValue(
                label=label, season=season, round=rnd, slot=slot,
                value=curve.value_at(ecr), ecr=ecr, exact=True,
            )

        direct = self._pick_ecr.get(key)
        if direct is not None:
            return PickValue(
                label=label,
                season=season or 0,
                round=rnd or 0,
                slot=slot,
                value=curve.value_at(direct[idx]),
                ecr=direct[idx],
                exact=exact,
            )

        if season is None or rnd is None:
            return None

        ecr = self._resolve_ecr(season, rnd, slot, teams, idx)
        if ecr is None:
            return None

        value = curve.value_at(ecr)

        # Seasons beyond the file get discounted per extra year of distance.
        if self._pick_seasons and season > self._pick_seasons[-1]:
            value *= self._far_season_decay ** (season - self._pick_seasons[-1])

        return PickValue(
            label=label, season=season, round=rnd, slot=slot, value=value, ecr=ecr, exact=exact
        )

    @staticmethod
    def _interp_overall(
        entries: list[tuple[int, float, float]], overall: int, idx: int
    ) -> float:
        """ECR at an overall selection number, interpolated between listed picks.

        Deep leagues run past the last pick the file lists, so the tail
        continues along the final slope instead of flat-lining -- a 14-team
        league's 5.14 should still be worth less than its 5.01.
        """
        overalls = [e[0] for e in entries]
        ecrs = [e[1 + idx] for e in entries]
        if overall <= overalls[0]:
            return ecrs[0]
        if overall >= overalls[-1]:
            if len(overalls) >= 2 and overalls[-1] > overalls[-2]:
                slope = (ecrs[-1] - ecrs[-2]) / (overalls[-1] - overalls[-2])
                return ecrs[-1] + slope * (overall - overalls[-1])
            return ecrs[-1]
        i = bisect_left(overalls, overall)
        lo, hi = overalls[i - 1], overalls[i]
        frac = 0.0 if hi == lo else (overall - lo) / (hi - lo)
        return ecrs[i - 1] + frac * (ecrs[i] - ecrs[i - 1])

    @staticmethod
    def _parse_label(key: str) -> tuple[int | None, int | None, int | None, bool]:
        m = _EXACT_RE.match(key)
        if m:
            return int(m.group(1)), int(m.group(2)), int(m.group(3)), True
        m = _TIER_RE.match(key)
        if m:
            return int(m.group(1)), int(m.group(3)), None, False
        m = _ROUND_RE.match(key)
        if m:
            return int(m.group(1)), int(m.group(2)), None, False
        return None, None, None, False

    def _resolve_ecr(
        self, season: int, rnd: int, slot: int | None, teams: int, idx: int
    ) -> float | None:
        """Find an ECR for a pick the file does not list verbatim."""
        # 1. An exact season with a slot the file lacks (non-12-team league):
        #    interpolate the exact-pick curve at this overall selection number.
        if season in self._exact_by_overall and slot:
            overall = (rnd - 1) * teams + slot
            return self._interp_overall(self._exact_by_overall[season], overall, idx)

        # 2. A tier season asked for by exact slot: fall back to its tier.
        if slot:
            tier_key = normalize_pick_label(
                f"{season} {tier_for_slot(slot, teams)} {_ordinal(rnd)}"
            )
            tiered = self._pick_ecr.get(tier_key)
            if tiered:
                return tiered[idx]

        # 3. Round-only for this season.
        round_key = normalize_pick_label(f"{season} {_ordinal(rnd)}")
        listed = self._pick_ecr.get(round_key)
        if listed:
            return listed[idx]

        # 4. Season past the end of the file: price it off the furthest season
        #    we do have and let the caller apply the distance discount.
        if self._pick_seasons and season > self._pick_seasons[-1]:
            fallback = self._round_only.get((self._pick_seasons[-1], rnd))
            if fallback:
                return fallback[idx]

        # 5. A round deeper than the file goes (some leagues draft 6+ rounds):
        #    use the deepest round listed for the nearest season we have.
        candidates = [r for (s, r) in self._round_only if s == season]
        if candidates:
            deepest = max(candidates)
            if rnd > deepest:
                return self._round_only[(season, deepest)][idx]
        return None


__all__ = [
    "ValueBook",
    "PlayerValue",
    "PickValue",
    "EcrValueCurve",
    "CsvCache",
    "normalize_name",
    "load_value_overrides",
    "normalize_pick_label",
    "tier_for_slot",
    "BASE_URL",
]
