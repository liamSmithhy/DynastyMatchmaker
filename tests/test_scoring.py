"""Lineup solving, window classification and positional shape."""

from __future__ import annotations

import pytest

from src.scoring import (
    CONTENDER,
    REBUILD,
    RETOOLER,
    STUCK,
    TOP_HEAVY,
    ValuedPlayer,
    classify_window,
    optimal_lineup,
    positional_demand,
    score_league,
)
from src.sleeper import RosterPlayer, RosterSlot, detect_settings

QB = RosterSlot("QB", frozenset({"QB"}))
RB = RosterSlot("RB", frozenset({"RB"}))
WR = RosterSlot("WR", frozenset({"WR"}))
FLEX = RosterSlot("FLEX", frozenset({"RB", "WR", "TE"}))
SUPER = RosterSlot("SUPER_FLEX", frozenset({"QB", "RB", "WR", "TE"}))
RECFLEX = RosterSlot("REC_FLEX", frozenset({"WR", "TE"}))
WRRB = RosterSlot("WRRB_FLEX", frozenset({"RB", "WR"}))


def player(name, position, value, age=26.0, slot="BENCH"):
    return ValuedPlayer(
        player=RosterPlayer(
            sleeper_id=name, name=name, position=position, team="FA", age=age, slot=slot
        ),
        value=value,
        matched_by="sleeper",
        record=None,
    )


class TestOptimalLineup:
    def test_picks_the_best_eligible(self):
        players = [player("a", "RB", 100), player("b", "RB", 50)]
        filled = optimal_lineup(players, [RB])
        assert filled[0][1].name == "a"

    def test_flex_takes_the_best_leftover(self):
        players = [player("rb1", "RB", 100), player("rb2", "RB", 90), player("wr1", "WR", 95)]
        filled = optimal_lineup(players, [RB, FLEX])
        assigned = {slot.code: p.name for slot, p in filled if p}
        assert assigned["RB"] == "rb1"
        assert assigned["FLEX"] == "wr1"

    def test_does_not_trust_the_managers_lineup(self):
        """Starter value is computed from the best available lineup, not the one
        that happens to be set."""
        players = [player("star", "RB", 900, slot="BENCH"), player("scrub", "RB", 10, slot="STARTER")]
        total = sum(p.value for _, p in optimal_lineup(players, [RB]) if p)
        assert total == 900

    def test_crossing_eligibility_sets_are_solved_exactly(self):
        """REC_FLEX (WR/TE) and WRRB_FLEX (RB/WR) are not nested, so a greedy
        most-restrictive-first fill can be beaten. The matroid solution cannot."""
        players = [player("wr", "WR", 100), player("te", "TE", 90), player("rb", "RB", 80)]
        total = sum(p.value for _, p in optimal_lineup(players, [RECFLEX, WRRB]) if p)
        assert total == 190  # te into REC_FLEX, wr into WRRB -- not wr then stuck

    def test_empty_slot_when_nothing_is_eligible(self):
        filled = optimal_lineup([player("qb", "QB", 100)], [RB])
        assert filled[0][1] is None

    def test_zero_value_players_are_not_started(self):
        filled = optimal_lineup([player("x", "RB", 0)], [RB])
        assert filled[0][1] is None

    def test_superflex_slot_can_take_a_quarterback(self):
        players = [player("qb", "QB", 500), player("rb", "RB", 100)]
        assigned = {
            slot.code: p.position for slot, p in optimal_lineup(players, [SUPER]) if p
        }
        assert assigned["SUPER_FLEX"] == "QB"


class TestWindows:
    def test_gap_beats_raw_rank(self):
        """Two teams holding identical total value land in opposite windows.
        This is the whole point of pillar 4."""
        mortgaged = classify_window(starter_rank=2, overall_rank=8, teams=12, capital_share=0.1)
        loaded = classify_window(starter_rank=10, overall_rank=8, teams=12, capital_share=0.1)
        assert mortgaged == TOP_HEAVY
        assert loaded == RETOOLER
        assert mortgaged != loaded

    def test_top_heavy(self):
        assert classify_window(2, 9, 12, 0.1) == TOP_HEAVY

    def test_retooler(self):
        assert classify_window(9, 3, 12, 0.2) == RETOOLER

    def test_contender_needs_both_strong(self):
        assert classify_window(2, 3, 12, 0.1) == CONTENDER

    def test_a_strong_team_with_a_big_gap_is_not_a_contender(self):
        assert classify_window(1, 6, 12, 0.1) == TOP_HEAVY

    def test_rebuild_requires_capital(self):
        assert classify_window(12, 11, 12, 0.30) == REBUILD
        assert classify_window(12, 11, 12, 0.05) == STUCK

    def test_middling_is_stuck(self):
        assert classify_window(6, 6, 12, 0.1) == STUCK

    def test_threshold_scales_with_league_size(self):
        # A 3-rank gap is decisive in a 10-team league and marginal in a 30-team one.
        assert classify_window(5, 8, 10, 0.1) == TOP_HEAVY
        assert classify_window(15, 18, 30, 0.1) == STUCK


