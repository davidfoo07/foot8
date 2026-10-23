"""Walk-forward backtest: rate every team as of each match date, price the
match, and compare against the result and Pinnacle's closing odds.

Pinnacle's close is the benchmark.  Beating it on log loss is very hard; the
realistic goal is a model that (a) is close to it, and (b) adds information
when blended with it — which is what the live edge calculation relies on,
because Polymarket prices track the sharp books.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from loguru import logger

from app.analytics.prediction_model import predict_match
from app.connectors import football_data as fd
from app.connectors import understat as us
from app.connectors.team_names import to_canonical
from app.services.ratings import RatingConfig, build_ratings


@dataclass
class ModelConfig:
    home_advantage: float | None = None  # None = league-estimated (home − away goals)
    ha_scale: float = 1.0                # multiplier on the league estimate
    rho: float = -0.05
    blend_market: float = 0.0            # 0 = pure model; used only for the blended score


@dataclass
class BacktestResult:
    rows: list[dict[str, Any]] = field(default_factory=list)


@lru_cache(maxsize=64)
def _matches(league: str, season: int) -> tuple[us.MatchRecord, ...]:
    return tuple(us.load_matches(league, season))


@lru_cache(maxsize=64)
def _odds_index(league: str, season: int) -> dict[tuple[str, str], fd.OddsRecord]:
    teams = {m.home for m in _matches(league, season)}
    idx: dict[tuple[str, str], fd.OddsRecord] = {}
    for o in fd.load_season(league, season):
        h, a = to_canonical(o.home, teams), to_canonical(o.away, teams)
        if h and a:
            idx[(h, a)] = o
    return idx


def run_backtest(
    leagues: list[str],
    seasons: list[int],
    rating_cfg: RatingConfig | None = None,
    model_cfg: ModelConfig | None = None,
    skip_first_days: int = 0,
) -> BacktestResult:
    rating_cfg = rating_cfg or RatingConfig()
    model_cfg = model_cfg or ModelConfig()
    logger.disable("app")
    result = BacktestResult()
    try:
        for league in leagues:
            for season in seasons:
                _run_season(league, season, rating_cfg, model_cfg, skip_first_days, result)
    finally:
        logger.enable("app")
    return result


def _run_season(
    league: str,
    season: int,
    rating_cfg: RatingConfig,
    model_cfg: ModelConfig,
    skip_first_days: int,
    result: BacktestResult,
) -> None:
    current = list(_matches(league, season))
    prior = list(_matches(league, season - 1))
    odds = _odds_index(league, season)
    played = [m for m in current if m.is_result]
    if not played:
        return
    season_start = played[0].date

    by_date: dict[Any, list[us.MatchRecord]] = defaultdict(list)
    for m in played:
        by_date[m.date].append(m)

    for day in sorted(by_date):
        if (day - season_start).days < skip_first_days:
            continue
        ratings = build_ratings(current, prior, day, rating_cfg)
        ha = model_cfg.home_advantage
        if ha is None:
            ha = ratings.home_advantage * model_cfg.ha_scale
        for m in by_date[day]:
            o = odds.get((m.home, m.away))
            h, a = ratings.teams[m.home], ratings.teams[m.away]
            pred = predict_match(
                h["xgf"], h["xga"], a["xgf"], a["xga"],
                home_advantage=ha,
                total_line=2.5,
                ah_line=o.ah_line if o and o.ah_line is not None else None,
                league_xg=ratings.league_xg,
                baseline_goals=ratings.baseline_goals,
                rho=model_cfg.rho,
            )
            result.rows.append(_row(league, season, m, pred, o, h, a))


def _row(league: str, season: int, m: us.MatchRecord, pred: Any, o: fd.OddsRecord | None,
         h: dict[str, float], a: dict[str, float]) -> dict[str, Any]:
    hg, ag = m.home_goals or 0, m.away_goals or 0
    row: dict[str, Any] = {
        "league": league, "season": season, "date": m.date, "home": m.home, "away": m.away,
        "hg": hg, "ag": ag,
        "outcome": 0 if hg > ag else (1 if hg == ag else 2),
        "p": (pred.home_win_prob, pred.draw_prob, pred.away_win_prob),
        "p_over": pred.over_prob,
        "lam": (pred.lambda_home, pred.lambda_away),
        "home_gp": h["gp"], "away_gp": a["gp"],
        "ah_line": pred.ah_line, "p_ah": pred.ah_cover_prob,
    }
    if o and o.close_home and o.close_draw and o.close_away:
        row["odds"] = (o.close_home, o.close_draw, o.close_away)
        row["q"] = tuple(fd.devig(o.close_home, o.close_draw, o.close_away))
    if o and o.close_over25 and o.close_under25:
        row["odds_ou"] = (o.close_over25, o.close_under25)
        row["q_over"] = fd.devig(o.close_over25, o.close_under25)[0]
    if o and o.ah_line is not None and o.close_ah_home and o.close_ah_away:
        row["odds_ah"] = (o.close_ah_home, o.close_ah_away)
        row["q_ah"] = fd.devig(o.close_ah_home, o.close_ah_away)[0]
    return row


# ─── Metrics ─────────────────────────────────────────────────────────

def _ll3(p: tuple[float, float, float], k: int) -> float:
    return -math.log(max(p[k], 1e-6))


def _ll2(p: float, hit: bool) -> float:
    return -math.log(max(p if hit else 1 - p, 1e-6))


def _blend3(p: tuple[float, ...], q: tuple[float, ...], w: float) -> tuple[float, float, float]:
    b = [w * pi + (1 - w) * qi for pi, qi in zip(p, q, strict=True)]
    s = sum(b)
    return (b[0] / s, b[1] / s, b[2] / s)


def summarise(result: BacktestResult, blend: float = 0.3) -> dict[str, Any]:
    """Log loss (model, market, blend), calibration and flat-stake ROI."""
    rows = [r for r in result.rows if "q" in r]
    if not rows:
        return {}
    n = len(rows)
    model_ll = sum(_ll3(r["p"], r["outcome"]) for r in rows) / n
    market_ll = sum(_ll3(r["q"], r["outcome"]) for r in rows) / n
    blend_ll = sum(_ll3(_blend3(r["p"], r["q"], blend), r["outcome"]) for r in rows) / n
    brier = sum(
        sum((r["p"][k] - (1.0 if r["outcome"] == k else 0.0)) ** 2 for k in range(3)) for r in rows
    ) / n

    ou = [r for r in rows if "q_over" in r]
    ou_hit = [r["hg"] + r["ag"] > 2.5 for r in ou]
    pairs = list(zip(ou, ou_hit, strict=True))
    ou_model = sum(_ll2(r["p_over"], hit) for r, hit in pairs) / max(len(ou), 1)
    ou_market = sum(_ll2(r["q_over"], hit) for r, hit in pairs) / max(len(ou), 1)

    goals = sum(r["hg"] + r["ag"] for r in rows) / n
    proj = sum(r["lam"][0] + r["lam"][1] for r in rows) / n
    home_rate = sum(r["outcome"] == 0 for r in rows) / n
    draw_rate = sum(r["outcome"] == 1 for r in rows) / n
    model_home = sum(r["p"][0] for r in rows) / n
    model_draw = sum(r["p"][1] for r in rows) / n

    return {
        "matches": n,
        "ll_model": round(model_ll, 4),
        "ll_market": round(market_ll, 4),
        f"ll_blend_{blend}": round(blend_ll, 4),
        "brier_model": round(brier, 4),
        "ou_ll_model": round(ou_model, 4),
        "ou_ll_market": round(ou_market, 4),
        "goals_actual": round(goals, 3),
        "goals_projected": round(proj, 3),
        "home_rate": round(home_rate, 3), "home_model": round(model_home, 3),
        "draw_rate": round(draw_rate, 3), "draw_model": round(model_draw, 3),
    }


def betting_sim(
    result: BacktestResult,
    min_edge: float = 0.03,
    blend: float = 1.0,
    markets: tuple[str, ...] = ("1x2", "ou25", "ah"),
) -> dict[str, dict[str, float]]:
    """Flat 1-unit bets at Pinnacle closing odds where the (blended) model's
    probability beats the de-vigged close by ``min_edge``.

    Betting at the close is the hardest test: real bets are placed earlier,
    at softer prices, but closing-line value is what predicts long-run profit.
    """
    out: dict[str, dict[str, float]] = {}
    for mk in markets:
        bets = 0
        pnl = 0.0
        for r in result.rows:
            for prob, q, leg_pnl in _legs(r, mk, blend):
                if prob - q < min_edge:
                    continue
                bets += 1
                pnl += leg_pnl
        out[mk] = {
            "bets": bets, "pnl": round(pnl, 2),
            "roi": round(pnl / bets, 4) if bets else 0.0,
        }
    return out


def _line_pnl(value: float, line: float, odds: float) -> float:
    """1-unit P&L of a bet winning when value + line > 0 (quarter lines split)."""
    comps = [line - 0.25, line + 0.25] if round(line * 4) % 2 else [line]
    pnl = 0.0
    for c in comps:
        x = value + c
        if x > 1e-9:
            pnl += (odds - 1.0) / len(comps)
        elif x < -1e-9:
            pnl -= 1.0 / len(comps)
    return pnl


def _legs(r: dict[str, Any], market: str, blend: float):  # type: ignore[no-untyped-def]
    """Yield (model prob, de-vigged market prob, P&L if bet) per side."""
    if market == "1x2" and "q" in r:
        p = _blend3(r["p"], r["q"], blend)
        for k in range(3):
            yield p[k], r["q"][k], (r["odds"][k] - 1.0) if r["outcome"] == k else -1.0
    elif market == "ou25" and "q_over" in r:
        po = blend * r["p_over"] + (1 - blend) * r["q_over"]
        total = r["hg"] + r["ag"]
        yield po, r["q_over"], _line_pnl(total, -2.5, r["odds_ou"][0])
        yield 1 - po, 1 - r["q_over"], _line_pnl(-total, 2.5, r["odds_ou"][1])
    elif market == "ah" and "q_ah" in r and r["ah_line"] is not None:
        pa = blend * r["p_ah"] + (1 - blend) * r["q_ah"]
        margin = r["hg"] - r["ag"]
        yield pa, r["q_ah"], _line_pnl(margin, r["ah_line"], r["odds_ah"][0])
        yield 1 - pa, 1 - r["q_ah"], _line_pnl(-margin, -r["ah_line"], r["odds_ah"][1])
