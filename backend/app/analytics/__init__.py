"""Analytics engine package."""

from app.analytics.edge_calculator import calculate_edge, calculate_match_edges
from app.analytics.lineup_adjustment import (
    LineupAdjustmentModel,
    OnOffSplitModel,
    compute_goalkeeper_impact,
    compute_player_impact,
)
from app.analytics.live_model import calculate_live_prediction, live_market_probs
from app.analytics.player_impact import build_player_absences
from app.analytics.prediction_model import (
    asian_handicap_probabilities,
    calculate_schedule_modifier,
    estimate_expected_goals,
    league_scoring_baseline,
    predict_match,
    score_matrix,
    supremacy_to_probabilities,
    total_goals_probabilities,
    win_probability_to_supremacy,
)
from app.analytics.schedule_engine import (
    calculate_schedule_context,
    determine_motivation,
)

__all__ = [
    "LineupAdjustmentModel",
    "OnOffSplitModel",
    "asian_handicap_probabilities",
    "build_player_absences",
    "calculate_edge",
    "calculate_live_prediction",
    "calculate_match_edges",
    "calculate_schedule_context",
    "calculate_schedule_modifier",
    "compute_goalkeeper_impact",
    "compute_player_impact",
    "determine_motivation",
    "estimate_expected_goals",
    "league_scoring_baseline",
    "live_market_probs",
    "predict_match",
    "score_matrix",
    "supremacy_to_probabilities",
    "total_goals_probabilities",
    "win_probability_to_supremacy",
]