class TestPositionalDemand:
    def test_dedicated_slots_are_counted(self):
        settings = detect_settings(
            {"total_rosters": 12, "roster_positions": ["QB", "RB", "RB", "WR"],
             "scoring_settings": {}, "settings": {}}
        )
        demand = positional_demand([], settings)
        assert demand["RB"] == 2
        assert demand["QB"] == 1

    def test_flex_share_is_measured_from_real_lineups(self):
        """Not assumed. If receivers win every flex, receivers carry the demand."""
        settings = detect_settings(
            {"total_rosters": 2, "roster_positions": ["WR", "FLEX"],
             "scoring_settings": {}, "settings": {}}
        )
        lineups = [
            [(WR, player("a", "WR", 10)), (FLEX, player("b", "WR", 9))],
            [(WR, player("c", "WR", 10)), (FLEX, player("d", "WR", 9))],
        ]
        demand = positional_demand(lineups, settings)
        assert demand["WR"] == pytest.approx(2.0)
        assert demand["RB"] == pytest.approx(0.0)

    def test_flex_share_splits_when_positions_share_it(self):
        settings = detect_settings(
            {"total_rosters": 2, "roster_positions": ["FLEX"],
             "scoring_settings": {}, "settings": {}}
        )
        lineups = [
            [(FLEX, player("a", "RB", 10))],
            [(FLEX, player("b", "WR", 9))],
        ]
        demand = positional_demand(lineups, settings)
        assert demand["RB"] == pytest.approx(0.5)
        assert demand["WR"] == pytest.approx(0.5)


class TestLeagueScoring:
    def test_every_team_scored(self, league, league_book):
        scored = score_league(league, league_book)
        assert len(scored.teams) == len(league.teams)

    def test_ranks_are_a_permutation(self, league, league_book):
        scored = score_league(league, league_book)
        n = len(scored.teams)
        assert sorted(t.overall_rank for t in scored.teams) == list(range(1, n + 1))
        assert sorted(t.starter_rank for t in scored.teams) == list(range(1, n + 1))

    def test_total_is_players_plus_picks(self, league, league_book):
        for team in score_league(league, league_book).teams:
            assert team.total_value == pytest.approx(team.player_value + team.pick_value)

    def test_starter_value_never_exceeds_player_value(self, league, league_book):
        for team in score_league(league, league_book).teams:
            assert team.starter_value <= team.player_value + 1e-6

    def test_draft_capital_is_material(self, league, league_book):
        """Pillar 3: never grade a roster without picks."""
        for team in score_league(league, league_book).teams:
            assert team.pick_value > 0

    def test_backup_quarterbacks_are_surplus_in_one_qb(self, league, league_book):
        """Pillar 1, arrived at by arithmetic rather than by a rule."""
        scored = score_league(league, league_book)
        assert any(t.surplus.get("QB", 0) > 0 for t in scored.teams)

    def test_superflex_deploys_more_quarterbacks(self, payloads, league_book, client):
        import copy

        from src.sleeper import DictTransport, SleeperClient

        sf = copy.deepcopy(payloads)
        positions = sf["league/1048291736450000000"]["roster_positions"]
        positions[positions.index("FLEX")] = "SUPER_FLEX"
        sf_league = SleeperClient(transport=DictTransport(sf)).load_league(
            "1048291736450000000"
        )

        def started_qbs(lg):
            sc = score_league(lg, league_book)
            return sum(
                1 for t in sc.teams for _, p in t.lineup if p and p.position == "QB"
            )

        assert started_qbs(sf_league) > started_qbs(client.load_league("1048291736450000000"))
