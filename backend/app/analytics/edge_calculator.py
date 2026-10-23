"""
Edge Calculator — compares model probability against Polymarket prices.
Calculates EV, Kelly criterion, and verdict for each market.

Unchanged from the NBA version except calculate_match_edges: football
Polymarket events list three separate YES/NO markets (home win, draw, away
win) plus handicap, total and BTTS, instead of moneyline / spread / total.
"""

from __future__ import annotations

from loguru import logger

from app.schemas.market import EdgeResult


def calculate_edge(
    model_probability: float,
    market_price: float,
) -> EdgeResult:
    """
    Compare the model's probability against a Polymarket YES price.

    For Asian handicap / whole-line totals pass the model's fair price
    (W / (W + L)), which already accounts for pushes refunding the stake.
    """
    model_probability = max(0.001, min(0.999, model_probability))
    market_price = max(0.01, min(0.99, market_price))

    # YES side
    yes_edge = model_probability - market_price
    decimal_odds_yes = 1.0 / market_price
    yes_ev = (model_probability * decimal_odds_yes) - 1.0

    # NO side
    no_model_prob = 1.0 - model_probability
    no_price = 1.0 - market_price
    no_edge = no_model_prob - no_price
    decimal_odds_no = 1.0 / no_price
    no_ev = (no_model_prob * decimal_odds_no) - 1.0

    kelly_yes = _kelly_fraction(model_probability, decimal_odds_yes)
    kelly_no = _kelly_fraction(no_model_prob, decimal_odds_no)

    # Quarter Kelly for conservative sizing
    quarter_kelly_yes = kelly_yes * 0.25
    quarter_kelly_no = kelly_no * 0.25

    best_side = "YES" if yes_edge > no_edge else "NO"
    best_edge = max(yes_edge, no_edge)
    best_kelly = quarter_kelly_yes if best_side == "YES" else quarter_kelly_no

    verdict = _get_verdict(best_edge)

    result = EdgeResult(
        yes_edge=round(yes_edge, 4),
        no_edge=round(no_edge, 4),
        yes_ev=round(yes_ev, 4),
        no_ev=round(no_ev, 4),
        best_side=best_side,
        best_edge=round(best_edge, 4),
        verdict=verdict,
        kelly_fraction=round(best_kelly, 4),
        suggested_bet_pct=round(best_kelly * 100, 2),
    )

    logger.debug(
        f"Edge calc: model={model_probability:.3f}, market={market_price:.3f}, "
        f"best={best_side} edge={best_edge:.4f}, verdict={verdict}"
    )

    return result


def _kelly_fraction(probability: float, decimal_odds: float) -> float:
    """Kelly% = (p * b - q) / b, b = decimal_odds - 1, q = 1 - p."""
    if decimal_odds <= 1.0:
        return 0.0
    b = decimal_odds - 1.0
    q = 1.0 - probability
    return max(0.0, (probability * b - q) / b)


def _get_verdict(edge: float) -> str:
    """
    - STRONG BUY: >= 12% edge
    - BUY: >= 6% edge
    - LEAN: >= 3% edge
    - NO EDGE: < 3% edge
    """
    if edge >= 0.12:
        return "STRONG BUY"
    if edge >= 0.06:
        return "BUY"
    if edge >= 0.03:
        return "LEAN"
    return "NO EDGE"


VERDICT_ORDER = ("NO EDGE", "LEAN", "BUY", "STRONG BUY")


def cap_verdict(edge: EdgeResult, max_verdict: str = "LEAN") -> EdgeResult:
    """Return ``edge`` with its verdict capped at ``max_verdict``.

    Used when the model's inputs are known to be unreliable (stale squad prior,
    unconfirmed line-ups): the numbers stay visible, the signal is downgraded.
    """
    if edge.verdict not in VERDICT_ORDER or max_verdict not in VERDICT_ORDER:
        return edge
    if VERDICT_ORDER.index(edge.verdict) <= VERDICT_ORDER.index(max_verdict):
        return edge
    return edge.model_copy(update={"verdict": max_verdict})


MATCH_MARKETS = ("home_win", "draw", "away_win", "asian_handicap", "total", "btts")


def calculate_match_edges(
    home_win_prob: float,
    draw_prob: float,
    away_win_prob: float,
    ah_cover_prob: float,
    over_prob: float,
    btts_prob: float,
    prices: dict[str, float | None],
) -> dict[str, EdgeResult | None]:
    """
    Edges for every football market type of a match.

    prices: market → YES price, keys from MATCH_MARKETS (missing / None skipped).
    """
    model = {
        "home_win": home_win_prob,
        "draw": draw_prob,
        "away_win": away_win_prob,
        "asian_handicap": ah_cover_prob,
        "total": over_prob,
        "btts": btts_prob,
    }
    edges: dict[str, EdgeResult | None] = {}
    for market in MATCH_MARKETS:
        price = prices.get(market)
        edges[market] = calculate_edge(model[market], price) if price is not None else None
    return edges


def remove_overround(prices: list[float]) -> list[float]:
    """Normalise a set of mutually exclusive YES prices (e.g. 1X2) to sum to 1.

    The three Polymarket 1X2 markets usually sum to 1.01-1.04; comparing the
    model against raw prices overstates the NO-side edge on every leg.
    """
    total = sum(prices)
    return [p / total for p in prices] if total > 0 else prices
