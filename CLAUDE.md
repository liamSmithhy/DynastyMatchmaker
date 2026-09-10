# Dynasty Trade Matchmaker

## What this is

Generates dynasty fantasy football trade proposals. Existing tools grade a trade
you already constructed. This one scans every roster in a league and surfaces
trades that *should* happen, ranked, with pitch text.

Pricing: $9.99 per league per season. Commissioner pays, ~12 managers use it.

## Hard constraint: no LLM in the matching path

Surplus/deficit detection, window classification, value balancing, and trade
validity are arithmetic — deterministic code, reproducible, testable, free.

The only LLM call converts a finished proposal into pitch text, and it must be
cacheable.

This is economics, not style: at $9.99/league across five active months with
repeated in-season use, per-match model calls go underwater.

## Analytical framework — four pillars, in order

### 1. Positional value in format

Detect settings from the league, never assume.

1QB full PPR:
- WR decides weeks
- RB depreciates fastest
- QB is cheapest to replace (backup QBs are dead roster spots worth zero)
- TE without premium is near-streamable below the top tier

Superflex inverts QB value entirely.

### 2. Age curves

Age is a value curve, not a footnote. A 33-year-old and a 23-year-old producing
identically are different assets. Trade value collapses *ahead of* production.

### 3. Draft capital

~1/3 of a roster's worth. Never grade a roster without picks.

Capital has a timeline: 2028 firsts attached to a core aging out in 2027 is a
contradiction, not an asset.

### 4. Deployed vs held value

The key diagnostic is the gap between starter rank and total-value rank:

| Signal | Window | Posture |
| --- | --- | --- |
| both strong | CONTENDER | push |
| starter >> total | TOP-HEAVY | mortgaged, no reload |
| total >> starter | RETOOLER | consolidate |
| both middling | STUCK | pick a direction |
| both weak + capital | REBUILD | hold |

Classify on the **GAP**, never the raw number. Two teams can hold near-identical
total value and be in opposite situations.

## Data sources (verified live 2026-08-26)

### Values — DynastyProcess open data

No key, no auth, auto-updated.

Base: `https://raw.githubusercontent.com/dynastyprocess/data/master/files/`

| File | Contents |
| --- | --- |
| `values-players.csv` | ~700 players: `value_1qb`, `value_2qb`, `age`, `ecr_1qb`, `fp_id` (latest scrape 2026-08-21) |
| `values-picks.csv` | pick ECR, **NOT** values (see gotcha 1) |
| `db_playerids.csv` | ~12,500 rows, `sleeper_id` <-> `fantasypros_id` crosswalk |

- Cache 12h.
- Credit DynastyProcess (Tan Ho, Joe Sydlowski) in the UI.
- Confirm repo license before charging money.

### League data — Sleeper

`https://docs.sleeper.com`, read-only, no token.

**OPEN BLOCKER:** free for NON-COMMERCIAL use only. Commercial use requires
contacting Sleeper for licensing. Unresolved — gates the paid tier.

Full player endpoint ~5MB: call once daily max.

## Known gotchas — do not rediscover

1. `values-picks.csv` ships ECR, not values. Convert through the player
   ECR/value curve so picks and players share one scale, or trade balancing is
   impossible.
2. Pick labels change by horizon: `"2026 Pick 1.01"` (exact) /
   `"2027 Mid 1st"` (tier) / `"2028 3rd"` (round only).
3. Sleeper only reports picks that **MOVED**. Seed every roster with its own
   picks across the horizon, THEN apply `traded_picks`. Otherwise teams that
   never traded show zero picks.
4. Dynasty leagues get a new `league_id` each season. Walk `previous_league_id`
   backward for history. No bulk transactions endpoint — page by week.
5. Roster arrays overlap (`players`/`starters`/`taxi`/`reserve`) and contain
   `"0"` placeholders. Bench is a set difference, not a slice.
6. Name matching needs normalization (strip punctuation, Jr/Sr/II/III) and still
   misses ~0.5%. Join on `sleeper_id` in production.

## Architecture rules

- `src/values.py` is a swappable adapter. Nothing else touches a values CSV.
- `src/sleeper.py` is a swappable adapter. Nothing downstream sees raw Sleeper
  JSON. ESPN/Yahoo adapters must slot in behind the same interface.
- Works on ANY league: arbitrary team count, Superflex, TE premium, non-PPR.
  Never hardcode for one league.
- Transform logic must be testable offline, without network.

## Working style

Direct and practical. Challenge the approach when there's a better one. Compare
options and recommend the strongest. Flag risks and blind spots proactively.

---

## Current status (updated after the first build)

**Shipping free, non-commercial. No paid tier, no billing code.** The pricing
model above remains the intent, but Sleeper's API is licensed for
non-commercial use, so charging for it is blocked until they answer in writing.
Every feature — multi-team deals included — is enabled for everyone meanwhile.
See `LICENSING.md` for what to ask them for.

`python3 -m src.cli demo` runs the whole pipeline on a bundled sample league
with no account and no network. Use it to show what the tool does.

`python3 -m src.cli report <username>` writes a self-contained HTML page: the
board and the ranked proposals, with a copyable message from either side.

Two per-league knobs handle a market the consensus board does not share:
`--exclude-position QB` keeps a position out of every trade, and
`--weight QB=0.3` scales a position's value. FMB uses the former — that league
does not pay for quarterbacks. Neither names a position in the code. The demo is the same
renderer pointed at sample data, so there is no demo codepath to drift from what
a real league produces. `src/report.py` owns it; `snapshot` captures a league to
disk for offline replay.

Stage 1 was verified against live DynastyProcess data. Stages 2-4 were verified
against a fixture built to the documented Sleeper schemas, because the build
sandbox blocked every Sleeper host at its egress proxy; `scripts/verify_stage2.py`
tries live first and falls back, so re-running it anywhere Sleeper is reachable
verifies against the real league with no code change.
