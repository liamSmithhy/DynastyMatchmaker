"""Trade generation: two-sided acceptance, determinism, and the window logic."""

from __future__ import annotations

import pytest

from src.matchmaker import (
    WINDOW_WEIGHTS,
    Asset,
    Side,
    player_asset,
    _cancel,
    find_trades,
    outbound_candidates,
    pair_proposals,
    pitch,
    rank,
    score_side,
    side_accepts,
)
from src.scoring import (
    CONTENDER,
    PEAK_AGE,
    TeamScore,
    optimal_lineup,
    REBUILD,
    RETOOLER,
    STUCK,
    TOP_HEAVY,
    ValuedPlayer,
    score_league,
)
from src.sleeper import RosterPlayer, RosterSlot, Team

RB = RosterSlot("RB", frozenset({"RB"}))


def asset(name, value, position="WR", age=26.0, is_pick=False):
    """A bare asset, for tests that only care about labels and values."""
    return Asset(
        key=f"x:{name}", label=name, value=value, position=position,
        age=age, is_pick=is_pick,
    )


def real_asset(name, value, position="WR", age=26.0):
    """An asset backed by an actual player, so the lineup solver can see it.

    A bare Asset carries no ValuedPlayer, so it never reaches a lineup -- which
    makes any test of deployment against one pass for the wrong reason.
    """
    return player_asset(
        ValuedPlayer(
            player=RosterPlayer(name, name, position, "FA", age, "BENCH"),
            value=value,
            matched_by="sleeper",
            record=None,
        )
    )


class TestCancellation:
    def test_identical_labels_cancel(self):
        a, b = _cancel([asset("2028 1st", 1500, is_pick=True)],
                       [asset("2028 1st", 1500, is_pick=True)])
        assert a == [] and b == []

    def test_only_matching_pairs_cancel(self):
        a, b = _cancel(
            [asset("2028 1st", 1500, is_pick=True), asset("Player", 900)],
            [asset("2028 1st", 1500, is_pick=True)],
        )
        assert [x.label for x in a] == ["Player"]
        assert b == []

    def test_unrelated_packages_survive(self):
        a, b = _cancel([asset("A", 100)], [asset("B", 100)])
        assert len(a) == 1 and len(b) == 1


class TestWindowWeights:
    def test_every_window_has_weights(self):
        for window in (CONTENDER, TOP_HEAVY, RETOOLER, STUCK, REBUILD):
            assert set(WINDOW_WEIGHTS[window]) == {
                "lineup", "market", "youth", "capital", "tolerance"
            }

    def test_contenders_value_the_lineup_most(self):
        assert WINDOW_WEIGHTS[CONTENDER]["lineup"] == max(
            w["lineup"] for w in WINDOW_WEIGHTS.values()
        )

    def test_rebuilders_do_not_value_the_lineup(self):
        assert WINDOW_WEIGHTS[REBUILD]["lineup"] < 0.1

    def test_contenders_will_overpay_and_rebuilders_will_not(self):
        assert WINDOW_WEIGHTS[CONTENDER]["tolerance"] > WINDOW_WEIGHTS[REBUILD]["tolerance"]

    def test_contenders_spend_capital_and_rebuilders_collect_it(self):
        assert WINDOW_WEIGHTS[CONTENDER]["capital"] < 0
        assert WINDOW_WEIGHTS[REBUILD]["capital"] > 0


class TestAcceptance:
    def _side(self, team, sends, receives):
        return Side(team=team, sends=sends, receives=receives)

    def test_a_side_that_loses_value_and_gains_nothing_refuses(self, scored):
        team = scored.teams[0]
        side = self._side(team, [asset("Good", 5000)], [asset("Bad", 100)])
        weights = WINDOW_WEIGHTS[team.window]
        score_side(side, scored.settings.starter_slots, weights)
        assert not side_accepts(side, weights)

    def test_every_generated_proposal_is_accepted_by_both_sides(self, scored, league_book):
        proposals = find_trades(scored, league_book, limit=25, multi_team=True)
        assert proposals
        for proposal in proposals:
            for side in proposal.sides:
                assert side.gain > 0

    def test_no_side_is_gutted(self, scored, league_book):
        """A proposal may never strip a team's lineup to balance a spreadsheet."""
        for proposal in find_trades(scored, league_book, limit=25):
            for side in proposal.sides:
                sent = sum(a.value for a in side.sends)
                assert side.lineup_gain >= -0.35 * max(sent, 1.0)

    def test_market_delta_respects_each_windows_tolerance(self, scored, league_book):
        for proposal in find_trades(scored, league_book, limit=25):
            for side in proposal.sides:
                sent = sum(a.value for a in side.sends)
                tolerance = WINDOW_WEIGHTS[side.window]["tolerance"]
                assert side.market_delta >= -tolerance * sent - 1e-6


