"""Tests for UCL fixture matching and advance-market parsing."""

from datetime import UTC, datetime

import pytest

from app.connectors.espn import EspnMatch, parse_events
from app.services.ucl import match_fixture, name_similarity, parse_advance_events


def _fx(eid, home, away, day):
    return EspnMatch("uefa.champions", eid, datetime(2026, 3, day, 20, tzinfo=UTC),
                     eid + "h", home, eid + "a", away, None, None, False, "STATUS_SCHEDULED")


@pytest.mark.parametrize("a,b", [
    ("FC Bayern München", "Bayern Munich"),
    ("Paris Saint-Germain FC", "Paris Saint-Germain"),
    ("Galatasaray SK", "Galatasaray"),
    ("Sporting CP", "Sporting CP"),
    ("FK Bodø/Glimt", "Bodo/Glimt"),
    ("Olympiakós SFP", "Olympiacos"),
])
def test_name_similarity_matches(a, b):
    assert name_similarity(a, b) >= 0.55


def test_name_similarity_rejects_different_clubs():
    assert name_similarity("Manchester United", "Manchester City") < 0.9
    assert name_similarity("Real Madrid", "Atletico Madrid") < 0.55


def test_match_fixture_picks_right_pair():
    cands = [_fx("1", "Bayern Munich", "Atalanta", 18), _fx("2", "Real Madrid", "Manchester City", 18),
             _fx("3", "Arsenal", "Bayer Leverkusen", 17)]
    got = match_fixture("FC Bayern München", "Atalanta BC", datetime(2026, 3, 18, tzinfo=UTC), cands)
    assert got.event_id == "1"
    got = match_fixture("Real Madrid CF", "Manchester City FC", datetime(2026, 3, 18, tzinfo=UTC), cands)
    assert got.event_id == "2"
    assert match_fixture("Arsenal FC", "Bayer 04 Leverkusen", datetime(2026, 3, 25, tzinfo=UTC), cands) is None


def test_parse_advance_events():
    events = [{"slug": "champions-league-arsenal-vs-psg-to-advance", "closed": False,
               "title": "Champions League: Arsenal vs. PSG (To Advance)",
               "markets": [{"outcomes": '["Arsenal", "PSG"]', "outcomePrices": '["0.41", "0.59"]',
                            "liquidityNum": 50000}]},
              {"slug": "x", "title": "Will Arsenal reach the final?", "markets": []}]
    [m] = parse_advance_events(events)
    assert m["team_a"] == "Arsenal" and m["price_a"] == 0.41


def test_espn_parse_events():
    raw = {"events": [{"id": "9", "date": "2025-09-17T19:00Z", "competitions": [{
        "neutralSite": False, "status": {"type": {"name": "STATUS_FULL_TIME"}},
        "competitors": [
            {"homeAway": "home", "score": "2", "team": {"id": "1", "displayName": "Olympiacos"}},
            {"homeAway": "away", "score": "0", "team": {"id": "2", "displayName": "Pafos"}}]}]}]}
    [m] = parse_events("uefa.champions", raw)
    assert m.home_goals == 2 and m.is_result and m.is_european
