"""
Knockout ties — two-legged aggregates and single-match finals, with extra
time and penalties.

UEFA rules since 2021-22: no away-goals rule.  Level on aggregate after the
second leg's 90 minutes → 30 minutes of extra time at the second-leg venue →
penalties.  A final is one match at a neutral venue with the same ET /
penalty finish.

Polymarket's match moneylines settle on 90 minutes; its "To Advance" markets
settle on who goes through — that is what these functions price.
"""

from __future__ import annotations

import numpy as np

from app.analytics.live_model import time_remaining_fraction
from app.analytics.prediction_model import DIXON_COLES_RHO, MIN_LAMBDA, score_matrix

# Extra time is 1/3 of a match but scoring is slower (fatigue, caution):
# ET goal rates run ≈ 85-90% of the regulation per-minute rate.
ET_FRACTION = 30.0 / 90.0
ET_RATE = 0.88
PENALTY_FIRST_TEAM_WIN = 0.5  # shoot-outs are close to coin flips


def diff_distribution(matrix: np.ndarray) -> dict[int, float]:
    """P(home goals − away goals = k) from a score matrix."""
    n_h, n_a = matrix.shape
    out: dict[int, float] = {}
    for i in range(n_h):
        for j in range(n_a):
            out[i - j] = out.get(i - j, 0.0) + float(matrix[i, j])
    return out


def _et_and_pens(lam_a: float, lam_b: float, p_pens_a: float) -> tuple[float, float, float]:
    """(A wins in ET, level after ET, A advances overall) given level at 90'."""
    k = ET_FRACTION * ET_RATE
    m = score_matrix(max(lam_a * k, MIN_LAMBDA * k), max(lam_b * k, MIN_LAMBDA * k), rho=0.0)
    d = diff_distribution(m)
    win = sum(p for x, p in d.items() if x > 0)
    level = d.get(0, 0.0)
    return win, level, win + level * p_pens_a


def two_leg_tie(
    leg1: tuple[float, float],
    leg2: tuple[float, float],
    leg1_score: tuple[int, int] | None = None,
    rho: float = DIXON_COLES_RHO,
    pens_a: float = PENALTY_FIRST_TEAM_WIN,
) -> dict[str, float]:
    """P(team A advances).

    leg1: (λ_A, λ_B) with A at home.  leg2: (λ_B, λ_A) with B at home.
    leg1_score: (A goals, B goals) once the first leg is played.
    """
    if leg1_score is not None:
        d1 = {leg1_score[0] - leg1_score[1]: 1.0}
    else:
        d1 = diff_distribution(score_matrix(leg1[0], leg1[1], rho=rho))
    d2 = diff_distribution(score_matrix(leg2[0], leg2[1], rho=rho))  # B − A in leg 2

    p_reg = p_level = 0.0
    for x1, p1 in d1.items():
        for x2, p2 in d2.items():
            agg = x1 - x2
            if agg > 0:
                p_reg += p1 * p2
            elif agg == 0:
                p_level += p1 * p2
    # ET in the leg-2 venue: B at home → A's rate is leg2[1]
    et_win, et_level, et_adv = _et_and_pens(leg2[1], leg2[0], pens_a)
    return {
        "advance": p_reg + p_level * et_adv,
        "win_in_regulation": p_reg,
        "level_after_90": p_level,
        "win_in_extra_time": p_level * et_win,
        "penalties": p_level * et_level,
    }


def single_match_advance(
    lam_a: float, lam_b: float, rho: float = DIXON_COLES_RHO,
    pens_a: float = PENALTY_FIRST_TEAM_WIN,
) -> dict[str, float]:
    """One-off knockout (e.g. the final at a neutral venue): P(A lifts it)."""
    d = diff_distribution(score_matrix(lam_a, lam_b, rho=rho))
    p_reg = sum(p for x, p in d.items() if x > 0)
    p_level = d.get(0, 0.0)
    et_win, et_level, et_adv = _et_and_pens(lam_a, lam_b, pens_a)
    return {
        "advance": p_reg + p_level * et_adv,
        "win_in_regulation": p_reg,
        "level_after_90": p_level,
        "win_in_extra_time": p_level * et_win,
        "penalties": p_level * et_level,
    }


def live_second_leg(
    leg2: tuple[float, float],
    aggregate_a_minus_b: int,
    minute: float,
    pens_a: float = PENALTY_FIRST_TEAM_WIN,
) -> float:
    """P(A advances) during the second leg.

    leg2: pre-match (λ_B, λ_A) for the second leg (B at home).
    aggregate_a_minus_b: A's aggregate lead right now (both legs so far).
    """
    t = max(time_remaining_fraction(minute), 0.0)
    lb, la = max(leg2[0] * t, 1e-6), max(leg2[1] * t, 1e-6)
    d = diff_distribution(score_matrix(lb, la, rho=0.0))  # remaining B − A
    p_reg = sum(p for x, p in d.items() if aggregate_a_minus_b - x > 0)
    p_level = sum(p for x, p in d.items() if aggregate_a_minus_b - x == 0)
    _, _, et_adv = _et_and_pens(leg2[1], leg2[0], pens_a)
    return p_reg + p_level * et_adv
