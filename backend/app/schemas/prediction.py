"""Pydantic schemas for model predictions."""

from pydantic import BaseModel


class MatchPrediction(BaseModel):
    """Full pre-match prediction for a single fixture.

    NBA → football:
      projected_spread  → projected_supremacy (expected home goals − away goals)
      home_win_prob     → home / draw / away (football is a three-way market)
      spread_cover_prob → ah_cover_prob (Asian handicap)
    """
    lambda_home: float              # expected home goals
    lambda_away: float              # expected away goals
    xgd_differential: float
    schedule_adjustment: float      # schedule + motivation, in goals of supremacy
    home_advantage: float = 0.30
    projected_supremacy: float      # positive = home favoured
    projected_total: float
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    ah_line: float | None = None    # home handicap, e.g. -0.75
    ah_cover_prob: float = 0.5      # fair price of home side of the handicap
    total_line: float | None = None
    over_prob: float = 0.5          # fair price of the over
    btts_prob: float = 0.5          # both teams to score
    most_likely_score: str = "1-1"
    confidence: str = "MEDIUM"


class LivePrediction(BaseModel):
    """In-play prediction: current score + Poisson goals for the time left.

    Unlike the NBA blend (score margin + scaled pre-game margin with a shrinking
    logistic), football goals arrive as a Poisson process, so the remaining
    match is simply the pre-match expected goals scaled by time remaining.
    """
    home_win_prob: float
    draw_prob: float
    away_win_prob: float
    pre_match_home_win_prob: float
    live_lambda_home: float         # expected home goals in the time remaining
    live_lambda_away: float
    home_score: int
    away_score: int
    minute: float
    time_remaining_pct: float       # 1.0 = kick-off, 0.0 = full time
    is_final: bool = False
    result: str | None = None       # H, D, A once final


class DataQuality(BaseModel):
    """Data quality indicators for a match analysis."""
    ratings_freshness: str = "FRESH"  # FRESH, STALE, MISSING
    injury_freshness: str = "FRESH"
    price_freshness: str = "FRESH"
    cross_source_validated: bool = True
    warnings: list[str] = []
