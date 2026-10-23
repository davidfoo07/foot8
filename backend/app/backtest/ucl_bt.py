"""Champions League walk-forward backtest for the cross-league global model.

Refit the global model before every UCL match date (no look-ahead), price
1X2 and O/U 2.5, and score against results — and, from Aug 2025, against
the real Polymarket price 60 minutes before kick-off.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from loguru import logger

from app.analytics.global_ratings import EURO_GROUP, GlobalConfig, GlobalRatings, fit_global
from app.analytics.prediction_model import outcome_probabilities, score_matrix
from app.analytics.prediction_model import total_goals_probabilities as totals
from app.connectors import espn
from app.connectors import polymarket as pm
from app.services.ucl import UCL, _is_final, match_fixture

EVAL_START = date(2024, 9, 1)   # new 36-team league-phase format


@dataclass
class UclRow:
    event_id: str
    day: date
    home: str
    away: str
    hg: int
    ag: int
    p: tuple[float, float, float]
    p_over: float
    lam: tuple[float, float]
    poly: dict[str, float] = field(default_factory=dict)


def walk_forward(
    history: list[espn.EspnMatch],
    cfg: GlobalConfig | None = None,
    start: date = EVAL_START,
    end: date | None = None,
    competitions: tuple[str, ...] = (UCL,),
) -> list[UclRow]:
    targets = [m for m in history if m.competition in competitions and m.is_result
               and m.kickoff.date() >= start and (end is None or m.kickoff.date() <= end)]
    by_day: dict[date, list[espn.EspnMatch]] = defaultdict(list)
    for m in targets:
        by_day[m.kickoff.date()].append(m)

    rows: list[UclRow] = []
    prev: GlobalRatings | None = None
    for day in sorted(by_day):
        ratings = fit_global(history, day, cfg, warm_start=prev)
        prev = ratings
        for m in by_day[day]:
            lh, la = ratings.expected_goals(m.home_id, m.away_id, EURO_GROUP,
                                            neutral=_is_final(m))
            mat = score_matrix(lh, la)
            rows.append(UclRow(
                event_id=m.event_id, day=day, home=m.home_name, away=m.away_name,
                hg=m.home_goals or 0, ag=m.away_goals or 0,
                p=outcome_probabilities(mat), p_over=totals(mat, 2.5)["fair_prob"],
                lam=(lh, la),
            ))
    return rows


def attach_polymarket(rows: list[UclRow], history: list[espn.EspnMatch],
                      minutes_before: int = 60) -> None:
    """Fill row.poly with Polymarket prices for matches Polymarket listed."""
    if not rows:
        return
    start, end = max(min(r.day for r in rows), date(2025, 7, 1)), max(r.day for r in rows)
    if start > end:
        return
    events = []
    for closed in (True, False):
        events += pm.fetch_events("UCL", closed, start, end + timedelta(days=1),
                                  ttl_seconds=86400)
    unique = list({e["id"]: e for e in events}.values())
    markets = pm.parse_match_events("UCL", unique)
    prices = pm.snapshot_prices(markets, minutes_before=minutes_before)
    ucl = [m for m in history if m.competition == UCL]
    by_event = {r.event_id: r for r in rows}
    for mm in markets:
        fx = match_fixture(mm.home, mm.away, mm.kickoff, ucl)
        if fx is not None and fx.event_id in by_event and mm.slug in prices:
            by_event[fx.event_id].poly = prices[mm.slug]


def _ll3(p: tuple[float, ...], k: int) -> float:
    return -math.log(max(p[k] / sum(p), 1e-6))


def _outcome(r: UclRow) -> int:
    return 0 if r.hg > r.ag else (1 if r.hg == r.ag else 2)


def summarise(rows: list[UclRow]) -> dict[str, Any]:
    if not rows:
        return {}
    n = len(rows)
    out: dict[str, Any] = {
        "matches": n,
        "ll_model": round(sum(_ll3(r.p, _outcome(r)) for r in rows) / n, 4),
        "ll_uniform_prior": round(sum(_ll3((0.45, 0.25, 0.30), _outcome(r)) for r in rows) / n, 4),
        "ou_ll_model": round(sum(-math.log(max(r.p_over if r.hg + r.ag > 2.5 else 1 - r.p_over,
                                                1e-6)) for r in rows) / n, 4),
        "goals_projected": round(sum(sum(r.lam) for r in rows) / n, 3),
        "goals_actual": round(sum(r.hg + r.ag for r in rows) / n, 3),
        "home_model": round(sum(r.p[0] for r in rows) / n, 3),
        "home_rate": round(sum(_outcome(r) == 0 for r in rows) / n, 3),
    }
    both = [r for r in rows if all(k in r.poly for k in ("home_win", "draw", "away_win"))]
    if both:
        out["with_polymarket"] = len(both)
        model_ll = sum(_ll3(r.p, _outcome(r)) for r in both) / len(both)
        out["ll_model_on_poly_set"] = round(model_ll, 4)
        out["ll_polymarket"] = round(sum(
            _ll3((r.poly["home_win"], r.poly["draw"], r.poly["away_win"]), _outcome(r))
            for r in both) / len(both), 4)
    return out


def simulate(rows: list[UclRow], min_edge: float = 0.05, slippage: float = 0.01) -> dict:
    """Flat 1-unit Polymarket buys where model − (price + slippage) ≥ min_edge."""
    bets, pnl = 0, 0.0
    for r in rows:
        k_out = _outcome(r)
        for k, key in enumerate(("home_win", "draw", "away_win")):
            price = r.poly.get(key)
            if price is None or not 0.02 <= price <= 0.98:
                continue
            for prob, cost, hit in ((r.p[k], price + slippage, k == k_out),
                                    (1 - r.p[k], 1 - price + slippage, k != k_out)):
                if prob - cost >= min_edge:
                    bets += 1
                    pnl += (1 / cost - 1) if hit else -1.0
    logger.debug(f"simulate: {bets} bets")
    return {"bets": bets, "pnl": round(pnl, 2), "roi": round(pnl / bets, 4) if bets else 0.0}
