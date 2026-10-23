"""Backtest against REAL Polymarket prices.

For every settled EPL / La Liga match Polymarket listed (from Aug 2025), take
the Polymarket price ``minutes_before`` kick-off, the model's walk-forward
prediction, and Pinnacle's close, then:
  - compare log loss: model vs Polymarket vs Pinnacle
  - simulate flat 1-unit buys on Polymarket when model − price ≥ min_edge,
    paying ``slippage`` above the quoted price (spread / thin books)
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from loguru import logger

from app.analytics.prediction_model import predict_match
from app.backtest.engine import ModelConfig, _matches, _odds_index
from app.connectors import football_data as fd
from app.connectors import polymarket as pm
from app.connectors.team_names import to_canonical
from app.services.ratings import RatingConfig, build_ratings


@dataclass
class PolyRow:
    league: str
    slug: str
    kickoff: datetime
    home: str
    away: str
    hg: int
    ag: int
    model: dict[str, float]
    poly: dict[str, float]
    pinnacle: dict[str, float] = field(default_factory=dict)        # closing (look-ahead)
    pinnacle_early: dict[str, float] = field(default_factory=dict)  # pre-match, no look-ahead


def load_settled_matches(league: str, start: date, end: date) -> list[pm.MatchMarkets]:
    """Main + side events, open or closed (main markets can stay open for days
    awaiting resolution after the side markets close)."""
    events = []
    for closed in (True, False):
        events += pm.fetch_events(league, closed=closed, start=start, end=end, ttl_seconds=86400)
    unique = list({e["id"]: e for e in events}.values())
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=3)
    return [m for m in pm.parse_match_events(league, unique) if m.kickoff < cutoff]


def build_rows(
    leagues: list[str],
    start: date,
    end: date,
    minutes_before: int = 60,
    rating_cfg: RatingConfig | None = None,
    model_cfg: ModelConfig | None = None,
) -> list[PolyRow]:
    rating_cfg = rating_cfg or RatingConfig()
    model_cfg = model_cfg or ModelConfig()
    rows: list[PolyRow] = []
    for league in leagues:
        markets = load_settled_matches(league, start, end)
        prices = pm.snapshot_prices(markets, minutes_before=minutes_before)
        logger.disable("app")
        try:
            rows += _league_rows(league, markets, prices, rating_cfg, model_cfg)
        finally:
            logger.enable("app")
    return rows


def _season_of(d: date) -> int:
    return d.year if d.month >= 7 else d.year - 1


def _league_rows(
    league: str,
    markets: list[pm.MatchMarkets],
    prices: dict[str, dict[str, float]],
    rating_cfg: RatingConfig,
    model_cfg: ModelConfig,
) -> list[PolyRow]:
    rows: list[PolyRow] = []
    ratings_cache: dict[tuple[int, date], Any] = {}
    for mm in markets:
        p = prices.get(mm.slug, {})
        if not all(k in p for k in ("home_win", "draw", "away_win")):
            continue
        day = mm.kickoff.date()
        season = _season_of(day)
        current, prior = list(_matches(league, season)), list(_matches(league, season - 1))
        teams = {m.home for m in current}
        home, away = to_canonical(mm.home, teams), to_canonical(mm.away, teams)
        played = next(
            (m for m in current if m.home == home and m.away == away and m.is_result
             and abs((m.date - day).days) <= 1),
            None,
        )
        if played is None:
            continue
        key = (season, played.date)
        if key not in ratings_cache:
            ratings_cache[key] = build_ratings(current, prior, played.date, rating_cfg)
        r = ratings_cache[key]
        h, a = r.teams[home], r.teams[away]
        ha = model_cfg.home_advantage
        if ha is None:
            ha = r.home_advantage * model_cfg.ha_scale
        pred = predict_match(h["xgf"], h["xga"], a["xgf"], a["xga"], home_advantage=ha,
                             total_line=2.5, league_xg=r.league_xg,
                             baseline_goals=r.baseline_goals, rho=model_cfg.rho)
        row = PolyRow(
            league=league, slug=mm.slug, kickoff=mm.kickoff, home=home, away=away,
            hg=played.home_goals or 0, ag=played.away_goals or 0,
            model={"home_win": pred.home_win_prob, "draw": pred.draw_prob,
                   "away_win": pred.away_win_prob, "total:2.5": pred.over_prob,
                   "btts": pred.btts_prob},
            poly=p,
        )
        o = _odds_index(league, season).get((home, away))
        if o and o.close_home and o.close_draw and o.close_away:
            q = fd.devig(o.close_home, o.close_draw, o.close_away)
            row.pinnacle = {"home_win": q[0], "draw": q[1], "away_win": q[2]}
            if o.close_over25 and o.close_under25:
                row.pinnacle["total:2.5"] = fd.devig(o.close_over25, o.close_under25)[0]
        if o and o.early_home and o.early_draw and o.early_away:
            q = fd.devig(o.early_home, o.early_draw, o.early_away)
            row.pinnacle_early = {"home_win": q[0], "draw": q[1], "away_win": q[2]}
            if o.early_over25 and o.early_under25:
                row.pinnacle_early["total:2.5"] = fd.devig(o.early_over25, o.early_under25)[0]
        rows.append(row)
    return rows


def _hit(row: PolyRow, key: str) -> bool:
    if key == "home_win":
        return row.hg > row.ag
    if key == "draw":
        return row.hg == row.ag
    if key == "away_win":
        return row.ag > row.hg
    if key == "total:2.5":
        return row.hg + row.ag > 2.5
    if key == "btts":
        return row.hg > 0 and row.ag > 0
    raise KeyError(key)


def _ll(p: float, hit: bool) -> float:
    return -math.log(max(p if hit else 1 - p, 1e-6))


def compare(rows: list[PolyRow]) -> dict[str, Any]:
    """1X2 log loss (normalised three-way) for model / Polymarket / Pinnacle."""
    keys = ("home_win", "draw", "away_win")
    both = [r for r in rows if r.pinnacle]
    out: dict[str, Any] = {"matches": len(rows), "with_pinnacle": len(both)}

    def ll3(dist: dict[str, float], r: PolyRow) -> float:
        tot = sum(dist[k] for k in keys)
        k = next(k for k in keys if _hit(r, k))
        return -math.log(max(dist[k] / tot, 1e-6))

    out["ll_model"] = round(sum(ll3(r.model, r) for r in both) / max(len(both), 1), 4)
    out["ll_polymarket"] = round(sum(ll3(r.poly, r) for r in both) / max(len(both), 1), 4)
    out["ll_pinnacle"] = round(sum(ll3(r.pinnacle, r) for r in both) / max(len(both), 1), 4)
    overround = [sum(r.poly[k] for k in keys) for r in rows]
    out["poly_1x2_sum_avg"] = round(sum(overround) / len(overround), 4) if overround else None
    gap = [abs(r.poly[k] / sum(r.poly[j] for j in keys) - r.pinnacle[k])
           for r in both for k in keys]
    out["poly_vs_pinnacle_mean_abs_gap"] = round(sum(gap) / len(gap), 4) if gap else None
    return out


def simulate(
    rows: list[PolyRow],
    min_edge: float = 0.05,
    slippage: float = 0.01,
    blend: float = 1.0,
    keys: tuple[str, ...] = ("home_win", "draw", "away_win", "total:2.5", "btts"),
    use_pinnacle: bool = False,
    pinnacle_source: str = "close",
) -> dict[str, dict[str, float]]:
    """Flat 1-unit buys of YES or NO on Polymarket.

    prob = blend × model + (1 − blend) × Polymarket price (or Pinnacle when
    use_pinnacle — "copy the sharp book", the benchmark strategy).
    """
    stats: dict[str, dict[str, float]] = defaultdict(lambda: {"bets": 0, "pnl": 0.0})
    for r in rows:
        for k in keys:
            if k not in r.poly:
                continue
            price = r.poly[k]
            if not 0.02 <= price <= 0.98:
                continue
            if use_pinnacle:
                ref = r.pinnacle if pinnacle_source == "close" else r.pinnacle_early
                if k not in ref:
                    continue
                prob = ref[k]
            else:
                if k not in r.model:
                    continue
                prob = blend * r.model[k] + (1 - blend) * price
            hit = _hit(r, k)
            for side_prob, side_price, side_hit in ((prob, price, hit),
                                                    (1 - prob, 1 - price, not hit)):
                cost = min(side_price + slippage, 0.99)
                if side_prob - cost < min_edge:
                    continue
                s = stats[k]
                s["bets"] += 1
                s["pnl"] += (1.0 / cost - 1.0) if side_hit else -1.0
    for s in stats.values():
        s["pnl"] = round(s["pnl"], 2)
        s["roi"] = round(s["pnl"] / s["bets"], 4) if s["bets"] else 0.0
    return dict(stats)
