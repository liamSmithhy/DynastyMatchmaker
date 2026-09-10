"""Roster array splitting (gotcha 5).

Sleeper's players/starters/taxi/reserve arrays overlap and are padded with "0",
so bench is a set difference and never a slice.
"""

from __future__ import annotations

from src.sleeper import split_roster


class TestSplitRoster:
    def test_bench_is_a_set_difference(self):
        groups = split_roster(
            {
                "players": ["a", "b", "c", "d", "e", "f"],
                "starters": ["a", "b"],
                "taxi": ["e"],
                "reserve": ["f"],
            }
        )
        assert groups["starters"] == ["a", "b"]
        assert groups["bench"] == ["c", "d"]
        assert groups["taxi"] == ["e"]
        assert groups["ir"] == ["f"]

    def test_slicing_would_have_been_wrong(self):
        """The starters array is not a prefix of players -- taking players[n:]
        as the bench silently mixes starters into it."""
        roster = {
            "players": ["a", "b", "c", "d"],
            "starters": ["d", "b"],
            "taxi": [],
            "reserve": [],
        }
        groups = split_roster(roster)
        assert groups["bench"] == ["a", "c"]
        assert roster["players"][2:] != groups["bench"]

    def test_placeholders_are_stripped(self):
        groups = split_roster(
            {
                "players": ["a", "0", "b"],
                "starters": ["a", "0", "0"],
                "taxi": ["0"],
                "reserve": [],
            }
        )
        assert "0" not in groups["starters"]
        assert "0" not in groups["bench"]
        assert groups["taxi"] == []
        assert groups["bench"] == ["b"]

    def test_groups_are_disjoint(self):
        groups = split_roster(
            {
                "players": ["a", "b", "c", "d", "e"],
                "starters": ["a", "b"],
                "taxi": ["c"],
                "reserve": ["d"],
            }
        )
        seen = [set(groups[k]) for k in ("starters", "bench", "taxi", "ir")]
        for i in range(len(seen)):
            for j in range(i + 1, len(seen)):
                assert not seen[i] & seen[j]

    def test_taxi_and_ir_are_not_left_on_the_bench(self):
        """They appear in `players` too, so a naive difference double-counts."""
        groups = split_roster(
            {
                "players": ["a", "b", "c"],
                "starters": ["a"],
                "taxi": ["b"],
                "reserve": ["c"],
            }
        )
        assert groups["bench"] == []

    def test_player_missing_from_players_is_not_dropped(self):
        groups = split_roster(
            {"players": ["a"], "starters": ["a", "z"], "taxi": [], "reserve": []}
        )
        assert "z" in groups["starters"]

    def test_empty_roster(self):
        groups = split_roster({})
        assert groups == {"starters": [], "bench": [], "taxi": [], "ir": []}

    def test_missing_keys_do_not_crash(self):
        groups = split_roster({"players": ["a", "b"]})
        assert groups["bench"] == ["a", "b"]

    def test_numeric_ids_are_normalized_to_strings(self):
        groups = split_roster({"players": [11, 12], "starters": [11]})
        assert groups["starters"] == ["11"]
        assert groups["bench"] == ["12"]


class TestLeagueRosters:
    def test_fixture_league_splits_cleanly(self, league):
        for team in league.teams:
            ids = [
                {p.sleeper_id for p in group}
                for group in (team.starters, team.bench, team.taxi, team.ir)
            ]
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    assert not ids[i] & ids[j], f"overlap on roster {team.roster_id}"
            assert all(p.sleeper_id != "0" for p in team.all_players)

    def test_rostered_excludes_ir_but_keeps_taxi(self, league):
        team = next(t for t in league.teams if t.ir and t.taxi)
        rostered = {p.sleeper_id for p in team.rostered}
        assert not rostered & {p.sleeper_id for p in team.ir}
        assert rostered >= {p.sleeper_id for p in team.taxi}
