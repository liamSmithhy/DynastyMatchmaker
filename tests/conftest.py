"""Shared fixtures, and a hard guarantee that the suite never touches the network.

Every test here runs on local CSVs and local JSON. If any code path reaches for
a socket the test fails loudly rather than quietly depending on the internet
being up and DynastyProcess being unchanged.
"""

from __future__ import annotations

import json
import socket
import urllib.request
from pathlib import Path

import pytest

from src.sleeper import DictTransport, SleeperClient
from src.values import ValueBook

FIXTURES = Path(__file__).parent / "fixtures"
VALUES_DIR = FIXTURES / "values"                      # small synthetic board
LEAGUE_VALUES_DIR = FIXTURES / "league" / "values"    # real values, trimmed
LEAGUE_PAYLOADS = FIXTURES / "league" / "payloads.json"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Any outbound connection is a test failure."""

    def blocked(*args, **kwargs):
        raise AssertionError("test attempted a network call")

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture(scope="session")
def book() -> ValueBook:
    return ValueBook(source_dir=VALUES_DIR)


@pytest.fixture(scope="session")
def league_book() -> ValueBook:
    """The values the league fixture was actually built from."""
    return ValueBook(source_dir=LEAGUE_VALUES_DIR)


@pytest.fixture(scope="session")
def payloads() -> dict:
    return json.loads(LEAGUE_PAYLOADS.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def client(payloads) -> SleeperClient:
    return SleeperClient(transport=DictTransport(payloads))


@pytest.fixture(scope="session")
def league(client):
    return client.load_league("1048291736450000000")


@pytest.fixture(scope="session")
def scored(league, league_book):
    from src.scoring import score_league

    return score_league(league, league_book)


@pytest.fixture()
def scratch_scored(scored):
    """A private copy for tests that mutate a team to construct a situation.

    The shared ``scored`` is session-scoped; a test that flips a window or adds
    a player to it silently changes what every later test sees.
    """
    import copy

    return copy.deepcopy(scored)
