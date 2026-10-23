"""Pydantic schemas for Polymarket data and edge calculations."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class MarketPrice(BaseModel):
    """Raw Polymarket market data."""
    match_id: str
    market_type: str  # home_win, draw, away_win, asian_handicap, total, btts
    polymarket_slug: str | None = None
    condition_id: str | None = None
    yes_price: float = Field(..., ge=0.01, le=0.99)
    no_price: float = Field(..., ge=0.01, le=0.99)
    volume: float = 0.0
    liquidity: float = 0.0
    fetched_at: datetime | None = None


class EdgeResult(BaseModel):
    """Result of edge calculation between model and market."""
    yes_edge: float
    no_edge: float
    yes_ev: float
    no_ev: float
    best_side: str  # YES or NO — or team name like "Arsenal"
    best_edge: float
    verdict: str  # STRONG BUY, BUY, LEAN, NO EDGE
    kelly_fraction: float = 0.0
    suggested_bet_pct: float = 0.0


class MarketEdge(BaseModel):
    """Full market analysis combining price and edge."""
    market_type: str
    line: float | None = None  # handicap or total line
    polymarket_yes: float | None = None
    polymarket_no: float | None = None
    yes_label: str | None = None  # e.g. "Arsenal -0.5"
    no_label: str | None = None
    model_probability: float
    edge: EdgeResult
