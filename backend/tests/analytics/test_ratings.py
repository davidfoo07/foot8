"""Tests for as-of-date ratings, standings and contribution-based absences."""

from datetime import date, datetime

import pytest

from app.analytics.player_impact import build_player_absences
from app.connectors.understat import MatchRecord
from app.services.ratings import RatingConfig, build_ratings
from app.services.standings import league_table, motivation_for


def _m(i, d, h, a, hg, ag, hx, ax, season=2025):
    return MatchRecord(str(i), "EPL", season, datetime.fromisoformat(d), h, a, hg, ag, hx, ax)


PRIOR = [_m(1, "2024-09-01", "A", "B", 2, 0, 2.0, 0.5, 2024),
         _m(2, "2024-09-08", "B", "A", 1, 1, 1.0, 1.5, 2024)]
CURRENT = [_m(3, "2025-08-16", "A", "C", 3, 1, 2.5, 1.0),
           _m(4, "2025-08-23", "C", "A", 0, 0, 0.8, 0.9)]


def test_no_lookahead():
    r = build_ratings(CURRENT, PRIOR, date(2025, 8, 16))
    assert r.teams["A"]["gp"] == 0  # the match ON the date is not used
    assert build_ratings(CURRENT, PRIOR, date(2025, 8, 17)).teams["A"]["gp"] == 1


def test_promoted_team_prior():
    cfg = RatingConfig(promoted_xgd=-0.6)
    r = build_ratings(CURRENT, PRIOR, date(2025, 8, 1), cfg)
    assert r.teams["C"]["prior_source"] == 1.0
    assert r.teams["C"]["xgd"] == pytest.approx(-0.6, abs=1e-3)


def test_blend_weight_and_value():
    cfg = RatingConfig(k_blend=1.0, xg_weight=1.0)
    r = build_ratings(CURRENT, PRIOR, date(2025, 8, 17), cfg)
    a = r.teams["A"]
    assert a["current_weight"] == 0.5
    # prior A: xgf (2.0 + 1.5) / 2 = 1.75 ; current 2.5
    assert a["xgf"] == pytest.approx(0.5 * 2.5 + 0.5 * 1.75)


def test_league_level_from_prior():
    r = build_ratings(CURRENT, PRIOR, date(2025, 8, 1))
    assert r.baseline_goals == pytest.approx(2.0)  # (2+0 + 1+1) / 2
    assert r.home_advantage == pytest.approx(1.0)  # (3 home − 1 away) / 2


def test_table_and_motivation():
    t = league_table(CURRENT, date(2025, 9, 1))
    assert [r["team"] for r in t][:2] == ["A", "C"]
    assert t[0]["points"] == 4 and t[1]["points"] == 1
    mot = motivation_for("EPL", CURRENT, date(2025, 9, 1))
    assert mot["A"].motivation_flag == "NEUTRAL"  # early season


def test_contribution_absence():
    metrics = {"Striker": {"MIN": 85, "GP": 10, "POS": "FWD", "XGI90": 0.95, "ON_XGA90": 0.9}}
    [a] = build_player_absences(
        [{"player_id": "1", "player_name": "Striker", "status": "OUT"}],
        metrics, team_xga=1.1,
    )
    share = 85 / 90
    assert a.xgf_impact == pytest.approx(-(0.95 - 0.35) * 0.5 * share, abs=1e-3)
    assert a.xga_impact == pytest.approx(0.2 * share * 0.5, abs=1e-3)
