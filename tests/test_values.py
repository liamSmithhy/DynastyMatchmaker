"""The value book: format inversion, the ECR curve, and caching."""

from __future__ import annotations

import json
import time

import pytest

from src.values import CACHE_TTL_SECONDS, CsvCache, EcrValueCurve, ValueBook

from .conftest import VALUES_DIR


class TestFormatInversion:
    def test_quarterbacks_are_worth_far_more_in_superflex(self, book):
        """The headline property of the whole system."""
        qb = book.get("Delta Passer")
        assert qb.value_2qb > qb.value_1qb * 2

    def test_receivers_are_not_inflated_by_superflex(self, book):
        wr = book.get("Alpha Receiver")
        assert wr.value_2qb < wr.value_1qb

    def test_value_selects_the_format(self, book):
        qb = book.get("Delta Passer")
        assert book.value("Delta Passer", superflex=False) == qb.value_1qb
        assert book.value("Delta Passer", superflex=True) == qb.value_2qb

    def test_a_qb_outranks_a_wr_only_in_superflex(self, book):
        qb = book.value("Delta Passer", superflex=False)
        wr = book.value("Charlie Receiver", superflex=False)
        assert qb < wr
        assert book.value("Delta Passer", superflex=True) > book.value(
            "Charlie Receiver", superflex=True
        )


class TestEcrCurve:
    def test_monotone_non_increasing(self, book):
        curve = book.curve()
        values = [curve.value_at(e) for e in range(1, 400, 7)]
        assert values == sorted(values, reverse=True)

    def test_interpolates_between_known_ranks(self, book):
        curve = book.curve()
        assert curve.value_at(1.0) > curve.value_at(2.0) > curve.value_at(3.0)

    def test_clamps_above_the_top(self, book):
        curve = book.curve()
        assert curve.value_at(-5) == pytest.approx(curve.value_at(1.0))

    def test_tail_keeps_decaying_rather_than_flattening(self, book):
        curve = book.curve()
        assert curve.value_at(600) < curve.value_at(500)
        assert curve.value_at(600) >= 0

    def test_smooths_a_non_monotone_input(self):
        """Rank and value are separate scrapes and disagree at the margins;
        the curve must not inherit the disagreement."""
        curve = EcrValueCurve([(1, 100), (2, 250), (3, 60), (4, 40)])
        values = [curve.value_at(e) for e in (1, 2, 3, 4)]
        assert values == sorted(values, reverse=True)

    def test_rejects_an_empty_curve(self):
        with pytest.raises(ValueError):
            EcrValueCurve([])


class TestSourceDir:
    def test_loads_without_network_or_cache(self, book):
        assert len(book.players) == 26
        assert book.get("Alpha Receiver") is not None

    def test_crosswalk_is_applied(self, book):
        assert book.get("Alpha Receiver").sleeper_id == "2001"


class TestCache:
    def test_fresh_cache_is_read_without_fetching(self, tmp_path):
        target = tmp_path / "values-players.csv"
        target.write_text("hello", encoding="utf-8")
        (tmp_path / "values-players.csv.meta.json").write_text(
            json.dumps({"fetched_at": time.time()}), encoding="utf-8"
        )
        cache = CsvCache(cache_dir=tmp_path, offline=True)
        assert cache.read("values-players.csv") == "hello"

    def test_stale_cache_is_used_when_offline(self, tmp_path):
        """A stale value beats a failed run."""
        target = tmp_path / "values-players.csv"
        target.write_text("stale", encoding="utf-8")
        (tmp_path / "values-players.csv.meta.json").write_text(
            json.dumps({"fetched_at": time.time() - CACHE_TTL_SECONDS * 3}),
            encoding="utf-8",
        )
        cache = CsvCache(cache_dir=tmp_path, offline=True)
        assert cache.read("values-players.csv") == "stale"
        assert any("offline" in note for note in cache.notes)

    def test_offline_without_cache_raises(self, tmp_path):
        cache = CsvCache(cache_dir=tmp_path, offline=True)
        with pytest.raises(FileNotFoundError):
            cache.read("values-players.csv")

    def test_ttl_is_twelve_hours(self):
        assert CACHE_TTL_SECONDS == 12 * 60 * 60

    def test_corrupt_metadata_is_treated_as_no_cache(self, tmp_path):
        (tmp_path / "values-players.csv").write_text("x", encoding="utf-8")
        (tmp_path / "values-players.csv.meta.json").write_text("{{{", encoding="utf-8")
        cache = CsvCache(cache_dir=tmp_path, offline=True)
        assert cache.age_seconds("values-players.csv") is None
        with pytest.raises(FileNotFoundError):
            cache.read("values-players.csv")


class TestAdapterBoundary:
    def test_value_book_needs_no_sleeper_types(self):
        """values.py must not import the league adapter -- the two are meant to
        be swappable independently."""
        import src.values as values

        source = values.__file__
        with open(source, encoding="utf-8") as handle:
            text = handle.read()
        assert "from .sleeper" not in text
        assert "import sleeper" not in text

    def test_loading_from_source_dir_takes_no_cache_argument_path(self):
        book = ValueBook(source_dir=VALUES_DIR)
        assert book.notes == []
