"""Tests for schedule context, motivation, season blend and squad prior."""

from datetime import date

import pytest

from app.analytics.schedule_engine import calculate_schedule_context, determine_motivation
from app.analytics.season_blend import PROMOTED_TEAM_PRIOR, blend_team_ratings
from app.analytics.squad_prior import (
    compute_squad_priors,
    compute_squad_turnover,
    is_stale_prior,
)


class TestScheduleContext:
    def test_midweek_turnaround(self):
        ctx = calculate_schedule_context("ARS", date(2026, 10, 27), [
            {"date": "2026-10-25", "competition": "EPL"},
            {"date": "2026-10-22", "competition": "UCL"},
        ])
        assert ctx.rest_days == 1
        assert ctx.is_short_rest and ctx.is_3_in_7
        assert not ctx.played_europe_midweek  # last match was the league

    def test_europe_then_weekend(self):
        ctx = calculate_schedule_context("TOT", date(2026, 10, 25), [
            {"date": "2026-10-22", "competition": "UEL"},
        ])
        assert ctx.played_europe_midweek
        assert ctx.rest_days == 2

    def test_no_history(self):
        assert calculate_schedule_context("X", date(2026, 10, 25)).rest_days == 6


class TestMotivation:
    def test_early_season_neutral(self):
        m = determine_motivation("X", "1-0-3", 20, 3, 34, points_above_relegation=-2)
        assert m.motivation_flag == "NEUTRAL"

    def test_relegation_fight(self):
        m = determine_motivation("X", "6-8-18", 17, 26, 6, points_above_relegation=2)
        assert m.motivation_flag == "DESPERATE"

    def test_dead_rubber(self):
        m = determine_motivation("X", "13-10-12", 11, 49, 3, points_above_relegation=18,
                                 points_behind_europe=12, points_behind_leader=35)
        assert m.motivation_flag == "REST_EXPECTED"

    def test_champions(self):
        m = determine_motivation("X", "28-5-2", 1, 89, 3, 60, status="CHAMPIONS")
        assert m.motivation_flag == "REST_EXPECTED"

    def test_title_race(self):
        m = determine_motivation("X", "20-6-4", 2, 66, 8, 40, points_behind_leader=2)
        assert m.motivation_flag == "FIGHTING"


class TestSeasonBlend:
    def test_weights(self):
        out = blend_team_ratings(
            {"ARS": {"xgf": 2.4, "xga": 0.8, "gp": 8}},
            {"ARS": {"xgf": 2.0, "xga": 1.0}},
            k=8,
        )
        assert out["ARS"]["xgf"] == pytest.approx(2.2)
        assert out["ARS"]["xgd"] == pytest.approx(2.2 - 0.9)
        assert out["ARS"]["current_weight"] == 0.5

    def test_promoted_prior(self):
        out = blend_team_ratings({"SUN": {"xgf": 1.3, "xga": 1.3, "gp": 0}}, {}, k=8)
        assert out["SUN"]["xgf"] == PROMOTED_TEAM_PRIOR["xgf"]


class TestSquadPrior:
    def _metrics(self):
        # 20 teams, 11 players each, team i on-pitch xGD scales with i
        metrics, squads = {}, {}
        for t in range(20):
            ids = []
            for p in range(11):
                pid = t * 100 + p
                metrics[pid] = {"PLAYER_ID": pid, "MIN": 85, "GP": 30,
                                "ON_XGF90": 1.0 + t * 0.04, "ON_XGA90": 1.8 - t * 0.04}
                ids.append(pid)
            squads[f"T{t}"] = ids
        prior = {f"T{t}": {"xgf": 1.375, "xga": 1.375} for t in range(20)}
        return squads, metrics, prior

    def test_ordering_and_anchor(self):
        squads, metrics, prior = self._metrics()
        out = compute_squad_priors(squads, metrics, prior)
        assert out["T19"]["xgd"] > out["T0"]["xgd"]
        mean_xgf = sum(r["xgf"] for r in out.values()) / len(out)
        assert mean_xgf == pytest.approx(1.375, abs=1e-3)

    def test_too_few_squads(self):
        squads, metrics, prior = self._metrics()
        assert compute_squad_priors(dict(list(squads.items())[:5]), metrics, prior) == {}

    def test_turnover(self):
        squads, metrics, _ = self._metrics()
        new = {"T0": squads["T0"][:6] + squads["T1"][:5]}
        t = compute_squad_turnover(new, {"T0": squads["T0"]}, metrics)["T0"]
        assert t["minutes_left"] == pytest.approx(5 / 11, abs=1e-3)
        assert is_stale_prior("team", t["turnover"], 0.2)
        assert not is_stale_prior("squad", t["turnover"], 0.2)
