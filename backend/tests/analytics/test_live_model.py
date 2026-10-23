"""Tests for the in-play model."""

import pytest

from app.analytics.live_model import (
    calculate_live_prediction,
    live_market_probs,
    time_remaining_fraction,
)
from app.analytics.prediction_model import predict_match

PRE = predict_match(1.8, 1.0, 1.2, 1.5, ah_line=-0.5, total_line=2.5)


def test_time_fraction():
    assert time_remaining_fraction(0) == pytest.approx(1.0)
    assert time_remaining_fraction(45) == pytest.approx(0.55)
    assert time_remaining_fraction(90) == pytest.approx(0.0)


def test_kickoff_matches_pre_match():
    live = calculate_live_prediction(PRE, "LIVE", 0, 0, 0)
    # Pre-match uses Dixon-Coles, in-play does not — close but not identical
    assert live.home_win_prob == pytest.approx(PRE.home_win_prob, abs=0.02)
    probs = live_market_probs(PRE, live, -0.5, 2.5)
    assert probs["total"] == pytest.approx(PRE.over_prob, abs=0.02)


def test_late_lead_near_certain():
    live = calculate_live_prediction(PRE, "LIVE", 2, 0, 88)
    assert live.home_win_prob > 0.97


def test_away_goal_lowers_home():
    live = calculate_live_prediction(PRE, "LIVE", 0, 1, 30)
    assert live.home_win_prob < PRE.home_win_prob
    assert live.away_win_prob > PRE.away_win_prob


def test_over_already_landed():
    live = calculate_live_prediction(PRE, "LIVE", 2, 1, 60)
    assert live_market_probs(PRE, live, None, 2.5)["total"] == pytest.approx(1.0)


def test_red_card_hurts():
    base = calculate_live_prediction(PRE, "LIVE", 0, 0, 30)
    red = calculate_live_prediction(PRE, "LIVE", 0, 0, 30, home_red_cards=1)
    assert red.home_win_prob < base.home_win_prob


def test_final():
    live = calculate_live_prediction(PRE, "FINAL", 1, 1, 90)
    assert live.result == "D" and live.draw_prob == 1.0
    probs = live_market_probs(PRE, live, 0.0, 2.0)
    assert probs["asian_handicap"] == 0.5  # push → fair price undefined, 0.5
    assert probs["total"] == 0.5
