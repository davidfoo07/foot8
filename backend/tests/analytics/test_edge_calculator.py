"""Tests for the edge calculator."""

import pytest

from app.analytics.edge_calculator import (
    calculate_edge,
    calculate_match_edges,
    cap_verdict,
    remove_overround,
)


def test_yes_edge_and_kelly():
    e = calculate_edge(0.60, 0.45)
    assert e.best_side == "YES"
    assert e.best_edge == pytest.approx(0.15)
    assert e.verdict == "STRONG BUY"
    # full Kelly (0.6×(1/0.45−1)−0.4)/(1/0.45−1) = 0.2727 → quarter
    assert e.kelly_fraction == pytest.approx(0.0682, abs=1e-4)


def test_no_side():
    e = calculate_edge(0.20, 0.30)
    assert e.best_side == "NO"
    assert e.verdict == "BUY"


def test_cap_verdict():
    assert cap_verdict(calculate_edge(0.60, 0.45)).verdict == "LEAN"


def test_match_edges_skip_missing_prices():
    edges = calculate_match_edges(0.5, 0.27, 0.23, 0.55, 0.48, 0.52,
                                  {"home_win": 0.45, "draw": 0.28, "total": None})
    assert edges["home_win"] is not None and edges["draw"] is not None
    assert edges["total"] is None and edges["btts"] is None


def test_remove_overround():
    assert sum(remove_overround([0.48, 0.28, 0.27])) == pytest.approx(1.0)
