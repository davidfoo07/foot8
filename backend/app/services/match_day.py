"""Matchday pipeline — football version of bk8's run_daily_pipeline.

Polymarket fixtures (next N days)
  → Understat ratings as of today (season blend)
  → absences: FPL (EPL) + manual file data/absences.json (any league)
  → lineup adjustment → schedule + motivation
  → Dixon-Coles score matrix → every listed market priced
  → edge vs the Polymarket price → verdict
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from loguru import logger

from app.analytics.edge_calculator import calculate_edge, cap_verdict
from app.analytics.lineup_adjustment import OnOffSplitModel
from app.analytics.player_impact import build_player_absences
from app.analytics.prediction_model import (
    asian_handicap_probabilities,
    btts_probability,
    calculate_schedule_modifier,
    predict_match,
    score_matrix,
    total_goals_probabilities,
)
from app.analytics.schedule_engine import calculate_schedule_context
from app.connectors import fpl
from app.connectors import polymarket as pm
from app.connectors import understat as us
from app.connectors.team_names import to_canonical
from app.schemas.market import EdgeResult
from app.services.ratings import RatingConfig, build_ratings
from app.services.standings import motivation_for

ABSENCES_FILE = Path(__file__).resolve().parents[2] / "data" / "absences.json"

# Backtest verdict policy (scripts/polymarket_backtest.py): the model alone
# does not beat sharp closing prices, so every signal is capped here until a
# market/line type shows positive out-of-sample ROI.  Edit with care.
MAX_VERDICT = "LEAN"

# Lines nobody trades (±4.5 goals, over 7.5 …) show stale 0.12 / 0.88 quotes on
# a few hundred dollars of liquidity — not real prices.
MIN_LIQUIDITY = 5000.0


@dataclass
class PricedMarket:
    market_type: str
    line: float | None
    label: str
    price: float
    model_prob: float
    edge: EdgeResult
    liquidity: float


@dataclass
class MatchAnalysis:
    league: str
    slug: str
    kickoff: datetime
    home: str
    away: str
    lambda_home: float
    lambda_away: float
    probs: dict[str, float]
    home_absences: list[str]
    away_absences: list[str]
    motivation: tuple[str, str]
    confidence: str
    markets: list[PricedMarket] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def load_manual_absences(path: Path = ABSENCES_FILE) -> dict[str, dict[str, list[dict]]]:
    """{"LALIGA": {"Barcelona": [{"player_name": "...", "status": "OUT"}]}}"""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        logger.error(f"absences.json is not valid JSON: {e}")
        return {}


def _epl_absences(teams: set[str]) -> tuple[dict[str, list[dict]], dict[str, dict]]:
    try:
        absences, metrics = fpl.parse_players(fpl.fetch_bootstrap())
    except Exception as e:  # noqa: BLE001 — injuries are optional input
        logger.warning(f"FPL fetch failed: {e}")
        return {}, {}
    by_team: dict[str, list[dict]] = {}
    for a in absences:
        team = to_canonical(a["team"], teams)
        if team:
            by_team.setdefault(team, []).append(a)
    return by_team, metrics


def analyse_league(
    league: str,
    days: int = 7,
    today: date | None = None,
    rating_cfg: RatingConfig | None = None,
) -> list[MatchAnalysis]:
    today = today or datetime.now(UTC).date()
    season = us.current_season(today)
    current = us.load_matches(league, season, today)
    prior = us.load_matches(league, season - 1)
    teams = {m.home for m in current}
    ratings = build_ratings(current, prior, today, rating_cfg)
    motivation = motivation_for(league, current, today)

    injuries, metrics = _epl_absences(teams) if league == "EPL" else ({}, {})
    for team, rows in load_manual_absences().get(league, {}).items():
        canon = to_canonical(team, teams) or team
        manual = {r["player_name"] for r in rows}
        injuries[canon] = [r for r in injuries.get(canon, []) if r["player_name"] not in manual]
        injuries[canon] += rows

    lineup = OnOffSplitModel()
    out: list[MatchAnalysis] = []
    for mm in pm.fetch_upcoming(league, days, today):
        home, away = pm.canonical_teams(mm, teams)
        if not home or not away:
            logger.warning(f"{mm.slug}: cannot map {mm.home} / {mm.away}")
            continue
        out.append(_analyse_match(league, mm, home, away, current, ratings, motivation,
                                  injuries, metrics, lineup))
    return out


def _analyse_match(
    league: str,
    mm: pm.MatchMarkets,
    home: str,
    away: str,
    current: list[us.MatchRecord],
    ratings: Any,
    motivation: dict[str, Any],
    injuries: dict[str, list[dict]],
    metrics: dict[str, dict],
    lineup: OnOffSplitModel,
) -> MatchAnalysis:
    warnings: list[str] = []
    adj = {}
    for team in (home, away):
        r = ratings.teams[team]
        absences = build_player_absences(
            injuries.get(team, []), metrics, team_xgd=r["xgd"], team_xga=r["xga"],
        )
        adj[team] = lineup.calculate_adjusted_ratings(team, r["xgf"], r["xga"], absences)
        if r["prior_source"] and r["current_weight"] < 0.5:
            warnings.append(f"{team}: promoted-team prior still dominates")
    if league != "EPL" and not load_manual_absences().get(league):
        warnings.append("No injury feed for this league — add absences to data/absences.json")

    sched = {}
    for team in (home, away):
        recent = [{"date": m.date} for m in current
                  if m.is_result and team in (m.home, m.away) and m.date < mm.kickoff.date()]
        sched[team] = calculate_schedule_context(team, mm.kickoff.date(), recent,
                                                 is_home=team == home)
    mods = {
        t: calculate_schedule_modifier(
            is_short_rest=s.is_short_rest, is_3_in_7=s.is_3_in_7, is_4_in_10=s.is_4_in_10,
            rest_days=s.rest_days, opponent_rest_days=sched[o].rest_days,
        )
        for t, s, o in ((home, sched[home], away), (away, sched[away], home))
    }
    hm = motivation.get(home)
    am = motivation.get(away)
    h_mot = hm.motivation_flag if hm else "NEUTRAL"
    a_mot = am.motivation_flag if am else "NEUTRAL"

    confidence = "LOW" if "LOW" in (adj[home].confidence, adj[away].confidence) else "MEDIUM"
    pred = predict_match(
        adj[home].adjusted_xgf, adj[home].adjusted_xga,
        adj[away].adjusted_xgf, adj[away].adjusted_xga,
        home_schedule_mod=mods[home], away_schedule_mod=mods[away],
        home_advantage=ratings.home_advantage,
        home_motivation=h_mot, away_motivation=a_mot,
        league_xg=ratings.league_xg, baseline_goals=ratings.baseline_goals,
        confidence=confidence,
    )
    matrix = score_matrix(pred.lambda_home, pred.lambda_away)

    analysis = MatchAnalysis(
        league=league, slug=mm.slug, kickoff=mm.kickoff, home=home, away=away,
        lambda_home=pred.lambda_home, lambda_away=pred.lambda_away,
        probs={"home_win": pred.home_win_prob, "draw": pred.draw_prob,
               "away_win": pred.away_win_prob, "btts": pred.btts_prob},
        home_absences=[p.name for p in adj[home].missing_players],
        away_absences=[p.name for p in adj[away].missing_players],
        motivation=(h_mot, a_mot), confidence=confidence, warnings=warnings,
    )
    for m in mm.markets:
        prob = _model_prob(m, pred, matrix)
        if prob is None or m.yes_price is None or not 0.01 <= m.yes_price <= 0.99:
            continue
        if m.liquidity < MIN_LIQUIDITY:
            continue
        edge = calculate_edge(prob, m.yes_price)
        max_v = "LEAN" if confidence == "LOW" else MAX_VERDICT
        analysis.markets.append(PricedMarket(
            market_type=m.market_type, line=m.line, label=_label(m, home, away),
            price=m.yes_price, model_prob=round(prob, 4),
            edge=cap_verdict(edge, max_v), liquidity=m.liquidity,
        ))
    analysis.markets.sort(key=lambda x: x.edge.best_edge, reverse=True)
    return analysis


def _model_prob(m: pm.OutcomeMarket, pred: Any, matrix: np.ndarray) -> float | None:
    if m.market_type == "home_win":
        return pred.home_win_prob
    if m.market_type == "draw":
        return pred.draw_prob
    if m.market_type == "away_win":
        return pred.away_win_prob
    if m.market_type == "btts":
        return btts_probability(matrix)
    if m.market_type == "total" and m.line is not None:
        return total_goals_probabilities(matrix, m.line)["fair_prob"]
    if m.market_type == "asian_handicap" and m.line is not None:
        return asian_handicap_probabilities(matrix, m.line)["fair_prob"]
    return None


def _label(m: pm.OutcomeMarket, home: str, away: str) -> str:
    return {
        "home_win": f"{home} win",
        "draw": "Draw",
        "away_win": f"{away} win",
        "btts": "BTTS yes",
        "total": f"Over {m.line}",
        "asian_handicap": f"{home} {m.line:+g}" if m.line is not None else home,
    }[m.market_type]
