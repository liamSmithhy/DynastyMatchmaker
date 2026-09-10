# Matchmaker specification

The algorithm, in plain English, written before the code.

Everything here is arithmetic. No model is consulted to find, validate or rank a
trade. The only place a model appears in this product is turning a finished
proposal into pitch text, and that output is cacheable because the proposal it
describes is deterministic.

---

## 0. What a trade actually is

Most tools answer "is this trade fair?" — a question about a trade you already
imagined. Fairness is the wrong target anyway: a perfectly balanced trade
between two teams in the same situation is a trade neither manager has any
reason to make.

A trade happens when two managers value the same assets differently. That
difference is almost never about the players being mispriced. It is about the
two rosters being in different shapes: one team can deploy a player the other
cannot, or one team wants production now and the other wants it in 2028.

So the question this tool answers is: **given every roster in the league, which
pairs of managers are holding assets that are worth more to the other side than
to them, and what is the specific swap that captures it?**

That reframing is what makes the search tractable. We are not scoring all
possible trades. We are looking for value that is provably idle on one roster
and provably useful on another.

---

## 1. Inputs

From the league (all detected, never assumed):

- Team count, starting slots and flex eligibility, PPR level, TE premium,
  whether the league starts more than one quarterback.
- Every roster split into starters / bench / taxi / IR.
- Every draft pick, seeded to its original owner then moved by `traded_picks`.

From the value book:

- A market value per player in this league's format (`value_1qb` or `value_2qb`).
- Age per player.
- A value per pick, converted from pick ECR through the player ECR/value curve
  so picks and players sit on one scale.

Derived per league (see the scoring module):

- **Positional demand** — dedicated slots plus the share of flex slots each
  position actually wins, measured across every team's optimal lineup.
- **Replacement level** — the value of the first player at each position the
  league cannot start. This is the line between "an asset" and "a body".

---

## 2. Detecting surplus and deficit

### Surplus

A player is surplus to a team when:

1. He does not make that team's own optimal starting lineup, **and**
2. He is above the league's replacement level at his position.

Surplus value at a position is the sum of the market value of those players.

Condition 1 does the format work with no positional constants. In a 1QB league a
strong backup quarterback never reaches the lineup, so his entire value is
surplus — this is what "backup QBs are dead roster spots" means once it is
arithmetic rather than advice. In superflex the same player starts and his
surplus is zero. Nothing in the code names a position to get either result.

Condition 2 stops depth being mistaken for wealth. A team's ninth receiver is
not an asset just because he exists; ten mediocre receivers are not a surplus.

### Deficit

A team has a deficit where a starting slot is filled below replacement level —
including a slot left empty. It is measured in the same units as surplus (value
below the line), so both sides of a proposal compare directly.

Deficit is a *diagnostic*, not the trade trigger. The trigger is stronger and
comes in §4: does this specific player actually improve that specific lineup?
A team can have no formal deficit anywhere and still be meaningfully upgraded by
a better starter, and that upgrade is a real reason to trade.

### Why format weighting is not a table of coefficients

There is no dictionary of positional multipliers anywhere in this system. Format
sensitivity enters through three doors, all of them measured:

| Signal | Where it comes from |
| --- | --- |
| QB scarcity | `value_2qb` vs `value_1qb`, and whether a QB makes the lineup |
| Flex composition | Which positions actually win flex slots under this scoring |
| Positional scarcity | Replacement level computed from this league's rosters |

Change the league to superflex and every one of those moves on its own.

---

## 3. Windows

Each team is classified from the gap between two ranks: where its **starting
lineup** ranks in the league, and where its **total value** (players plus picks)
ranks. Classify on the gap, never the raw number.

| Condition | Window | What it wants |
| --- | --- | --- |
| lineup rank much better than total rank | TOP-HEAVY | assets and youth; it is mortgaged |
| total rank much better than lineup rank | RETOOLER | consolidate depth into starters |
| both in the top third | CONTENDER | starters now, will pay with future picks |
| both in the bottom third, with capital | REBUILD | youth and picks, will sell production |
| anything else | STUCK | pick a direction |

"Much better" is `max(2, teams/5)` rank positions, so it scales with league size.

The window is what makes a proposal *acceptable* rather than merely balanced. It
is the reason a contender and a rebuilder can both gain from the same trade
while a value-only tool would score it as a wash.

---

## 4. Building a proposal

For each ordered pair of teams (A, B):

1. **Find the candidate outbound assets.** From each side: everything surplus
   to it, plus any starter already past his positional peak. Picks are
   candidates too, weighted by window — a rebuilder's picks are not for sale, a
   contender's are.

   Offering a past-peak starter is not the same as recommending he be moved.
   Whether a team will actually part with him is settled in §5, where a
   contender's lineup weight makes selling a productive starter score negative
   and a rebuilder's does not. Applying the window twice — once as a filter here
   and again as a weight there — was a real bug: it locked every team the
   classifier called STUCK out of selling at all, so the oldest and worst roster
   in a league was never offered a trade, despite shipping its thirty-year-olds
   being the only move it has.

2. **Test each candidate against the other lineup.** For an asset `x` leaving A
   for B, compute B's optimal lineup value with `x` added. The **lineup gain**
   is the increase. This is the marginal-value test, and it is where the real
   trade is found: A's sixth receiver is worth whatever the market says, but he
   is worth a *starting slot* to B, and only to some Bs.

