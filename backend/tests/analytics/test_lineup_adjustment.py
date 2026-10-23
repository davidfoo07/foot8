"""Tests for lineup adjustment and player absence impact."""

import pytest

from app.analytics.lineup_adjustment import (
    OnOffSplitModel,
    compute_goalkeeper_impact,
    compute_player_impact,
)
from app.analytics.player_impact import build_player_absences
from app.schemas.team import PlayerAbsence


def _absence(name: str, xgf: float, xga: float) -> PlayerAbsence:
    return PlayerAbsence(player_id=name, name=name, status="OUT",
                         xgf_impact=xgf, xga_impact=xga, xgd_impact=xgf - xga)


class TestOnOffSplitModel:
    model = OnOffSplitModel()

    def test_no_absences(self):
        r = self.model.calculate_adjusted_ratings("ARS", 2.0, 0.9, [])
        assert r.adjusted_xgd == pytest.approx(1.1)
        assert r.confidence == "HIGH"

    def test_diminishing_returns(self):
        r = self.model.calculate_adjusted_ratings(
            "ARS", 2.0, 0.9, [_absence("A", -0.2, 0.0), _absence("B", -0.2, 0.0)],
        )
        assert r.xgf_delta == pytest.approx(-0.2 - 0.2 * 0.85)

    def test_xg_floor(self):
        r = self.model.calculate_adjusted_ratings("X", 0.5, 1.5, [_absence("A", -1.0, 0.0)])
        assert r.adjusted_xgf == 0.2

    def test_confidence_from_minutes(self):
        ctx = {"A": {"on_minutes": 1500, "off_minutes": 400}}
        r = self.model.calculate_adjusted_ratings("X", 1.5, 1.3, [_absence("A", -0.1, 0.0)], ctx)
        assert r.confidence == "HIGH"
        ctx = {"A": {"on_minutes": 500, "off_minutes": 100}}
        r = self.model.calculate_adjusted_ratings("X", 1.5, 1.3, [_absence("A", -0.1, 0.0)], ctx)
        assert r.confidence == "LOW"


def test_compute_player_impact():
    xgf, xga, xgd = compute_player_impact(2.0, 1.6, 1.0, 1.2, minutes_share=0.9)
    assert xgf == pytest.approx(-0.36)
    assert xga == pytest.approx(0.18)
    assert xgd == pytest.approx(-0.54)


def test_goalkeeper_impact():
    assert compute_goalkeeper_impact(0.15, -0.10) == (0.0, 0.25, -0.25)


class TestBuildPlayerAbsences:
    metrics = {
        "Striker": {"MIN": 85, "GP": 20, "POS": "FWD", "ON_XGD90": 0.9},
        "Keeper": {"MIN": 90, "GP": 25, "POS": "GK", "GOALS_PREVENTED90": 0.15},
        "Sub": {"MIN": 15, "GP": 12, "POS": "MID", "ON_XGD90": 1.5},
    }

    def test_relative_to_team(self):
        out = build_player_absences(
            [{"player_id": "1", "player_name": "Striker", "status": "OUT"}],
            self.metrics, team_xgd=0.5,
        )
        # rel 0.4 × share 85/90 × 0.5 dampening
        assert out[0].xgd_impact == pytest.approx(-0.4 * 85 / 90 * 0.5, abs=1e-3)
        assert out[0].xgf_impact < 0 and out[0].xga_impact > 0
        assert abs(out[0].xgf_impact) > abs(out[0].xga_impact)  # forward: mostly attack

    def test_goalkeeper_and_filters(self):
        out = build_player_absences(
            [
                {"player_id": "2", "player_name": "Keeper", "status": "SUSPENDED"},
                {"player_id": "3", "player_name": "Sub", "status": "OUT"},
                {"player_id": "4", "player_name": "Unknown Guy", "status": "QUESTIONABLE"},
                {"player_id": "1", "player_name": "Striker", "status": "QUESTIONABLE"},
            ],
            self.metrics, injury_overrides={"Striker": "OFF"}, team_xgd=0.5,
        )
        names = [a.name for a in out]
        assert names[0] == "Keeper"  # biggest impact first
        assert "Sub" not in names and "Striker" not in names
        assert out[0].xga_impact == pytest.approx(0.25)
        unknown = next(a for a in out if a.name == "Unknown Guy")
        assert unknown.xgf_impact == pytest.approx(-0.025)
