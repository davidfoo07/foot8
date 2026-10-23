"""
Prediction Model — football translation of the CourtEdge NBA V2 model.

NBA V2                                  →  Football
───────────────────────────────────────────────────────────────────────────
NRtg differential → projected margin    →  xG attack × defence → expected goals
                                           per side (λ_home, λ_away)
+ home court (3.0 pts)                  →  + home advantage (0.30 goals of
                                           supremacy)
+ schedule / motivation (points)        →  + schedule / motivation (goals)
margin → logistic win prob (σ=5.8)      →  Dixon-Coles adjusted Poisson score
                                           matrix → home / draw / away
spread cover: Normal(margin, σ=11.5)    →  Asian handicap settled on the score
                                           matrix (half, whole and quarter lines)
total: pace-adjusted ORtg×DRtg, σ=19    →  total goals from the same matrix
                                           (Poisson already carries the variance)
league scoring baseline (K league games)→  league goals-per-match + home
                                           advantage, blended the same way
OT adjustment / late-season bias        →  dropped (no overtime in league play;
                                           no established month effect)
                                        +  BTTS and correct-score come for free

Why Poisson instead of a Normal margin: football scores are small integers, a
draw is a third outcome, and the variance of a match is set by its expected
goals.  A 0.3-goal edge means far more in a 2.2-goal match than a 3.4-goal one.
"""

from __future__ import annotations

import math

import numpy as np
from loguru import logger

from app.schemas.prediction import MatchPrediction

# ─── Calibration Constants ──────────────────────────────────────────
# HOME_ADVANTAGE_GOALS / LEAGUE_AVG_* are fallbacks only: the live path and
# the backtest use the league's own home−away goals (ratings.build_ratings).
# DIXON_COLES_RHO is backtest-tuned (scripts/tune.py).

HOME_ADVANTAGE_GOALS = 0.30        # goals of supremacy; top-5 leagues ≈ 0.25-0.35
                                    # since 2021 (pre-COVID ≈ 0.35-0.40)
LEAGUE_AVG_GOALS = 2.75            # goals per match, top-5 league average (fallback)
LEAGUE_AVG_XG = LEAGUE_AVG_GOALS / 2.0  # xG per team per 90 (fallback)
DIXON_COLES_RHO = -0.05            # low-score dependence; backtest-tuned (EPL+La Liga)
                                    # (inflates 0-0 / 1-1, deflates 1-0 / 0-1)
MAX_GOALS = 10                     # score matrix is 0..MAX_GOALS per side
MIN_LAMBDA = 0.05                  # floor on expected goals after adjustments

# League-matches at which the current season's own scoring level gets 50%
# weight (≈ 6 rounds of a 20-team league).  Analog of LEAGUE_LEVEL_K_GAMES.
LEAGUE_LEVEL_K_MATCHES = 60.0
SEASON_SCORING_DRIFT = 0.0         # goals/match; league level has no reliable trend

# Motivation impact on supremacy (goals).  The NBA values scaled by the ratio
# of home advantages (0.30 / 3.0).
MOTIVATION_SUPREMACY_ADJUSTMENTS = {
    "DESPERATE": +0.15,      # relegation six-pointer / last-chance title push
    "FIGHTING": +0.05,       # European places, title race still live
    "NEUTRAL": 0.0,
    "REST_EXPECTED": -0.20,  # dead rubber: rotation, "on the beach"
}


# ─── Schedule Adjustments (goals of supremacy) ─────────────────────
SCHEDULE_ADJUSTMENTS = {
    "short_rest": -0.10,       # < 72h since last match (NBA b2b)
    "3_in_7": -0.15,           # 3rd match in 7 days (NBA 3-in-4)
    "4_in_10": -0.20,          # 4th match in 10 days (NBA 4-in-6)
    "europe_midweek": -0.05,   # European tie midweek, league match at weekend
    "rest_advantage": 0.04,    # per extra rest day (max +0.12)
    "home_advantage": HOME_ADVANTAGE_GOALS,
    "travel_long": -0.08,      # > 1500 km away trip
    "travel_medium": -0.03,    # > 500 km
}
REST_ADVANTAGE_CAP = 0.12
REST_DAYS_CAP = 7  # beyond a week, extra rest is no longer an advantage


