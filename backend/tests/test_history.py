"""Tests for settling and grading past predictions."""

from datetime import UTC, datetime

import pytest

from app.services.history import GradedMatch, _finish, grade_quote, settle_yes, summarise


@pytest.mark.parametrize("mtype,line,hg,ag,expected", [
    ("home_win", None, 2, 1, (1.0, 0.0)),
    ("draw", None, 1, 1, (1.0, 0.0)),
    ("away_win", None, 1, 1, (0.0, 1.0)),
    ("btts", None, 1, 0, (0.0, 1.0)),
    ("total", 2.5, 2, 1, (1.0, 0.0)),
    ("total", 3.0, 2, 1, (0.0, 0.0)),        # whole line: push
    ("total", 2.75, 2, 1, (0.5, 0.0)),       # quarter: half win, half push
    ("asian_handicap", -1.5, 1, 0, (0.0, 1.0)),
    ("asian_handicap", -0.75, 1, 0, (0.5, 0.0)),
    ("asian_handicap", 1.5, 0, 1, (1.0, 0.0)),
])
def test_settle_yes(mtype, line, hg, ag, expected):
    assert settle_yes(mtype, line, hg, ag) == expected


def test_grade_quote_no_side():
    # Model 0.47 vs price 0.355 on Over 2.5 → buy NO at 0.645; 0-2 finish → NO wins
    q = grade_quote("total", 2.5, "Over 2.5", 0.355, 0.471 - 0.116 - 0.116, 0, 2)
    assert q.side == "NO" and q.result == "WON"
    assert q.pnl == pytest.approx(1 / 0.645 - 1, abs=1e-3)
    assert q.label == "NOT Over 2.5"


def test_grade_quote_loss():
    q = grade_quote("home_win", None, "Chelsea win", 0.50, 0.60, 0, 2)
    assert q.side == "YES" and q.result == "LOST" and q.pnl == -1.0


def _gm(hg, ag, probs, market):
    return _finish(GradedMatch(
        league="EPL", slug=f"s{hg}{ag}", kickoff=datetime(2026, 10, 24, tzinfo=UTC), home="A",
        away="B", source="live", predicted_at=None, home_goals=hg, away_goals=ag,
        probs={"home_win": probs[0], "draw": probs[1], "away_win": probs[2], "btts": 0.5},
        market={"home_win": market[0], "draw": market[1], "away_win": market[2]},
        pick="", pick_correct=False, ll_model=0.0, ll_market=None,
    ))


def test_finish_and_summarise():
    a = _gm(2, 0, (0.6, 0.25, 0.15), (0.5, 0.3, 0.2))
    b = _gm(0, 2, (0.6, 0.25, 0.15), (0.3, 0.3, 0.4))
    assert a.pick == "H" and a.pick_correct and not b.pick_correct
    assert a.ll_model < a.ll_market
    s = summarise([a, b])
    assert s["matches"] == 2 and s["pick_accuracy"] == 0.5 and s["market_pick_accuracy"] == 1.0
