"""
In-play model — football translation of the NBA pipeline's
_calculate_live_prediction / _live_market_probs.

NBA: live margin + pre-game margin × time left, logistic with σ × sqrt(time
left).  Football: goals are (close to) a Poisson process, so the rest of the
match is a fresh score matrix with expected goals scaled by time remaining,
added to the current score.  No variance fudge is needed — the Poisson
variance shrinks with λ automatically.

Red cards: a side down to ten men scores ~30% less and concedes ~25% more for
the rest of the match (literature rule of thumb; refit before betting).
"""

from __future__ import annotations

import numpy as np

from app.analytics.prediction_model import (
    MIN_LAMBDA,
    _fair_price,
    _settle,
    outcome_probabilities,
    score_matrix,
)
from app.schemas.prediction import LivePrediction, MatchPrediction

REGULATION_MINUTES = 90.0
# Goals are not uniform over 90 minutes: ~55% come in the second half.
# Weight remaining time so a minute in the second half counts more.
SECOND_HALF_GOAL_SHARE = 0.55
RED_CARD_ATTACK_FACTOR = 0.70   # own scoring rate while a man down
RED_CARD_DEFENCE_FACTOR = 1.25  # opponent's scoring rate against ten men


def time_remaining_fraction(minute: float) -> float:
    """Share of a match's expected goals still to come at ``minute``."""
    m = max(0.0, min(REGULATION_MINUTES, minute))
    first, second = 1.0 - SECOND_HALF_GOAL_SHARE, SECOND_HALF_GOAL_SHARE
    if m <= 45.0:
        return first * (45.0 - m) / 45.0 + second
    return second * (90.0 - m) / 45.0


def calculate_live_prediction(
    pre_match: MatchPrediction,
    status: str,
    home_score: int,
    away_score: int,
    minute: float,
    home_red_cards: int = 0,
    away_red_cards: int = 0,
) -> LivePrediction | None:
    """status: SCHEDULED, LIVE, FINAL (stoppage time: pass minute ≥ 90)."""
    if status == "SCHEDULED":
        return None

    if status == "FINAL":
        result = "H" if home_score > away_score else ("A" if away_score > home_score else "D")
        return LivePrediction(
            home_win_prob=1.0 if result == "H" else 0.0,
            draw_prob=1.0 if result == "D" else 0.0,
            away_win_prob=1.0 if result == "A" else 0.0,
            pre_match_home_win_prob=pre_match.home_win_prob,
            live_lambda_home=0.0,
            live_lambda_away=0.0,
            home_score=home_score,
            away_score=away_score,
            minute=minute,
            time_remaining_pct=0.0,
            is_final=True,
            result=result,
        )

    t = max(0.005, time_remaining_fraction(minute))
    lh = pre_match.lambda_home * t
    la = pre_match.lambda_away * t
    if home_red_cards > away_red_cards:
        lh, la = lh * RED_CARD_ATTACK_FACTOR, la * RED_CARD_DEFENCE_FACTOR
    elif away_red_cards > home_red_cards:
        lh, la = lh * RED_CARD_DEFENCE_FACTOR, la * RED_CARD_ATTACK_FACTOR
    lh, la = max(lh, MIN_LAMBDA * t), max(la, MIN_LAMBDA * t)

    final = _final_score_matrix(lh, la, home_score, away_score)
    home, draw, away = outcome_probabilities(final)

    return LivePrediction(
        home_win_prob=round(home, 4),
        draw_prob=round(draw, 4),
        away_win_prob=round(away, 4),
        pre_match_home_win_prob=pre_match.home_win_prob,
        live_lambda_home=round(lh, 3),
        live_lambda_away=round(la, 3),
        home_score=home_score,
        away_score=away_score,
        minute=minute,
        time_remaining_pct=round(t, 4),
    )


def _final_score_matrix(lh: float, la: float, home_score: int, away_score: int) -> np.ndarray:
    """Matrix over FINAL scores: remaining-goals matrix shifted by the current score.

    No Dixon-Coles correction in-play: the low-score dependence is a property
    of full matches, not of the last 20 minutes.
    """
    remaining = score_matrix(lh, la, rho=0.0)
    n = remaining.shape[0]
    final = np.zeros((n + home_score, n + away_score))
    final[home_score:, away_score:] = remaining
    return final


def live_market_probs(
    pre_match: MatchPrediction,
    live: LivePrediction,
    ah_line: float | None,
    total_line: float | None,
) -> dict[str, float]:
    """In-play fair prices: home / draw / away, home handicap, over.

    Asian handicap is settled on the FULL match score (pre-match AH); in-play
    Asian lines that settle on the rest-of-match score should subtract the
    current score from the line before calling this.
    """
    out = {
        "home_win": live.home_win_prob,
        "draw": live.draw_prob,
        "away_win": live.away_win_prob,
        "asian_handicap": pre_match.ah_cover_prob,
        "total": pre_match.over_prob,
    }
    if live.is_final:
        margin = live.home_score - live.away_score
        total = live.home_score + live.away_score
        final = np.ones((1, 1))
        if ah_line is not None:
            w, _, lo = _settle(np.array([[float(margin)]]), final, ah_line)
            out["asian_handicap"] = _fair_price(w, lo)
        if total_line is not None:
            w, _, lo = _settle(np.array([[float(total)]]), final, -total_line)
            out["total"] = _fair_price(w, lo)
        return out

    final = _final_score_matrix(
        live.live_lambda_home, live.live_lambda_away, live.home_score, live.away_score,
    )
    rows, cols = final.shape
    if ah_line is not None:
        margin = np.subtract.outer(np.arange(rows), np.arange(cols)).astype(float)
        w, _, lo = _settle(margin, final, ah_line)
        out["asian_handicap"] = round(_fair_price(w, lo), 4)
    if total_line is not None:
        total = np.add.outer(np.arange(rows), np.arange(cols)).astype(float)
        w, _, lo = _settle(total, final, -total_line)
        out["total"] = round(_fair_price(w, lo), 4)
    return out