def calculate_schedule_modifier(
    is_short_rest: bool = False,
    is_3_in_7: bool = False,
    is_4_in_10: bool = False,
    played_europe_midweek: bool = False,
    rest_days: int = 6,
    opponent_rest_days: int = 6,
    travel_distance_km: float = 0.0,
) -> float:
    """
    Schedule-based supremacy adjustment for a team, in goals.
    Negative = disadvantage.
    """
    modifier = 0.0

    # Fixture congestion (take the worst applicable)
    if is_4_in_10:
        modifier += SCHEDULE_ADJUSTMENTS["4_in_10"]
    elif is_3_in_7:
        modifier += SCHEDULE_ADJUSTMENTS["3_in_7"]
    elif is_short_rest:
        modifier += SCHEDULE_ADJUSTMENTS["short_rest"]

    if played_europe_midweek:
        modifier += SCHEDULE_ADJUSTMENTS["europe_midweek"]

    # Rest advantage (capped: two weeks off is not better than one)
    rest_diff = min(rest_days, REST_DAYS_CAP) - min(opponent_rest_days, REST_DAYS_CAP)
    if rest_diff > 0:
        modifier += min(rest_diff * SCHEDULE_ADJUSTMENTS["rest_advantage"], REST_ADVANTAGE_CAP)

    if travel_distance_km > 1500:
        modifier += SCHEDULE_ADJUSTMENTS["travel_long"]
    elif travel_distance_km > 500:
        modifier += SCHEDULE_ADJUSTMENTS["travel_medium"]

    return round(modifier, 2)


def _calculate_motivation_modifier(home_motivation: str, away_motivation: str) -> float:
    """Motivation-based supremacy adjustment for the home side."""
    home_mod = MOTIVATION_SUPREMACY_ADJUSTMENTS.get(home_motivation, 0.0)
    away_mod = MOTIVATION_SUPREMACY_ADJUSTMENTS.get(away_motivation, 0.0)
    return home_mod - away_mod


# ─── Score matrix ───────────────────────────────────────────────────

def _poisson_pmf(lam: float, max_goals: int = MAX_GOALS) -> np.ndarray:
    k = np.arange(max_goals + 1)
    log_pmf = k * math.log(lam) - lam - np.array([math.lgamma(i + 1) for i in k])
    return np.exp(log_pmf)


def score_matrix(
    lambda_home: float,
    lambda_away: float,
    rho: float = DIXON_COLES_RHO,
    max_goals: int = MAX_GOALS,
) -> np.ndarray:
    """P(home = i, away = j) for i, j in 0..max_goals.

    Independent Poisson with the Dixon-Coles (1997) low-score correction,
    renormalised so the truncated matrix sums to 1.
    """
    lh, la = max(lambda_home, MIN_LAMBDA), max(lambda_away, MIN_LAMBDA)
    m = np.outer(_poisson_pmf(lh, max_goals), _poisson_pmf(la, max_goals))
    if rho:
        m[0, 0] *= max(0.0, 1.0 - lh * la * rho)
        m[0, 1] *= max(0.0, 1.0 + lh * rho)
        m[1, 0] *= max(0.0, 1.0 + la * rho)
        m[1, 1] *= max(0.0, 1.0 - rho)
    return m / m.sum()


def outcome_probabilities(matrix: np.ndarray) -> tuple[float, float, float]:
    """(home win, draw, away win) from a score matrix."""
    home = float(np.tril(matrix, -1).sum())
    draw = float(np.trace(matrix))
    away = float(np.triu(matrix, 1).sum())
    return home, draw, away


