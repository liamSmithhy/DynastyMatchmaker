"""Name normalization (gotcha 6).

Normalization closes most of the gap between two vendors' spellings. It does not
close all of it, which is why production joins on sleeper_id first -- there are
tests here for both the cases it handles and the ones it cannot.
"""

from __future__ import annotations

import pytest

from src.values import normalize_name


class TestNormalize:
    @pytest.mark.parametrize(
        "raw, expected",
        [
            ("Ja'Marr Chase", "jamarr chase"),
            ("Travis Etienne Jr.", "travis etienne"),
            ("Marvin Harrison Jr.", "marvin harrison"),
            ("Kenneth Walker III", "kenneth walker"),
            ("Michael Pittman Jr.", "michael pittman"),
            ("Odell Beckham Jr", "odell beckham"),
            ("Amon-Ra St. Brown", "amon ra st brown"),
            ("D.K. Metcalf", "dk metcalf"),
            ("A.J. Brown", "aj brown"),
            ("  Justin   Jefferson  ", "justin jefferson"),
            ("DeVonta Smith", "devonta smith"),
        ],
    )
    def test_normalization(self, raw, expected):
        assert normalize_name(raw) == expected

    def test_accents_are_folded(self):
        assert normalize_name("Chigoziem Okónkwo") == normalize_name("Chigoziem Okonkwo")

    def test_case_insensitive(self):
        assert normalize_name("PUKA NACUA") == normalize_name("Puka Nacua")

    def test_suffix_variants_agree(self):
        assert normalize_name("Brian Robinson Jr.") == normalize_name("Brian Robinson Jr")
        assert normalize_name("Odell Beckham Jr.") == normalize_name("Odell Beckham")

    def test_roman_numeral_suffixes(self):
        for suffix in ("II", "III", "IV", "V"):
            assert normalize_name(f"Player Name {suffix}") == "player name"

    def test_short_names_keep_their_last_token(self):
        """Only strip a suffix when a real name survives it."""
        assert normalize_name("Boston Scott") == "boston scott"
        assert normalize_name("Deebo Samuel Sr.") == "deebo samuel"

    def test_empty_and_none_safe(self):
        assert normalize_name("") == ""
        assert normalize_name(None) == ""

    def test_hyphenated_names_survive(self):
        assert normalize_name("Jaxon Smith-Njigba") == "jaxon smith njigba"

    def test_known_limitation_nicknames_do_not_match(self):
        """Documented ~0.5% miss rate: normalization is not a nickname table.
        This is exactly why the production join is on sleeper_id."""
        assert normalize_name("Mike Evans") != normalize_name("Michael Evans")


class TestLookup:
    def test_lookup_by_name(self, book):
        assert book.get("Alpha Receiver") is not None

    def test_lookup_is_normalization_insensitive(self, book):
        assert book.get("  alpha   receiver ") is book.get("Alpha Receiver")

    def test_lookup_by_sleeper_id(self, book):
        player = book.get("Alpha Receiver")
        assert book.get_by_sleeper(player.sleeper_id) is player

    def test_sleeper_id_accepts_int_or_str(self, book):
        player = book.get("Alpha Receiver")
        assert book.get_by_sleeper(int(player.sleeper_id)) is player

    def test_unknown_player_is_zero_not_an_error(self, book):
        assert book.get("Nobody At All") is None
        assert book.value("Nobody At All") == 0.0
        assert book.value_by_sleeper("999999") == 0.0

    def test_missing_crosswalk_entry_leaves_no_sleeper_id(self, book):
        """Not every valued player has a Sleeper id; that must not break the
        name path."""
        orphan = book.get("Zulu Back")
        assert orphan is not None
        assert orphan.sleeper_id is None
        assert book.value("Zulu Back") > 0

    def test_none_sleeper_id(self, book):
        assert book.get_by_sleeper(None) is None