class TestOutboundCandidates:
    """These construct situations by mutating a team, so they take a private
    copy -- mutating the shared league silently changes every later test."""

    def test_starters_in_their_prime_are_never_for_sale(self, scratch_scored):
        """A starter who has not peaked yet is not a trade candidate for anyone,
        whatever window his team is in."""
        for team in scratch_scored.teams:
            keys = {a.key for a in outbound_candidates(team, scratch_scored.replacement)}
            for slot, vp in team.lineup:
                if vp is None or vp.age is None:
                    continue
                peak = PEAK_AGE.get(vp.position, 27.0)
                if vp.age < peak:
                    assert f"p:{vp.player.sleeper_id}" not in keys, vp.name

    def _add(self, scored, team, key, age, position="RB", value=6000.0):
        """Add a player and re-solve the team, exactly as scoring would.

        Appending to `lineup` without recomputing `starter_value` leaves the
        team's baseline describing a roster it no longer has, which makes every
        later lineup_gain wrong by the value of whatever was added.
        """
        vp = ValuedPlayer(
            player=RosterPlayer(key, key, position, "FA", age, "STARTER"),
            value=value,
            matched_by="sleeper",
            record=None,
        )
        team.roster.append(vp)
        team.lineup = optimal_lineup(team.roster, scored.settings.starter_slots)
        team.starter_value = sum(p.value for _, p in team.lineup if p is not None)
        return vp

    def test_a_past_peak_starter_is_offered_in_every_window(self, scratch_scored):
        """Availability is not window-gated. Gating it here as well as in the
        acceptance weights locked the oldest, worst roster in a league out of
        trading at all -- the one team whose only move is to sell veterans."""
        team = next(iter(scratch_scored.teams))
        self._add(scratch_scored, team, "old", 31.0)
        for window in (CONTENDER, TOP_HEAVY, RETOOLER, STUCK, REBUILD):
            team.window = window
            keys = {a.key for a in outbound_candidates(team, scratch_scored.replacement)}
            assert "p:old" in keys, window

    def test_the_window_decides_acceptance_not_availability(self):
        """Selling a past-peak starter for future capital: a rebuilder takes it
        and a contender refuses, on identical numbers.

        Built on a synthetic roster rather than a fixture team so the lineup
        effect is controlled: the seller has capable backups, so losing the
        starter costs little and the trade turns purely on how each window
        prices capital and youth -- which is the thing under test.
        """
        slots = [RB, RB, RosterSlot("FLEX", frozenset({"RB", "WR", "TE"}))]
        roster = [
            self._vp("old", 31.0, 6000.0),
            self._vp("backup1", 24.0, 5800.0),
            self._vp("backup2", 24.0, 5600.0),
            self._vp("backup3", 24.0, 5400.0),
        ]
        team = self._synthetic(roster, slots)
        assert any(p is roster[0] for _, p in team.lineup), "seller must be starting"

        gains, verdicts = {}, {}
        for window in (CONTENDER, REBUILD):
            team.window = window
            side = Side(
                team=team,
                sends=[player_asset(roster[0])],
                receives=[asset("2028 1st", 6000, position="PICK", is_pick=True)],
            )
            weights = WINDOW_WEIGHTS[window]
            score_side(side, slots, weights)
            gains[window] = side.gain
            verdicts[window] = side_accepts(side, weights)

        assert gains[REBUILD] > gains[CONTENDER]
        assert verdicts[REBUILD] is True
        assert verdicts[CONTENDER] is False

    @staticmethod
    def _vp(key, age, value, position="RB"):
        return ValuedPlayer(
            player=RosterPlayer(key, key, position, "FA", age, "STARTER"),
            value=value,
            matched_by="sleeper",
            record=None,
        )

    @staticmethod
    def _synthetic(roster, slots):
        team = Team(roster_id=99, owner_id="u", manager="m", team_name="Synthetic")
        score = TeamScore(
            team=team,
            starters=[],
            lineup=optimal_lineup(roster, slots),
            roster=roster,
            picks=[],
        )
        score.starters = [p for _, p in score.lineup if p is not None]
        score.starter_value = sum(p.value for p in score.starters)
        score.window = CONTENDER
        return score

    def test_a_young_starter_is_not_sold_even_by_a_rebuilder(self, scratch_scored):
        """Age is the trigger, not the window alone -- a rebuilder holds its kids."""
        team = next(t for t in scratch_scored.teams if t.window == CONTENDER)
        self._add(scratch_scored, team, "kid", 22.0)
        team.window = REBUILD
        assert "p:kid" not in {
            a.key for a in outbound_candidates(team, scratch_scored.replacement)
        }

    def test_peak_age_is_per_position(self, scratch_scored):
        """A 26-year-old back is on the way down; a 26-year-old QB has not
        started yet."""
        team = next(t for t in scratch_scored.teams if t.window == CONTENDER)
        team.window = REBUILD
        back = self._add(scratch_scored, team, "rb26", 26.0)
        back.player.__dict__["position"] = "RB"
        keys = {a.key for a in outbound_candidates(team, scratch_scored.replacement)}
        assert "p:rb26" in keys

    def test_rebuilders_do_not_sell_picks(self, scratch_scored):
        team = next(iter(scratch_scored.teams))
        team.window = REBUILD
        assert not any(
            a.is_pick for a in outbound_candidates(team, scratch_scored.replacement)
        )

    def test_contenders_do_sell_picks(self, scratch_scored):
        team = next(t for t in scratch_scored.teams if t.picks)
        team.window = CONTENDER
        assert any(
            a.is_pick for a in outbound_candidates(team, scratch_scored.replacement)
        )