def _line_components(line: float) -> list[float]:
    """Split a quarter line into its two half-stake lines (−0.75 → −0.5, −1.0)."""
    if round(line * 4) % 2 != 0:
        return [line - 0.25, line + 0.25]
    return [line]


def _settle(values: np.ndarray, matrix: np.ndarray, line: float) -> tuple[float, float, float]:
    """(win, push, lose) probability of a bet that wins when value + line > 0.

    Quarter lines settle as two half stakes, so each component counts 50%.
    """
    comps = _line_components(line)
    win = push = lose = 0.0
    for c in comps:
        adj = values + c
        win += float(matrix[adj > 1e-9].sum())
        push += float(matrix[np.abs(adj) <= 1e-9].sum())
        lose += float(matrix[adj < -1e-9].sum())
    n = len(comps)
    return win / n, push / n, lose / n


def _fair_price(win: float, lose: float) -> float:
    """Break-even YES price for a bet that refunds on a push: W / (W + L)."""
    return win / (win + lose) if win + lose > 0 else 0.5


def asian_handicap_probabilities(matrix: np.ndarray, home_line: float) -> dict[str, float]:
    """Home side of an Asian handicap, e.g. home_line = −0.75 means home gives 0.75.

    Returns win / push / lose and fair_prob — the price at which the bet has
    zero EV, which is what gets compared against a Polymarket YES price.
    """
    n = matrix.shape[0]
    margin = np.subtract.outer(np.arange(n), np.arange(n)).astype(float)
    win, push, lose = _settle(margin, matrix, home_line)
    return {"win": win, "push": push, "lose": lose, "fair_prob": _fair_price(win, lose)}


def total_goals_probabilities(matrix: np.ndarray, line: float) -> dict[str, float]:
    """Over side of a goals total (2.5, 2.25, 3.0, ...)."""
    n = matrix.shape[0]
    total = np.add.outer(np.arange(n), np.arange(n)).astype(float)
    # Over wins when total − line > 0, i.e. value = total, offset = −line
    win, push, lose = _settle(total, matrix, -line)
    return {"win": win, "push": push, "lose": lose, "fair_prob": _fair_price(win, lose)}


def btts_probability(matrix: np.ndarray) -> float:
    """P(both teams score)."""
    return float(matrix[1:, 1:].sum())


# ─── Expected goals ─────────────────────────────────────────────────

def estimate_expected_goals(
    home_xgf: float,
    home_xga: float,
    away_xgf: float,
    away_xga: float,
    league_xg: float | None = None,
    baseline_goals: float | None = None,
) -> tuple[float, float]:
    """
    Neutral-venue expected goals per side (before home advantage):

      attack_h = home_xgf / league_xg,  defence_a = away_xga / league_xg
      λ_home   = (baseline_goals / 2) × attack_h × defence_a
      λ_away   = (baseline_goals / 2) × attack_a × defence_h

    The football version of estimate_total's multiplicative efficiency term:
    a strong attack meeting a leaky defence compounds rather than adds.
    league_xg is the league average of the ratings passed in, so ratings only
    say how far a team sits from average; the level comes from baseline_goals
    (league_scoring_baseline).  Pace has no separate role — tempo is already
    in each team's xG per 90.
    """
    league_xg = league_xg or LEAGUE_AVG_XG
    baseline_goals = baseline_goals or 2.0 * league_xg
    base = baseline_goals / 2.0

    lambda_home = base * (home_xgf / league_xg) * (away_xga / league_xg)
    lambda_away = base * (away_xgf / league_xg) * (home_xga / league_xg)
    return lambda_home, lambda_away


