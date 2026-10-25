"""Prediction history: grade foot8's pre-match predictions against results.

Two sources, labelled per match:
  live      — the latest prediction stored in the database BEFORE kick-off
              (written by `predict.py --save` and by the API on every refresh)
  backfill  — for played matches with no stored prediction: the walk-forward
              model (ratings use only matches before kick-off) and the
              Polymarket price 60 minutes before kick-off

Each match gets: result, model pick hit/miss, 1X2 log loss for the model and
for the market, and every quoted market with edge ≥ PICK_EDGE settled at the
quoted price (flat 1-unit stakes).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.analytics.edge_calculator import calculate_edge, cap_verdict
from app.analytics.global_ratings import EURO_GROUP, fit_global
from app.analytics.prediction_model import outcome_probabilities, predict_match, score_matrix
from app.analytics.prediction_model import total_goals_probabilities as totals
from app.connectors import polymarket as pm
from app.connectors import understat as us
from app.connectors.team_names import to_canonical
from app.db import (
    DatabaseNotConfiguredError,
    MatchPredictionRow,
    MatchResult,
    PredictionRun,
    make_engine,
    session_factory,
)
from app.services.match_day import MAX_VERDICT
from app.services.ratings import build_ratings
from app.services.ucl import UCL, _is_final, load_history, match_fixture

PICK_EDGE = 0.03
LEAGUES = ("EPL", "LALIGA", "UCL")


@dataclass
class GradedQuote:
    label: str
    side: str                 # YES / NO
    price: float              # price paid for that side
    model_prob: float         # model probability of that side
    edge: float
    verdict: str
    result: str               # WON, LOST, PUSH
    pnl: float                # per 1-unit stake


@dataclass
class GradedMatch:
    league: str
    slug: str
    kickoff: datetime
    home: str
    away: str
    source: str               # live / backfill
    predicted_at: datetime | None
    home_goals: int
    away_goals: int
    probs: dict[str, float]
    market: dict[str, float] | None   # Polymarket 1X2 (normalised) at prediction time
    pick: str                 # H / D / A — the model's most likely outcome
    pick_correct: bool
    ll_model: float
    ll_market: float | None
    quotes: list[GradedQuote] = field(default_factory=list)

    @property
    def result(self) -> str:
        return "H" if self.home_goals > self.away_goals else (
            "A" if self.away_goals > self.home_goals else "D")


# ─── Settlement ──────────────────────────────────────────────────────

def settle_yes(market_type: str, line: float | None, hg: int, ag: int) -> tuple[float, float]:
    """(win share, lose share) of the YES side; quarter lines split, whole lines push."""
    if market_type == "home_win":
        return (1.0, 0.0) if hg > ag else (0.0, 1.0)
    if market_type == "draw":
        return (1.0, 0.0) if hg == ag else (0.0, 1.0)
    if market_type == "away_win":
        return (1.0, 0.0) if ag > hg else (0.0, 1.0)
    if market_type == "btts":
        return (1.0, 0.0) if hg > 0 and ag > 0 else (0.0, 1.0)
    if line is None:
        raise ValueError(f"{market_type} needs a line")
    if market_type == "asian_handicap":      # YES = home side: margin + line > 0
        value, comps_of = float(hg - ag), line
        sign = 1.0
    elif market_type == "total":             # YES = over: goals − line > 0
        value, comps_of = float(hg + ag), -line
        sign = 1.0
    else:
        raise ValueError(f"unknown market {market_type}")
    comps = [comps_of - 0.25, comps_of + 0.25] if round(comps_of * 4) % 2 else [comps_of]
    win = lose = 0.0
    for c in comps:
        x = sign * (value + c)
        if x > 1e-9:
            win += 1 / len(comps)
        elif x < -1e-9:
            lose += 1 / len(comps)
    return win, lose


def grade_quote(market_type: str, line: float | None, label: str, yes_price: float,
                yes_model: float, hg: int, ag: int) -> GradedQuote:
    edge = cap_verdict(calculate_edge(yes_model, yes_price), MAX_VERDICT)
    side = edge.best_side
    w, lo = settle_yes(market_type, line, hg, ag)
    if side == "NO":
        w, lo = lo, w
    cost = yes_price if side == "YES" else 1 - yes_price
    pnl = w * (1 / cost - 1) - lo
    result = "WON" if w > lo else ("LOST" if lo > w else "PUSH")
    return GradedQuote(
        label=label if side == "YES" else f"NOT {label}", side=side, price=round(cost, 4),
        model_prob=round(yes_model if side == "YES" else 1 - yes_model, 4),
        edge=round(edge.best_edge, 4), verdict=edge.verdict, result=result, pnl=round(pnl, 4),
    )


def _ll(probs: tuple[float, float, float], outcome: str) -> float:
    k = {"H": 0, "D": 1, "A": 2}[outcome]
    return -math.log(max(probs[k] / sum(probs), 1e-6))


def _finish(gm: GradedMatch) -> GradedMatch:
    p = (gm.probs["home_win"], gm.probs["draw"], gm.probs["away_win"])
    gm.pick = "HDA"[max(range(3), key=lambda i: p[i])]
    gm.pick_correct = gm.pick == gm.result
    gm.ll_model = round(_ll(p, gm.result), 4)
    if gm.market:
        q = (gm.market["home_win"], gm.market["draw"], gm.market["away_win"])
        gm.ll_market = round(_ll(q, gm.result), 4)
    return gm


# ─── Results ─────────────────────────────────────────────────────────

@dataclass
class Result:
    league: str
    home: str
    away: str
    kickoff: datetime
    hg: int
    ag: int


def load_results(start: date, today: date) -> list[Result]:
    out: list[Result] = []
    season = us.current_season(today)
    for league in ("EPL", "LALIGA"):
        for m in us.load_matches(league, season, today):
            if m.is_result and start <= m.date <= today:
                out.append(Result(league, m.home, m.away, m.kickoff.replace(tzinfo=UTC),
                                   m.home_goals or 0, m.away_goals or 0))
    return out


def _find_result(results: list[Result], league: str, home: str, away: str,
                 kickoff: datetime) -> Result | None:
    for r in results:
        if (r.league == league and r.home == home and r.away == away
                and abs((r.kickoff.date() - kickoff.date()).days) <= 1):
            return r
    return None


# ─── Live (stored) predictions ───────────────────────────────────────

def _stored(start: datetime, now: datetime) -> list[MatchPredictionRow]:
    """Latest stored prediction made before kick-off, per fixture."""
    try:
        engine = make_engine()
    except DatabaseNotConfiguredError:
        return []
    with session_factory(engine)() as s:
        rows = s.execute(
            select(MatchPredictionRow)
            .join(PredictionRun)
            .where(MatchPredictionRow.kickoff >= start, MatchPredictionRow.kickoff <= now,
                   PredictionRun.created_at < MatchPredictionRow.kickoff)
            .options(selectinload(MatchPredictionRow.quotes),
                     selectinload(MatchPredictionRow.run))
        ).scalars().all()
    latest: dict[str, MatchPredictionRow] = {}
    for r in rows:
        cur = latest.get(r.slug)
        if cur is None or r.run.created_at > cur.run.created_at:
            latest[r.slug] = r
    return list(latest.values())


def _grade_stored(row: MatchPredictionRow, hg: int, ag: int) -> GradedMatch:
    quotes = {q.market_type: q for q in row.quotes if q.line is None}
    market = None
    if all(k in quotes for k in ("home_win", "draw", "away_win")):
        tot = sum(quotes[k].price for k in ("home_win", "draw", "away_win"))
        market = {k: quotes[k].price / tot for k in ("home_win", "draw", "away_win")}
    gm = GradedMatch(
        league=row.league, slug=row.slug, kickoff=row.kickoff, home=row.home, away=row.away,
        source="live", predicted_at=row.run.created_at, home_goals=hg, away_goals=ag,
        probs={"home_win": row.home_win, "draw": row.draw, "away_win": row.away_win,
               "btts": row.btts},
        market=market, pick="", pick_correct=False, ll_model=0.0, ll_market=None,
    )
    for q in row.quotes:
        if q.edge < PICK_EDGE:
            continue
        gm.quotes.append(grade_quote(q.market_type, q.line, q.label, q.price, q.model_prob,
                                     hg, ag))
    return _finish(gm)


# ─── Backfill ────────────────────────────────────────────────────────

def _settled_markets(league: str, start: date, today: date) -> list[pm.MatchMarkets]:
    events = []
    for closed in (True, False):
        events += pm.fetch_events(league, closed, start, today, ttl_seconds=600)
    unique = list({e["id"]: e for e in events}.values())
    cutoff = datetime.now(UTC) - timedelta(hours=2)
    return [m for m in pm.parse_match_events(league, unique) if m.kickoff < cutoff]


def _backfill_quotes(gm: GradedMatch, model: dict[str, float], poly: dict[str, float]) -> None:
    labels = {"home_win": f"{gm.home} win", "draw": "Draw", "away_win": f"{gm.away} win",
              "total:2.5": "Over 2.5", "btts": "BTTS yes"}
    for key, label in labels.items():
        if key not in poly or key not in model or not 0.01 <= poly[key] <= 0.99:
            continue
        mtype, line = (("total", 2.5) if key == "total:2.5" else (key, None))
        q = grade_quote(mtype, line, label, poly[key], model[key], gm.home_goals, gm.away_goals)
        if q.edge >= PICK_EDGE:
            gm.quotes.append(q)


def _backfill_league(league: str, markets: list[pm.MatchMarkets], results: list[Result],
                     today: date) -> list[GradedMatch]:
    season = us.current_season(today)
    current = us.load_matches(league, season, today)
    prior = us.load_matches(league, season - 1)
    teams = {m.home for m in current}
    prices = pm.snapshot_prices(markets, minutes_before=60)
    out: list[GradedMatch] = []
    cache: dict[date, Any] = {}
    for mm in markets:
        home, away = to_canonical(mm.home, teams), to_canonical(mm.away, teams)
        if not home or not away:
            continue
        res = _find_result(results, league, home, away, mm.kickoff)
        if res is None:
            continue
        day = mm.kickoff.date()
        if day not in cache:
            cache[day] = build_ratings(current, prior, day)
        r = cache[day]
        h, a = r.teams[home], r.teams[away]
        pred = predict_match(h["xgf"], h["xga"], a["xgf"], a["xga"],
                             home_advantage=r.home_advantage, total_line=2.5,
                             league_xg=r.league_xg, baseline_goals=r.baseline_goals)
        model = {"home_win": pred.home_win_prob, "draw": pred.draw_prob,
                 "away_win": pred.away_win_prob, "total:2.5": pred.over_prob,
                 "btts": pred.btts_prob}
        out.append(_backfill_match(league, mm, home, away, res.hg, res.ag, model,
                                   prices.get(mm.slug, {})))
    return out


def _backfill_match(league: str, mm: pm.MatchMarkets, home: str, away: str, hg: int, ag: int,
                    model: dict[str, float], poly: dict[str, float]) -> GradedMatch:
    market = None
    if all(k in poly for k in ("home_win", "draw", "away_win")):
        tot = sum(poly[k] for k in ("home_win", "draw", "away_win"))
        market = {k: poly[k] / tot for k in ("home_win", "draw", "away_win")}
    gm = GradedMatch(
        league=league, slug=mm.slug, kickoff=mm.kickoff, home=home, away=away,
        source="backfill", predicted_at=None, home_goals=hg, away_goals=ag,
        probs={k: model[k] for k in ("home_win", "draw", "away_win", "btts")},
        market=market, pick="", pick_correct=False, ll_model=0.0, ll_market=None,
    )
    _backfill_quotes(gm, model, poly)
    return _finish(gm)


def _backfill_ucl(markets: list[pm.MatchMarkets], today: date,
                  skip: set[str]) -> list[GradedMatch]:
    todo = [m for m in markets if m.slug not in skip]
    if not todo:
        return []
    history = load_history(today, days_ahead=0, cache_only=True)
    ucl = [m for m in history if m.competition == UCL and m.is_result]
    prices = pm.snapshot_prices(todo, minutes_before=60)
    out: list[GradedMatch] = []
    fits: dict[date, Any] = {}
    for mm in todo:
        fx = match_fixture(mm.home, mm.away, mm.kickoff, ucl)
        if fx is None:
            continue
        day = fx.kickoff.date()
        if day not in fits:
            fits[day] = fit_global(history, day)
        lh, la = fits[day].expected_goals(fx.home_id, fx.away_id, EURO_GROUP,
                                          neutral=_is_final(fx))
        mat = score_matrix(lh, la)
        hw, dr, aw = outcome_probabilities(mat)
        model = {"home_win": hw, "draw": dr, "away_win": aw,
                 "total:2.5": totals(mat, 2.5)["fair_prob"], "btts": float(mat[1:, 1:].sum())}
        out.append(_backfill_match("UCL", mm, fx.home_name, fx.away_name,
                                   fx.home_goals or 0, fx.away_goals or 0, model,
                                   prices.get(mm.slug, {})))
    return out


# ─── Public API ──────────────────────────────────────────────────────

def graded_history(days: int = 14, today: date | None = None) -> dict[str, Any]:
    now = datetime.now(UTC)
    today = today or now.date()
    start = today - timedelta(days=days)
    results = load_results(start, today)
    stored = _stored(datetime.combine(start, datetime.min.time(), UTC), now)

    graded: list[GradedMatch] = []
    done: set[str] = set()
    for row in stored:
        res = _find_result(results, row.league, row.home, row.away, row.kickoff)
        if res is None and row.league == "UCL":
            continue  # UCL stored rows are graded via ESPN below when backfilled
        if res is not None:
            graded.append(_grade_stored(row, res.hg, res.ag))
            done.add(row.slug)

    for league in LEAGUES:
        try:
            markets = _settled_markets(league, start, today)
            if league == "UCL":
                graded += _backfill_ucl(markets, today, done)
            else:
                todo = [m for m in markets if m.slug not in done]
                graded += _backfill_league(league, todo, results, today)
        except Exception as e:  # noqa: BLE001 — one league failing shouldn't hide the rest
            logger.exception(f"history backfill failed for {league}: {e}")

    graded.sort(key=lambda g: g.kickoff, reverse=True)
    _store_results(graded)
    return {
        "generated_at": now.isoformat(),
        "days": days,
        "pick_edge": PICK_EDGE,
        "summary": {lg: summarise([g for g in graded if g.league == lg]) for lg in LEAGUES}
        | {"ALL": summarise(graded)},
        "matches": graded,
    }


def summarise(rows: list[GradedMatch]) -> dict[str, Any]:
    if not rows:
        return {"matches": 0}
    with_market = [g for g in rows if g.ll_market is not None]
    quotes = [q for g in rows for q in g.quotes]
    staked = [q for q in quotes if q.result != "PUSH"] or quotes
    return {
        "matches": len(rows),
        "live": sum(g.source == "live" for g in rows),
        "pick_accuracy": round(sum(g.pick_correct for g in rows) / len(rows), 3),
        "market_pick_accuracy": round(
            sum(_market_pick(g) == g.result for g in with_market) / len(with_market), 3)
        if with_market else None,
        "ll_model": round(sum(g.ll_model for g in with_market) / len(with_market), 4)
        if with_market else round(sum(g.ll_model for g in rows) / len(rows), 4),
        "ll_market": round(sum(g.ll_market or 0 for g in with_market) / len(with_market), 4)
        if with_market else None,
        "picks": len(quotes),
        "picks_won": sum(q.result == "WON" for q in quotes),
        "pnl": round(sum(q.pnl for q in quotes), 2),
        "roi": round(sum(q.pnl for q in quotes) / len(staked), 4) if staked else 0.0,
    }


def _market_pick(g: GradedMatch) -> str:
    m = g.market or {}
    keys = ("home_win", "draw", "away_win")
    return "HDA"[max(range(3), key=lambda i: m.get(keys[i], 0))]


def _store_results(graded: list[GradedMatch]) -> None:
    """Upsert final scores into match_results (best effort)."""
    try:
        engine = make_engine()
    except DatabaseNotConfiguredError:
        return
    try:
        with session_factory(engine)() as s:
            existing = {r.slug for r in s.execute(select(MatchResult.slug)).scalars()}
            for g in graded:
                if g.slug not in existing:
                    s.add(MatchResult(slug=g.slug, league=g.league, kickoff=g.kickoff,
                                      home_goals=g.home_goals, away_goals=g.away_goals))
            s.commit()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"could not store results: {e}")