class TestSurplusMeetsNeed:
    def test_a_wr_surplus_team_trades_receivers_into_an_rb_surplus_team(
        self, scored
    ):
        """The Stage 4 requirement: this must fall out of the arithmetic with no
        position named anywhere in the matching path."""
        wr_rich = sorted(
            (t for t in scored.teams if t.surplus.get("WR", 0) > 0),
            key=lambda t: -t.surplus["WR"],
        )
        rb_rich = sorted(
            (t for t in scored.teams if t.surplus.get("RB", 0) > 0),
            key=lambda t: -t.surplus["RB"],
        )
        assert wr_rich and rb_rich

        for a in wr_rich:
            for b in rb_rich:
                if a.roster_id == b.roster_id:
                    continue
                for proposal in pair_proposals(
                    a, b, scored.settings.starter_slots, scored.replacement
                ):
                    sends_a = {x.position for x in proposal.sides[0].sends}
                    sends_b = {x.position for x in proposal.sides[1].sends}
                    if "WR" in sends_a and "RB" in sends_b:
                        return
        pytest.fail("no WR-for-RB trade emerged between complementary rosters")

    def test_no_position_is_named_in_the_matching_path(self):
        """Format sensitivity must be derived. A literal position in the trade
        logic would mean a league type was hardcoded."""
        import re

        with open("src/matchmaker.py", encoding="utf-8") as handle:
            source = handle.read()
        body = source.split("# --------", 1)[-1]
        code = "\n".join(
            line for line in body.splitlines() if not line.strip().startswith("#")
        )
        code = re.sub(r'"""(?:.|\n)*?"""', "", code)
        for position in ("'QB'", '"QB"', "'RB'", '"RB"', "'WR'", '"WR"', "'TE'", '"TE"'):
            assert position not in code, f"{position} hardcoded in matchmaker"


