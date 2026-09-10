"""The shareable HTML report. Same renderer the demo and a real league use."""

from __future__ import annotations

import json
import re

import pytest

from src.report import DATA_PLACEHOLDER, build_report_data, render, write_report

DATA_RE = re.compile(
    r'<script id="data" type="application/json">(.*?)</script>', re.S
)


@pytest.fixture(scope="module")
def data(scored, league_book):
    return build_report_data(scored, league_book, focus_roster=1, source="sample")


class TestData:
    def test_top_level_shape(self, data):
        assert set(data) >= {
            "meta", "league", "demand", "replacement", "coverage",
            "teams", "proposals", "picks",
        }

    def test_every_team_present(self, data, scored):
        assert len(data["teams"]) == len(scored.teams)

    def test_teams_are_ranked(self, data):
        ranks = [t["ovr"] for t in data["teams"]]
        assert ranks == sorted(ranks)

    def test_each_team_carries_its_full_roster(self, data):
        """The whole roster, not just the totals -- lineup, bench, taxi, IR
        and picks, which is what makes the page worth sharing."""
        for team in data["teams"]:
            assert team["lineup"], team["name"]
            for key in ("bench", "taxi", "ir", "pickList"):
                assert isinstance(team[key], list)
            assert team["pickList"], team["name"]

    def test_lineup_has_one_entry_per_starting_slot(self, data, scored):
        slots = len(scored.settings.starter_slots)
        for team in data["teams"]:
            assert len(team["lineup"]) == slots

    def test_lineup_and_bench_do_not_overlap(self, data):
        for team in data["teams"]:
            starting = {p["player"]["name"] for p in team["lineup"] if p["player"]}
            for key in ("bench", "taxi", "ir"):
                assert not starting & {p["name"] for p in team[key]}, team["name"]

    def test_focus_roster_recorded(self, data):
        assert data["meta"]["focus"] == 1

    def test_one_pitch_per_side(self, data):
        for proposal in data["proposals"]:
            assert len(proposal["pitches"]) == len(proposal["sides"])
            assert all(p.strip() for p in proposal["pitches"])

    def test_each_pitch_is_written_from_that_side(self, data):
        """Side 0's message offers side 0's players."""
        for proposal in data["proposals"]:
            for i, side in enumerate(proposal["sides"]):
                text = proposal["pitches"][i]
                for asset in side["sends"]:
                    assert asset["label"] in text

    def test_is_json_serialisable(self, data):
        json.dumps(data)

    def test_sample_source_is_marked(self, data):
        assert data["meta"]["source"] == "sample"


class TestRender:
    def test_placeholder_is_replaced(self, data):
        assert DATA_PLACEHOLDER not in render(data)

    def test_data_round_trips_through_the_page(self, data):
        html = render(data)
        embedded = json.loads(DATA_RE.search(html).group(1))
        assert embedded == data

    def test_page_is_self_contained(self, data):
        """Only the font stylesheet may be fetched; everything else ships inline."""
        html = render(data)
        external = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
        assert all(u.startswith("https://fonts.googleapis.com") for u in external), external

    def test_defaults_to_the_tool_name(self, data):
        assert "<title>Dynasty Trade Matchmaker</title>" in render(data)

    def test_title_can_name_the_league(self, scored, league_book):
        """A gallery reads a page's name from the tag, so each league's report
        needs its own name written into it."""
        d = build_report_data(scored, league_book, title="FMB League Trade Board")
        assert "<title>FMB League Trade Board</title>" in render(d)

    def test_title_is_escaped(self, scored, league_book):
        d = build_report_data(scored, league_book, title="<script>x</script>")
        assert "<title><script>" not in render(d)

    def test_both_themes_defined(self, data):
        html = render(data)
        assert "prefers-color-scheme:dark" in html
        assert ':root[data-theme="dark"]' in html

    def test_closing_script_tag_in_data_cannot_break_out(self, data):
        hostile = dict(data)
        hostile["league"] = dict(data["league"], name='</script><img src=x>')
        html = render(hostile)
        assert "</script><img src=x>" not in html
        embedded = json.loads(
            DATA_RE.search(html).group(1).replace("<\\/script", "</script")
        )
        assert embedded["league"]["name"] == '</script><img src=x>'

    def test_writes_a_file(self, data, tmp_path):
        out = write_report(data, tmp_path / "nested" / "r.html")
        assert out.exists()
        assert out.read_text(encoding="utf-8").startswith("<title>")


class TestWithoutFocus:
    def test_focus_is_optional(self, scored, league_book):
        data = build_report_data(scored, league_book, focus_roster=None)
        assert data["meta"]["focus"] is None
        assert DATA_PLACEHOLDER not in render(data)


class TestTargetResolution:
    """`report` and `snapshot` accept a username or a league id."""

    def test_league_id_resolves(self, client, payloads):
        from src.cli import _resolve_league

        league, focus = _resolve_league(client, "1048291736450000000")
        assert league.league_id == "1048291736450000000"
        assert focus is None

    def test_username_resolves_and_finds_their_roster(self, client):
        from src.cli import _resolve_league

        league, focus = _resolve_league(client, "liamsmithh")
        assert focus is not None
        assert league.team(focus).manager == "liamsmithh"

    def test_a_non_numeric_league_id_still_resolves(self, payloads):
        """Guessing from the string's shape alone sent every non-numeric id
        down the username path, where it failed."""
        from src.cli import _resolve_league
        from src.sleeper import DictTransport, SleeperClient

        renamed = {
            k.replace("1048291736450000000", "abc123league"): v
            for k, v in payloads.items()
        }
        league, _ = _resolve_league(
            SleeperClient(transport=DictTransport(renamed)), "abc123league"
        )
        assert league is not None

    def test_unknown_target_says_so(self, client):
        from src.cli import _resolve_league
        from src.sleeper import SleeperError

        with pytest.raises(SleeperError, match="neither"):
            _resolve_league(client, "not-a-real-thing")
