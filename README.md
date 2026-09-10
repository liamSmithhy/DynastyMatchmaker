# Dynasty Trade Matchmaker

**Free, non-commercial use.** Every feature is enabled and there is no paid tier
— see [LICENSING.md](LICENSING.md) for why, and what to ask Sleeper for.

Every tool on the market grades a trade you already thought of. This one finds
the trade.

Enter a Sleeper username. It ingests every roster in the league, works out who
has surplus where and who has holes, and produces a ranked list of trades that
should happen — with the message to send.

No model is consulted to find, validate or rank a trade. All of that is
arithmetic: deterministic, reproducible, testable, and free to run. The only
place an LLM belongs is turning a finished proposal into pitch text, and because
the proposal is deterministic that output is cacheable.

---

## Setup

Python 3.11+. No third-party runtime dependencies — the whole thing runs on the
standard library.

```bash
git clone <this repo>
cd DynastyMatchmaker
python3 -m pytest tests/ -q          # 170 tests, no network required
```

Data is fetched on first use and cached to `~/.cache/dynasty-matchmaker`
(override with `DYNASTY_CACHE_DIR`). Values are cached 12h, the Sleeper player
dump 24h.

## Try it in one command

No account, no network, no setup:

```bash
python3 -m src.cli demo
```

That runs the whole pipeline on a sample league bundled with the repo — real
players and real DynastyProcess values, invented managers — and prints the
scored board, every team's window, and the ranked proposals with pitch text.

## Commands

```bash
python3 -m src.cli demo                         # end-to-end on the sample league
python3 -m src.cli leagues  <username>          # every league this season
python3 -m src.cli load     <league_id>         # normalized league + rosters
python3 -m src.cli load     <league_id> --roster 4
python3 -m src.cli doctor   <username>          # score every team, check coverage
python3 -m src.cli doctor   <username> -v       # + optimal lineups
python3 -m src.cli trades   <league_id>         # ranked proposals
python3 -m src.cli trades   <league_id> --roster 4 --limit 5
python3 -m src.cli trades   <league_id> --multi-team    # include 3-team rings
python3 -m src.cli history  <league_id>         # completed trades, all seasons
```

Global flags: `--offline` (cached data only), `--season YEAR`,
`--fixture PATH` (read Sleeper payloads from a file instead of the API),
`--values-dir PATH` (read values from local CSVs instead of DynastyProcess).

To drive the sample league through the normal commands rather than `demo`:

```bash
FX=tests/fixtures/league/payloads.json
VD=tests/fixtures/league/values
python3 -m src.cli --fixture $FX --values-dir $VD doctor liamsmithh
python3 -m src.cli --fixture $FX --values-dir $VD trades 1048291736450000000
```

## Layout

| Path | What it is |
| --- | --- |
| `src/values.py` | DynastyProcess adapter. The only file that touches a values CSV. |
| `src/sleeper.py` | Sleeper adapter. The only file that sees Sleeper JSON. |
| `src/scoring.py` | Roster scoring, windows, positional shape. |
| `src/matchmaker.py` | Trade generation, validation, ranking, pitch text. |
| `src/cli.py` | Thin shell over the above. |
| `docs/matchmaker-spec.md` | The algorithm in plain English. Read before the code. |
| `scripts/verify_stage*.py` | Stage verification, live where possible. |
| `scripts/make_fixture.py` | Rebuilds the fixture league from live data. |

Both adapters are swappable: nothing downstream of `sleeper.py` sees a Sleeper
payload, so an ESPN or Yahoo adapter slots in by producing the same `League`,
`Team` and `Pick` objects. Nothing outside `values.py` knows where a value came
from.

---

## Verification status

**Stage 1 was verified against live DynastyProcess data.** Herbert prices at
3,304 in 1QB and 7,700 in superflex (2.33x), Chase at 10,232/9,098, Bijan at
9,648/8,301. Pick values are monotone across all 60 exact picks with zero
violations, and the Sleeper crosswalk resolves 630 of 640 players.

**Stages 2–4 were not verified against your live league.** The sandbox this was
built in blocks `api.sleeper.app`, `docs.sleeper.com` and `sleeper.com` at the
egress proxy — 403 on CONNECT, an organization policy denial rather than a
transient failure, and not something to route around.

They were verified instead against a fixture league built to the documented
Sleeper schemas and populated with real players, real `sleeper_id`s and real
DynastyProcess values. That exercises every transform, but it cannot catch a
field that Sleeper names differently from the documentation.

To verify against your real league, run this anywhere Sleeper is reachable:

```bash
python3 scripts/verify_stage2.py liamsmithh    # tries live first, falls back
python3 -m src.cli doctor liamsmithh
```

`verify_stage2.py` attempts the live API and only falls back to the fixture if
it cannot connect, so no code change is needed. Two things to check on that
first live run:

- The format line should read `12-team 1QB Full PPR`. If any part is wrong, the
  detector needs a fix, not a workaround.
- Value coverage over QB/RB/WR/TE should clear 90%. It reads 100% on the fixture
  only because the fixture is built from the value book itself; a real league
  will be lower once kickers, defenses and deep bench players are in it.

---

## What's weakest in the current design

An honest list, worst first.

### 1. The commercial blocker is unresolved, and it is not a technical one

Sleeper's API is free for **non-commercial** use. A $9.99 product is not that.
No amount of engineering moves it, so the tool currently ships free with every
feature enabled and no billing code, which is the only honest position until
Sleeper answers. DynastyProcess's licence needs the same confirmation before its
numbers are resold.

`LICENSING.md` sets out what to ask for. Everything below is subordinate to it.

### 2. Value is one vendor's opinion, taken as ground truth

Every number traces back to a single DynastyProcess scrape. If their board is
wrong about a player, the tool is wrong in exactly the same direction, and it
will state that wrongness with total confidence in a generated message the user
sends to a friend. There is no second source, no uncertainty band, and no way
for the output to say "this one is contested."

Their file ships `ecr_high` and `ecr_low` columns that the pick loader currently
reads and discards. That is free disagreement data, sitting unused.

### 3. Surplus is measured against a market that includes the surplus

Replacement level is computed from the players rostered in the league, and
"surplus" is value above it. But a team hoarding six startable receivers is
itself pushing that replacement level up, which slightly suppresses the very
surplus it should be flagging. The effect is small in a 12-team league and gets
worse in a 10-team one. A cleaner formulation would compute replacement over the
league excluding the team being scored.

### 4. Window weights are hand-set numbers

The five window profiles in `WINDOW_WEIGHTS` are the one genuinely arbitrary
thing in the system. Everything else is derived from the league or the market;
these are my judgement about how a contender trades off lineup against capital.
They are plausible and internally consistent, and they are not measured. The
league's own trade history is right there and is the obvious calibration set.

### 5. Nothing knows whether these trades actually get accepted

The tool proposes what should happen. It has no idea what does. Acceptance is
the only metric that matters commercially and it currently goes unrecorded.

### 6. Smaller things

- Draft slots for the coming rookie draft are projected from current standings,
  so early-season pick values carry real error. Sleeper exposes the real order
  once the draft exists, which is used when available.
- Injuries, holdouts, depth charts and bye weeks are invisible. The values file
  absorbs some of this on a lag and none of it in-week.
- IDP and team-defense leagues parse correctly but those positions carry no
  value, so a deep IDP league will grade badly. The tool should say so rather
  than quietly scoring half a roster at zero.
- Multi-team search only looks for 3-cycles, and only over single-asset legs.
- `positional_shape` excludes IR, which is right for deployable value and wrong
  for asset value; an injured star is still very much a tradeable asset.

---

## What I would build next

**First: settle the Sleeper licence.** Nothing else is worth doing until it is.

**Then, in order:**

1. **Calibrate the window weights against real trades.** `load_trades()` already
   walks `previous_league_id` back through every prior season and returns
   completed trades. That is a labelled dataset of deals that real managers
   actually accepted. Score each historical trade under the current weights and
   fit the weights so that accepted trades score positively for both sides. This
   converts the most arbitrary part of the system into the most evidence-backed
   one, and it is the single highest-value thing in this list.

2. **Record acceptance and close the loop.** When a user sends a proposal, ask
   what happened. Accept rate per window pairing, per trade size, per manager is
   the product's only real quality metric, and it feeds directly back into (1).

3. **Use the uncertainty already in the data.** `ecr_high`/`ecr_low` give a
   disagreement band per player for free. Rank proposals partly on how robust
   they are across that band — a trade that clears at every plausible valuation
   is worth more than one that only clears at the midpoint. This also lets the
   pitch text say "this is close, here's why I think it works," which is far
   more persuasive than false precision.

4. **A second value source.** Even one more board turns a point estimate into a
   range and makes "contested" a first-class state.

5. **The commissioner surface.** The pricing model is one purchase covering
   twelve managers, so the product is really a league-wide artifact: a standing
   page showing every team's window and each manager's three best available
   trades, refreshed when the values file updates. That is a much better fit for
   $9.99/league/season than a CLI one person runs.

6. **Then the web app.** Not before. The CLI is the right shape for getting the
   arithmetic right, and the arithmetic is the product.

---

## Credits

Player and pick values from [DynastyProcess](https://github.com/dynastyprocess/data)
(Tan Ho, Joe Sydlowski). League data from the [Sleeper API](https://docs.sleeper.com).
Both must be credited in any user-facing surface, and both licences confirmed
before charging for this.