class TestRankingAndDeterminism:
    def test_output_is_reproducible(self, scored, league_book):
        def signature():
            return [
                (round(p.score, 6), tuple(sorted(p.assets)))
                for p in find_trades(scored, league_book, limit=10, multi_team=True)
            ]

        assert signature() == signature() == signature()

    def test_best_proposal_comes_first(self, scored, league_book):
        """Selection order is by freshness-ADJUSTED score, so the raw scores are
        deliberately not monotone -- a slightly weaker proposal that reuses no
        assets outranks a stronger variation on the deal above it. What must
        hold is that nothing outscores the pick at the top, where no assets are
        spoken for yet."""
        proposals = find_trades(scored, league_book, limit=10)
        assert proposals
        assert proposals[0].score == max(p.score for p in proposals)

    def test_limit_is_respected(self, scored, league_book):
        assert len(find_trades(scored, league_book, limit=3)) <= 3

    def test_roster_filter(self, scored, league_book):
        target = scored.teams[1].roster_id
        for proposal in find_trades(scored, league_book, roster_id=target, limit=10):
            assert any(s.team.roster_id == target for s in proposal.sides)

    def test_freshness_penalises_reusing_the_same_asset(self, scored, league_book):
        """Twelve variations on trading one receiver is one idea, not twelve."""
        proposals = find_trades(scored, league_book, limit=8)
        first_assets = proposals[0].assets
        overlaps = sum(1 for p in proposals[1:] if p.assets & first_assets)
        assert overlaps < len(proposals) - 1

    def test_simpler_trades_are_preferred_at_equal_value(self):
        from src.matchmaker import Proposal, _finalize

        class FakeTeam:
            def __init__(self, window, roster_id):
                self.window = window
                self.roster_id = roster_id
                self.name = f"T{roster_id}"
                self.starter_value = 0.0
                self.roster = []

        def make(n):
            a = Side(FakeTeam(CONTENDER, 1), [asset(f"a{i}", 100) for i in range(n)], [])
            b = Side(FakeTeam(REBUILD, 2), [asset(f"b{i}", 100) for i in range(n)], [])
            a.gain = b.gain = 500.0
            proposal = Proposal(sides=[a, b])
            _finalize(proposal)
            return proposal

        assert make(1).score > make(2).score


class TestDeadWeight:
    def test_paying_a_premium_for_a_player_you_cannot_start_is_refused(self, scratch_scored):
        """Spec section 5: a side may take on an undeployable player as a trade
        chip, but not while also losing the value exchange."""
        team = next(iter(scratch_scored.teams))
        weights = WINDOW_WEIGHTS[team.window]
        side = Side(
            team=team,
            sends=[real_asset("Real Starter", 5000, position="RB")],
            receives=[real_asset("Cannot Start", 4000, position="XX")],
        )
        score_side(side, scratch_scored.settings.starter_slots, weights)
        assert side.deployed_in == 0
        assert side.market_delta < 0
        assert not side_accepts(side, weights)

    def test_an_undeployable_player_is_acceptable_when_winning_on_value(
        self, scratch_scored
    ):
        team = next(iter(scratch_scored.teams))
        weights = WINDOW_WEIGHTS[team.window]
        side = Side(
            team=team,
            sends=[real_asset("Chip", 100, position="RB")],
            receives=[real_asset("Cannot Start", 4000, position="XX")],
        )
        score_side(side, scratch_scored.settings.starter_slots, weights)
        assert side.deployed_in == 0
        assert side.market_delta > 0
        assert side_accepts(side, weights)

    def test_deployed_in_counts_arrivals_that_reach_the_lineup(self, scratch_scored):
        team = next(iter(scratch_scored.teams))
        weights = WINDOW_WEIGHTS[team.window]
        side = Side(
            team=team, sends=[], receives=[real_asset("Superstar", 99999, position="WR")]
        )
        score_side(side, scratch_scored.settings.starter_slots, weights)
        assert side.deployed_in == 1


class TestPitch:
    def test_pitch_names_both_packages(self, scored, league_book):
        proposal = find_trades(scored, league_book, limit=1)[0]
        text = pitch(proposal)
        for a in proposal.sides[0].sends:
            assert a.label in text
        for a in proposal.sides[0].receives:
            assert a.label in text

    def test_pitch_is_deterministic(self, scored, league_book):
        proposal = find_trades(scored, league_book, limit=1)[0]
        assert pitch(proposal) == pitch(proposal)

    def test_pitch_speaks_to_the_recipients_window(self, scored, league_book):
        for proposal in find_trades(scored, league_book, limit=10):
            text = pitch(proposal)
            assert text.startswith("Hey —")
            assert "I'd send you:" in text

    def test_opener_describes_the_sender_not_the_recipient(self, scored, league_book):
        """The message is written in the first person and signed by the sender,
        so the opener has to be the sender's situation. Keying it off the
        recipient had a contender opening with a retooler's problem."""
        openers = {
            CONTENDER: "going for it",
            TOP_HEAVY: "get younger without",
            RETOOLER: "depth I can't start",
            REBUILD: "building for a couple",
            STUCK: "pick a direction",
        }
        for proposal in find_trades(scored, league_book, limit=10, multi_team=True):
            for i, side in enumerate(proposal.sides):
                text = pitch(proposal, from_side=i)
                assert openers[side.window] in text