3. **Pair them up.** Take the assets with real lineup gain on the other side and
   assemble packages, smallest first: 1-for-1, then 2-for-1 and 1-for-2, then
   2-for-2. Small trades get proposed before large ones because they actually
   get accepted.

4. **Balance the value.** The two packages must be within a tolerance of each
   other on market value. The tolerance is not symmetric — see §5.

Consolidation (2-for-1) is generated deliberately, not incidentally: it is the
specific thing a RETOOLER needs and the specific thing a REBUILD team is happy
to supply.

---

## 5. Validating that both sides say yes

A proposal is only real if **each side improves relative to its own window**.
This is the test that separates this tool from a trade grader.

Each side's gain is scored on four terms:

1. **Lineup gain** — the increase in that team's optimal starting lineup value.
2. **Market delta** — value received minus value sent, at market.
3. **Youth delta** — change in value-weighted age of the assets exchanged.
4. **Capital delta** — change in draft capital.

Each window weights those four differently:

| Window | Lineup | Market | Youth | Capital |
| --- | --- | --- | --- | --- |
| CONTENDER | high | low | negative | negative |
| TOP-HEAVY | low | high | high | high |
| RETOOLER | high | medium | low | low |
| REBUILD | none | high | high | high |
| STUCK | medium | high | low | low |

Read the CONTENDER row as: a contender will pay above market, and will give up
youth and picks, to raise its starting lineup. Read the REBUILD row as the
mirror: a rebuilding team does not care that its lineup got worse and wants
value, youth and picks. Those two rows are why a contender/rebuilder trade
clears when a fairness grader would call it lopsided.

A proposal is **valid** when:

- Both sides score a positive weighted gain.
- Neither side's market delta is worse than its window's tolerance
  (a contender may overpay; a rebuilder may not be fleeced).
- Neither side gives up a player its own lineup depends on — outbound assets
  must be genuinely surplus, tested by re-solving the sending team's lineup and
  requiring its loss to be small relative to its gain.
- No side receives a player it cannot use *and* loses value doing it.

The last two conditions are what stop the generator producing technically
balanced nonsense.

### The asymmetry that makes trades exist

The same asset is scored differently by each side, because each side's weights
differ. A 28-year-old RB2 is worth more to a contender than to a rebuilder even
at identical market value. A 2028 first is worth more to a rebuilder. The
algorithm never needs to be told this: it falls out of the window weights
applied to the same four deltas.

---

## 6. Ranking

Valid proposals are ranked by a single score:

```
score = joint_gain × mutuality × simplicity × freshness
```

- **joint_gain** — the sum of both sides' weighted gains. How much total value
  the trade unlocks.
- **mutuality** — `min(gain_a, gain_b) / max(gain_a, gain_b)`, in [0, 1]. A trade
  that is great for one side and marginal for the other will not be accepted, no
  matter how much value it unlocks in total. This term is what stops the list
  filling with proposals that are technically valid and socially dead.
- **simplicity** — a penalty on the number of assets moved. A 1-for-1 that
  captures 80% of the value of a 3-for-3 is the better proposal because it will
  actually get sent.
- **freshness** — a penalty on repeating what the list already contains.

### Freshness, in three parts

Selection is greedy over the freshness-adjusted score, so the raw scores in the
final list are deliberately *not* monotone: a slightly weaker proposal that
introduces new teams and new assets outranks a stronger variation on the deal
above it.

| Repetition | Penalty | Why |
| --- | --- | --- |
| same two teams trading again | heaviest | this is the definition of "a variation on one idea" |
| an asset already spoken for | heavy | you cannot trade the same receiver twice |
| a team that has already appeared | light | spreads suggestions across the league |

Without these, the single largest surplus in the league wins every slot. The
list reads as twelve versions of one deal, one manager gets twelve suggestions,
and the other eleven get none — which for a product the commissioner buys on
behalf of twelve managers is a straightforward failure.

---

## 7. Multi-team deals (paid tier)

Two-team matching fails when A has what B wants, B has what C wants, and C has
what A wants — a value cycle with no bilateral edge.

The generator builds a directed graph over teams where an edge A→B means "A holds
an asset with real lineup gain for B", then finds 3-cycles. Each leg is validated
with the same rules as a two-team deal, and every team in the ring must clear its
own window test. Cycles are strictly harder to get accepted, so they rank below
equivalent two-team deals and are only surfaced on the paid tier.

---

## 8. Determinism

Given the same league snapshot and the same values file, the output is
byte-identical. Iteration order is sorted everywhere, no clock or RNG is read,
and ties break on stable keys (roster id, then player id).

This matters commercially, not just aesthetically. Deterministic output can be
cached per league per values-scrape, which is what keeps the marginal cost of a
proposal at zero and the pitch-text model call at one-per-proposal-ever.

---

## 9. What this deliberately does not model

Stated plainly so nobody mistakes silence for coverage.

- **Manager psychology.** Some managers never trade, some overvalue their own
  players, some will not trade within their division. The tool proposes what
  *should* happen, which is not the same as what will.
- **Team context.** Depth charts, injuries, holdouts, coaching changes. The
  values file absorbs some of this on a lag, and none of it in-week.
- **Schedule and playoff odds.** A contender two games out is treated the same as
  one in first place. Record is read but only feeds projected draft slot.
- **Positional runs and league psychology.** If three managers just traded for
  running backs, the fourth will pay more. Not modelled.
- **Contending on defense.** IDP and team-defense leagues are parsed correctly
  but those positions carry no value, so a deep IDP league will grade badly.