def predict_match(
    home_adj_xgf: float,
    home_adj_xga: float,
    away_adj_xgf: float,
    away_adj_xga: float,
    home_schedule_mod: float = 0.0,
    away_schedule_mod: float = 0.0,
    home_advantage: float = HOME_ADVANTAGE_GOALS,
    ah_line: float | None = None,
    total_line: float | None = None,
    home_motivation: str = "NEUTRAL",
    away_motivation: str = "NEUTRAL",
    league_xg: float | None = None,
    baseline_goals: float | None = None,
    rho: float = DIXON_COLES_RHO,
    confidence: str = "MEDIUM",
) -> MatchPrediction:
    """
    Core football prediction model:
    1. Attack × defence → neutral expected goals per side
    2. Add home advantage, schedule, motivation as goals of supremacy
       (split half to each side, so the expected total is unchanged)
    3. Dixon-Coles Poisson score matrix
    4. Home / draw / away, Asian handicap, total goals, BTTS from the matrix

    league_xg / baseline_goals / home_advantage come from
    league_scoring_baseline(); without them the static league averages apply.
    """
    home_xgd = home_adj_xgf - home_adj_xga
    away_xgd = away_adj_xgf - away_adj_xga
    xgd_diff = home_xgd - away_xgd

    # Step 1: neutral expected goals
    lambda_home, lambda_away = estimate_expected_goals(
        home_adj_xgf, home_adj_xga, away_adj_xgf, away_adj_xga,
        league_xg=league_xg, baseline_goals=baseline_goals,
    )

    # Step 2: supremacy modifiers
    schedule_adj = home_schedule_mod - away_schedule_mod
    motivation_adj = _calculate_motivation_modifier(home_motivation, away_motivation)
    supremacy_adj = home_advantage + schedule_adj + motivation_adj
    lambda_home = max(MIN_LAMBDA, lambda_home + supremacy_adj / 2.0)
    lambda_away = max(MIN_LAMBDA, lambda_away - supremacy_adj / 2.0)

    # Steps 3-4: score matrix → markets
    prediction = predict_from_lambdas(
        lambda_home, lambda_away, ah_line=ah_line, total_line=total_line, rho=rho,
        confidence=confidence, home_advantage=home_advantage,
        xgd_differential=xgd_diff, schedule_adjustment=schedule_adj + motivation_adj,
    )
    home_win, draw = prediction.home_win_prob, prediction.draw_prob
    away_win = prediction.away_win_prob
    ah_cover, over, btts = prediction.ah_cover_prob, prediction.over_prob, prediction.btts_prob

    logger.info(
        f"Prediction: xGD diff={xgd_diff:+.2f}, λ={lambda_home:.2f}-{lambda_away:.2f} "
        f"(sched={schedule_adj:+.2f}, motiv={motivation_adj:+.2f}), "
        f"1X2={home_win:.3f}/{draw:.3f}/{away_win:.3f}, "
        f"AH {ah_line}={ah_cover:.3f}, O{total_line}={over:.3f}, BTTS={btts:.3f}"
    )

    return prediction


def predict_from_lambdas(
    lambda_home: float,
    lambda_away: float,
    ah_line: float | None = None,
    total_line: float | None = None,
    rho: float = DIXON_COLES_RHO,
    confidence: str = "MEDIUM",
    home_advantage: float = 0.0,
    xgd_differential: float = 0.0,
    schedule_adjustment: float = 0.0,
) -> MatchPrediction:
    """Score matrix and every market from final expected goals.

    Shared by predict_match (xG ratings) and the cross-league model
    (global_ratings), which produces expected goals directly.
    """
    matrix = score_matrix(lambda_home, lambda_away, rho=rho)
    home_win, draw, away_win = outcome_probabilities(matrix)
    ah_cover = (asian_handicap_probabilities(matrix, ah_line)["fair_prob"]
                if ah_line is not None else 0.5)
    over = (total_goals_probabilities(matrix, total_line)["fair_prob"]
            if total_line is not None else 0.5)
    hi, ai = np.unravel_index(int(np.argmax(matrix)), matrix.shape)
    return MatchPrediction(
        lambda_home=round(lambda_home, 3),
        lambda_away=round(lambda_away, 3),
        xgd_differential=round(xgd_differential, 2),
        schedule_adjustment=round(schedule_adjustment, 2),
        home_advantage=home_advantage,
        projected_supremacy=round(lambda_home - lambda_away, 2),
        projected_total=round(lambda_home + lambda_away, 2),
        home_win_prob=round(home_win, 3),
        draw_prob=round(draw, 3),
        away_win_prob=round(away_win, 3),
        ah_line=ah_line,
        ah_cover_prob=round(ah_cover, 3),
        total_line=total_line,
        over_prob=round(over, 3),
        btts_prob=round(btts_probability(matrix), 3),
        most_likely_score=f"{hi}-{ai}",
        confidence=confidence,
    )


