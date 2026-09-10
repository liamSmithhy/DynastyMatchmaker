"""Pick ownership (gotcha 3), labels by horizon (gotcha 2) and pick values
on the player scale (gotcha 1)."""

from __future__ import annotations

import pytest

from src.sleeper import build_pick_ownership, estimate_draft_slots, Team
from src.values import tier_for_slot

ROSTERS = [1, 2, 3, 4]
SEASONS = [2027, 2028]
ROUNDS = 3


def own(traded=()):
    return build_pick_ownership(ROSTERS, SEASONS, ROUNDS, list(traded))


class TestPickOwnership:
    def test_every_roster_seeded_when_nothing_traded(self):
        """The gotcha: traded_picks reports only picks that MOVED, so a league
        with no trades must still show every team holding full capital."""
        picks = own()
        for roster_id in ROSTERS:
            assert len(picks[roster_id]) == len(SEASONS) * ROUNDS
            assert all(p.is_own for p in picks[roster_id])

    def test_conservation(self):
        traded = [
            {"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2},
            {"season": "2028", "round": 3, "roster_id": 4, "owner_id": 1},
        ]
        picks = own(traded)
        total = sum(len(v) for v in picks.values())
        assert total == len(ROSTERS) * len(SEASONS) * ROUNDS

    def test_traded_pick_moves(self):
        picks = own([{"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2}])
        assert not any(p.season == 2027 and p.round == 1 for p in picks[1])
        moved = [p for p in picks[2] if p.season == 2027 and p.round == 1]
        assert len(moved) == 2  # roster 2's own, plus roster 1's
        assert {p.original_roster_id for p in moved} == {1, 2}
        assert any(not p.is_own for p in moved)

    def test_non_trader_keeps_everything(self):
        picks = own([{"season": "2027", "round": 1, "roster_id": 1, "owner_id": 2}])
        assert len(picks[3]) == len(SEASONS) * ROUNDS
        assert all(p.is_own for p in picks[3])

    def test_pick_traded_twice_lands_with_final_owner(self):
        picks = own(
            [
                {"season": "2027", "round": 2, "roster_id": 1,
                 "previous_owner_id": 1, "owner_id": 2},
                {"season": "2027", "round": 2, "roster_id": 1,
                 "previous_owner_id": 2, "owner_id": 3},
            ]
        )
        assert any(p.original_roster_id == 1 for p in picks[3] if p.season == 2027)
        assert not any(p.original_roster_id == 1 and p.season == 2027 and p.round == 2
                       for p in picks[2])

    def test_pick_outside_horizon_is_ignored(self):
        picks = own([{"season": "2031", "round": 1, "roster_id": 1, "owner_id": 2}])
        assert sum(len(v) for v in picks.values()) == len(ROSTERS) * len(SEASONS) * ROUNDS

    def test_malformed_rows_are_skipped(self):
        picks = own(
            [
                {"season": "not-a-year", "round": 1, "roster_id": 1, "owner_id": 2},
                {"round": 1, "owner_id": 2},
                {},
            ]
        )
        assert all(all(p.is_own for p in v) for v in picks.values())

    def test_pick_traded_to_a_roster_outside_the_league_is_dropped(self):
        picks = own([{"season": "2027", "round": 1, "roster_id": 1, "owner_id": 99}])
        assert not any(p.season == 2027 and p.round == 1 for p in picks[1])
        assert 99 not in picks


class TestDraftSlots:
    def test_worst_record_picks_first(self):
        teams = [
            Team(roster_id=1, owner_id="a", manager="a", team_name="A",
                 wins=10, losses=3, points_for=1500),
            Team(roster_id=2, owner_id="b", manager="b", team_name="B",
                 wins=2, losses=11, points_for=1000),
            Team(roster_id=3, owner_id="c", manager="c", team_name="C",
                 wins=6, losses=7, points_for=1200),
        ]
        slots = estimate_draft_slots(teams, 3)
        assert slots[2] == 1
        assert slots[1] == 3

    def test_points_break_record_ties(self):
        teams = [
            Team(roster_id=1, owner_id="a", manager="a", team_name="A",
                 wins=5, losses=5, points_for=1400),
            Team(roster_id=2, owner_id="b", manager="b", team_name="B",
                 wins=5, losses=5, points_for=1100),
        ]
        slots = estimate_draft_slots(teams, 2)
        assert slots[2] == 1


class TestTiers:
    @pytest.mark.parametrize(
        "slot, teams, expected",
        [
            (1, 12, "early"), (4, 12, "early"), (5, 12, "mid"),
            (8, 12, "mid"), (9, 12, "late"), (12, 12, "late"),
            (1, 10, "early"), (10, 10, "late"),
            (1, 14, "early"), (14, 14, "late"),
        ],
    )
    def test_tier_for_slot(self, slot, teams, expected):
        assert tier_for_slot(slot, teams) == expected


class TestPickLabels:
    def test_near_season_is_exact(self, book):
        assert book.pick_label(2026, 1, 3, teams=12) == "2026 Pick 1.03"

    def test_next_season_is_a_tier(self, book):
        assert book.pick_label(2027, 1, 3, teams=12) == "2027 Early 1st"
        assert book.pick_label(2027, 2, 11, teams=12) == "2027 Late 2nd"

    def test_far_season_is_round_only(self, book):
        assert book.pick_label(2028, 1, 3, teams=12) == "2028 1st"
        assert book.pick_label(2030, 3, 7, teams=12) == "2030 3rd"

    def test_unknown_slot_falls_back_to_round(self, book):
        assert book.pick_label(2027, 1, None, teams=12) == "2027 1st"


class TestPickValues:
    def test_picks_land_on_the_player_scale(self, book):
        """Gotcha 1: the file ships ECR, so a raw read would be a rank, not a
        value. A first-round pick must be comparable to a real player."""
        value = book.pick_value("2026 Pick 1.01")
        assert value > book.value("Mike Receiver")
        assert value < book.value("Alpha Receiver")

    def test_later_picks_are_never_worth_more(self, book):
        values = [
            book.pick_value(f"2026 Pick {r}.{s:02d}")
            for r in (1, 2, 3)
            for s in range(1, 13)
        ]
        assert values == sorted(values, reverse=True)

    def test_superflex_picks_are_worth_more(self, book):
        assert book.pick_value("2026 Pick 1.01", superflex=True) > book.pick_value(
            "2026 Pick 1.01"
        )

    def test_tiers_are_ordered(self, book):
        early = book.pick_value("2027 Early 1st")
        mid = book.pick_value("2027 Mid 1st")
        late = book.pick_value("2027 Late 1st")
        assert early > mid > late

    def test_rounds_are_ordered(self, book):
        assert book.pick_value("2028 1st") > book.pick_value("2028 2nd")
        assert book.pick_value("2028 2nd") > book.pick_value("2028 3rd")

    def test_distant_seasons_are_discounted(self, book):
        assert book.pick_value("2029 1st") < book.pick_value("2028 1st")
        assert book.pick_value("2030 1st") < book.pick_value("2029 1st")

    def test_non_twelve_team_slots_interpolate(self, book):
        """A 14-team league's 1.13 has no row in the file."""
        v13 = book.pick_value("2026 Pick 1.13", teams=14)
        assert book.pick_value("2026 Pick 1.14", teams=14) < v13
        assert v13 < book.pick_value("2026 Pick 1.12", teams=14)

    def test_team_count_changes_what_a_slot_is_worth(self, book):
        """2.01 is the 11th pick in a 10-team league and the 15th in a 14-team
        one, so the same label is a different asset. (Round 1 is the exception:
        slot N is the Nth pick at any league size.)"""
        assert book.pick_value("2026 Pick 2.01", teams=10) > book.pick_value(
            "2026 Pick 2.01", teams=14
        )
        assert book.pick_value("2026 Pick 1.10", teams=10) == pytest.approx(
            book.pick_value("2026 Pick 1.10", teams=14)
        )

    def test_round_deeper_than_the_file_still_prices(self, book):
        assert book.pick_value("2028 6th") > 0

    def test_unparseable_label_is_zero_not_a_crash(self, book):
        assert book.pick_value("next year's first") == 0.0
        assert book.pick("garbage") is None
