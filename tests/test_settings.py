"""Settings parsing across formats. Nothing may be assumed from one league."""

from __future__ import annotations

import pytest

from src.sleeper import detect_settings, parse_roster_positions

STANDARD = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "FLEX"]


def make(positions, scoring=None, teams=12, settings=None):
    return {
        "total_rosters": teams,
        "roster_positions": positions,
        "scoring_settings": scoring or {},
        "settings": settings or {},
    }


class TestFormatLabel:
    @pytest.mark.parametrize(
        "positions, scoring, teams, expected",
        [
            (STANDARD, {"rec": 1.0}, 12, "12-team 1QB Full PPR"),
            (STANDARD, {"rec": 0.5}, 12, "12-team 1QB Half PPR"),
            (STANDARD, {"rec": 0.0}, 10, "10-team 1QB Non-PPR"),
            (STANDARD, {}, 10, "10-team 1QB Non-PPR"),
            (
                STANDARD + ["SUPER_FLEX"],
                {"rec": 1.0},
                12,
                "12-team Superflex Full PPR",
            ),
            (
                ["QB", "QB"] + STANDARD[1:],
                {"rec": 1.0},
                14,
                "14-team Superflex Full PPR",
            ),
            (
                STANDARD,
                {"rec": 1.0, "bonus_rec_te": 0.5},
                12,
                "12-team 1QB Full PPR TE+0.5",
            ),
            (STANDARD, {"rec": 0.25}, 8, "8-team 1QB 0.25 PPR"),
        ],
    )
    def test_label(self, positions, scoring, teams, expected):
        assert detect_settings(make(positions, scoring, teams)).format_label == expected


class TestSuperflex:
    def test_single_qb_is_not_superflex(self):
        assert detect_settings(make(STANDARD, {"rec": 1.0})).superflex is False

    def test_super_flex_slot(self):
        s = detect_settings(make(STANDARD + ["SUPER_FLEX"]))
        assert s.superflex is True
        assert s.qb_slots == pytest.approx(1.5)

    def test_two_dedicated_qb_slots_count_as_superflex(self):
        """A straight 2QB league prices QBs off value_2qb just like superflex."""
        s = detect_settings(make(["QB", "QB"] + STANDARD[1:]))
        assert s.superflex is True
        assert s.qb_slots == pytest.approx(2.0)

    def test_op_slot(self):
        assert detect_settings(make(STANDARD + ["OP"])).superflex is True

    def test_flex_without_qb_is_not_superflex(self):
        assert detect_settings(make(STANDARD + ["REC_FLEX"])).superflex is False


class TestTePremium:
    def test_absent(self):
        assert detect_settings(make(STANDARD, {"rec": 1.0})).te_premium == 0.0

    def test_present(self):
        s = detect_settings(make(STANDARD, {"rec": 1.0, "bonus_rec_te": 1.0}))
        assert s.te_premium == 1.0
        assert "TE+1" in s.format_label


class TestRosterPositions:
    def test_splits_bench_taxi_ir(self):
        starters, bench, taxi, ir = parse_roster_positions(
            STANDARD + ["BN"] * 5 + ["TAXI"] * 3 + ["IR"] * 2
        )
        assert len(starters) == 9
        assert (bench, taxi, ir) == (5, 3, 2)

    def test_taxi_from_settings_when_absent_from_positions(self):
        s = detect_settings(
            make(STANDARD + ["BN"] * 5, settings={"taxi_slots": 4, "reserve_slots": 2})
        )
        assert s.taxi_slots == 4
        assert s.ir_slots == 2

    def test_takes_larger_of_positions_and_settings(self):
        s = detect_settings(
            make(STANDARD + ["TAXI"] * 3, settings={"taxi_slots": 1})
        )
        assert s.taxi_slots == 3

    def test_flex_eligibility(self):
        starters, *_ = parse_roster_positions(["FLEX", "REC_FLEX", "WRRB_FLEX", "SUPER_FLEX"])
        eligible = {s.code: s.eligible for s in starters}
        assert eligible["FLEX"] == {"RB", "WR", "TE"}
        assert eligible["REC_FLEX"] == {"WR", "TE"}
        assert eligible["WRRB_FLEX"] == {"RB", "WR"}
        assert "QB" in eligible["SUPER_FLEX"]

    def test_team_count_falls_back_to_settings(self):
        cfg = {"roster_positions": STANDARD, "settings": {"num_teams": 14},
               "scoring_settings": {}}
        assert detect_settings(cfg).teams == 14

    def test_kickers_and_defense_are_starters_not_bench(self):
        starters, bench, _, _ = parse_roster_positions(STANDARD + ["K", "DEF", "BN"])
        assert len(starters) == 11
        assert bench == 1

    def test_empty_positions_do_not_crash(self):
        s = detect_settings(make([], {"rec": 1.0}, teams=12))
        assert s.starters_per_team == 0
        assert s.superflex is False