def league_scoring_baseline(
    current: dict[str, float],
    prior: dict[str, float],
    blended: dict[str, dict[str, float]],
    k_matches: float = LEAGUE_LEVEL_K_MATCHES,
    drift: float = SEASON_SCORING_DRIFT,
) -> dict[str, float]:
    """League goal level and home advantage for predict_match.

    current / prior: league totals {"matches", "home_goals", "away_goals"} for
    this season and last.  blended: per-team ratings fed to the model
    ({"xgf", "xga"}).

    Returns:
      league_xg: average of the blended ratings (what attack/defence are
        measured against).
      baseline_goals: w × this season's goals/match + (1 − w) × (last season's
        + drift), w = matches / (matches + k_matches).
      home_advantage: (home − away) goals per match, blended with the same w.
      level_weight: w.
    """
    xgs = [float(r[k]) for r in blended.values() for k in ("xgf", "xga") if r.get(k) is not None]
    league_xg = sum(xgs) / len(xgs) if xgs else LEAGUE_AVG_XG

    def level(row: dict[str, float]) -> tuple[float, float] | None:
        n = float(row.get("matches", 0) or 0)
        if n <= 0:
            return None
        hg, ag = float(row.get("home_goals", 0)), float(row.get("away_goals", 0))
        return (hg + ag) / n, (hg - ag) / n

    prior_level = level(prior) or (LEAGUE_AVG_GOALS, HOME_ADVANTAGE_GOALS)
    prior_goals, prior_ha = prior_level[0] + drift, prior_level[1]

    cur = level(current)
    matches = float(current.get("matches", 0) or 0)
    w = matches / (matches + k_matches) if cur is not None and k_matches > 0 else 0.0

    goals = w * cur[0] + (1 - w) * prior_goals if cur and w > 0 else prior_goals
    ha = w * cur[1] + (1 - w) * prior_ha if cur and w > 0 else prior_ha
    return {
        "league_xg": round(league_xg, 3),
        "baseline_goals": round(goals, 3),
        "home_advantage": round(ha, 3),
        "level_weight": round(w, 3),
    }


def supremacy_to_probabilities(
    supremacy: float,
    total: float = LEAGUE_AVG_GOALS,
    rho: float = DIXON_COLES_RHO,
) -> tuple[float, float, float]:
    """Expected supremacy + total → (home, draw, away).  Analog of margin_to_win_probability."""
    total = max(total, 2 * MIN_LAMBDA + 1e-6)
    supremacy = max(-total + 2 * MIN_LAMBDA, min(total - 2 * MIN_LAMBDA, supremacy))
    lh, la = (total + supremacy) / 2.0, (total - supremacy) / 2.0
    return outcome_probabilities(score_matrix(lh, la, rho=rho))


def win_probability_to_supremacy(
    home_prob: float,
    total: float = LEAGUE_AVG_GOALS,
    rho: float = DIXON_COLES_RHO,
) -> float:
    """Invert supremacy_to_probabilities on the home-win probability (bisection).

    Useful for reading a market's implied supremacy from its home-win price.
    """
    if home_prob <= 0 or home_prob >= 1:
        return 0.0
    lo, hi = -total + 2 * MIN_LAMBDA, total - 2 * MIN_LAMBDA
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if supremacy_to_probabilities(mid, total, rho)[0] < home_prob:
            lo = mid
        else:
            hi = mid
    return round((lo + hi) / 2.0, 3)
