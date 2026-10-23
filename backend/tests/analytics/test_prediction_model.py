"""Tests for the football prediction model."""

import pytest

from app.analytics.prediction_model import (
    HOME_ADVANTAGE_GOALS,
    asian_handicap_probabilities,
    btts_probability,
    calculate_schedule_modifier,
    estimate_expected_goals,
    league_scoring_baseline,
    outcome_probabilities,
    predict_match,
    score_matrix,
    supremacy_to_probabilities,
    total_goals_probabilities,
    win_probability_to_supremacy,
)

AVG = 1.375


class TestScoreMatrix:
    def test_sums_to_one(self):
        assert score_matrix(1.6, 1.1).sum() == pytest.approx(1.0)

    def test_dixon_coles_inflates_draws(self):
        _, d_dc, _ = outcome_probabilities(score_matrix(1.3, 1.3, rho=-0.1))
        _, d_ind, _ = outcome_probabilities(score_matrix(1.3, 1.3, rho=0.0))
        assert d_dc > d_ind

    def test_equal_teams_symmetric(self):
        h, d, a = outcome_probabilities(score_matrix(1.4, 1.4))
        assert h == pytest.approx(a, abs=1e-9)
        assert 0.25 < d < 0.32

    def test_btts(self):
        m = score_matrix(1.5, 1.2, rho=0.0)
        # Independent Poisson: P(both score) = (1 - e^-1.5)(1 - e^-1.2)
        assert btts_probability(m) == pytest.approx(0.7769 * 0.6988, abs=1e-3)


class TestMarkets:
    m = score_matrix(1.7, 1.0)

    def test_half_line_handicap_equals_win(self):
        h, d, _ = outcome_probabilities(self.m)
        ah = asian_handicap_probabilities(self.m, -0.5)
        assert ah["push"] == 0.0
        assert ah["fair_prob"] == pytest.approx(h)
        assert asian_handicap_probabilities(self.m, 0.5)["fair_prob"] == pytest.approx(h + d)

    def test_draw_no_bet_pushes_on_draw(self):
        h, d, a = outcome_probabilities(self.m)
        ah = asian_handicap_probabilities(self.m, 0.0)
        assert ah["push"] == pytest.approx(d)
        assert ah["fair_prob"] == pytest.approx(h / (h + a))

    def test_quarter_line_between_neighbours(self):
        p05 = asian_handicap_probabilities(self.m, -0.5)["fair_prob"]
        p075 = asian_handicap_probabilities(self.m, -0.75)["fair_prob"]
        p10 = asian_handicap_probabilities(self.m, -1.0)["fair_prob"]
        assert p10 < p075 < p05

    def test_totals_monotonic(self):
        o15 = total_goals_probabilities(self.m, 1.5)["fair_prob"]
        o25 = total_goals_probabilities(self.m, 2.5)["fair_prob"]
        o35 = total_goals_probabilities(self.m, 3.5)["fair_prob"]
        assert o15 > o25 > o35

    def test_whole_total_pushes(self):
        t = total_goals_probabilities(self.m, 3.0)
        assert t["push"] > 0.1
        assert t["win"] + t["push"] + t["lose"] == pytest.approx(1.0)


class TestExpectedGoals:
    def test_average_teams_reproduce_league_level(self):
        lh, la = estimate_expected_goals(AVG, AVG, AVG, AVG, league_xg=AVG, baseline_goals=2.75)
        assert lh == pytest.approx(1.375)
        assert la == pytest.approx(1.375)

    def test_multiplicative(self):
        lh, _ = estimate_expected_goals(2.0, AVG, AVG, 1.8, league_xg=AVG, baseline_goals=2.75)
        assert lh == pytest.approx(1.375 * (2.0 / AVG) * (1.8 / AVG))


class TestPredictMatch:
    def test_equal_teams_home_favoured(self):
        p = predict_match(AVG, AVG, AVG, AVG)
        assert p.home_win_prob > p.away_win_prob
        assert p.projected_supremacy == pytest.approx(HOME_ADVANTAGE_GOALS, abs=0.01)
        assert p.home_win_prob + p.draw_prob + p.away_win_prob == pytest.approx(1.0, abs=0.002)

    def test_neutral_venue_symmetric(self):
        p = predict_match(AVG, AVG, AVG, AVG, home_advantage=0.0)
        assert p.home_win_prob == pytest.approx(p.away_win_prob, abs=0.001)

    def test_strong_home_side(self):
        # City-like (2.2 / 0.8) vs a relegation side (1.0 / 1.9)
        p = predict_match(2.2, 0.8, 1.0, 1.9, ah_line=-1.5, total_line=2.5)
        assert p.home_win_prob > 0.75
        assert p.lambda_home > 2.5
        assert 0.4 < p.ah_cover_prob < 0.75
        assert p.over_prob > 0.6

    def test_motivation_and_schedule_shift_supremacy(self):
        base = predict_match(AVG, AVG, AVG, AVG)
        tired = predict_match(AVG, AVG, AVG, AVG, home_schedule_mod=-0.15,
                              home_motivation="REST_EXPECTED", away_motivation="DESPERATE")
        assert tired.home_win_prob < base.home_win_prob
        assert tired.projected_total == pytest.approx(base.projected_total, abs=0.01)
        assert tired.schedule_adjustment == pytest.approx(-0.15 - 0.35)


class TestScheduleModifier:
    def test_worst_congestion_only(self):
        mod = calculate_schedule_modifier(is_short_rest=True, is_3_in_7=True, is_4_in_10=True)
        assert mod == -0.20

    def test_rest_advantage_capped(self):
        assert calculate_schedule_modifier(rest_days=6, opponent_rest_days=2) == 0.12
        # Two weeks off is no better than one
        assert calculate_schedule_modifier(rest_days=14, opponent_rest_days=7) == 0.0

    def test_travel_and_europe(self):
        mod = calculate_schedule_modifier(travel_distance_km=2000, played_europe_midweek=True)
        assert mod == -0.13


class TestLeagueBaseline:
    blended = {"A": {"xgf": 1.5, "xga": 1.2}, "B": {"xgf": 1.2, "xga": 1.5}}

    def test_no_current_uses_prior(self):
        b = league_scoring_baseline({}, {"matches": 380, "home_goals": 600, "away_goals": 480},
                                    self.blended)
        assert b["baseline_goals"] == pytest.approx(1080 / 380, abs=1e-3)
        assert b["home_advantage"] == pytest.approx(120 / 380, abs=1e-3)
        assert b["level_weight"] == 0.0
        assert b["league_xg"] == pytest.approx(1.35)

    def test_current_weighted(self):
        b = league_scoring_baseline({"matches": 60, "home_goals": 105, "away_goals": 75},
                                    {"matches": 380, "home_goals": 570, "away_goals": 475},
                                    self.blended)
        assert b["level_weight"] == 0.5
        assert b["baseline_goals"] == pytest.approx(0.5 * 3.0 + 0.5 * 1045 / 380, abs=1e-3)


class TestSupremacyConversion:
    def test_round_trip(self):
        h, _, _ = supremacy_to_probabilities(0.6, 2.7)
        assert win_probability_to_supremacy(h, 2.7) == pytest.approx(0.6, abs=0.01)

    def test_zero_supremacy_even(self):
        h, _, a = supremacy_to_probabilities(0.0, 2.6)
        assert h == pytest.approx(a)
