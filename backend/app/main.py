"""Foot8 API — FastAPI wrapper around the matchday pipelines.

    uvicorn app.main:app --reload --port 8100

Analyses are computed in a worker thread and cached for CACHE_TTL seconds;
the first request after start-up (or after /refresh) takes ~10-30 s while
Understat / FPL / Polymarket / ESPN data loads.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from app.db import DatabaseNotConfiguredError, init_db, session_factory
from app.services.history import graded_history
from app.services.match_day import MAX_VERDICT, MIN_LIQUIDITY, MatchAnalysis, analyse_league
from app.services.store import save_run
from app.services.ucl import analyse_ucl, load_history

LEAGUES = ("EPL", "LALIGA", "UCL")
LEAGUE_NAMES = {"EPL": "Premier League", "LALIGA": "La Liga", "UCL": "Champions League"}
CACHE_TTL = 600.0
SAVE_INTERVAL = 3600.0   # store live predictions at most hourly per league (history grading)
BACKTEST_FILE = Path(__file__).resolve().parents[1] / "data" / "backtest_summary.json"

app = FastAPI(title="Foot8", version="0.1.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3008", "http://127.0.0.1:3008"],
    allow_methods=["*"],
    allow_headers=["*"],
)

_cache: dict[tuple[str, int], tuple[float, dict[str, Any]]] = {}
_locks: dict[tuple[str, int], asyncio.Lock] = {}
_last_saved: dict[str, float] = {}
_history_cache: dict[int, tuple[float, dict[str, Any]]] = {}
_history_lock = asyncio.Lock()


def _jsonable(obj: Any) -> Any:
    if is_dataclass(obj) and not isinstance(obj, type):
        return _jsonable(asdict(obj))
    if hasattr(obj, "model_dump"):
        return _jsonable(obj.model_dump())
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list | tuple):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    return obj


def _save_predictions(league: str, days: int, analyses: list[MatchAnalysis]) -> None:
    """Store a pre-match snapshot so /history can grade it later (best effort)."""
    if not analyses or time.time() - _last_saved.get(league, 0.0) < SAVE_INTERVAL:
        return
    try:
        engine = init_db()
        with session_factory(engine)() as session:
            run = save_run(session, analyses, [league], days)
        _last_saved[league] = time.time()
        logger.info(f"saved {league} run {run.id} ({len(analyses)} matches)")
    except DatabaseNotConfiguredError:
        _last_saved[league] = time.time()
    except Exception as e:  # noqa: BLE001 — never break the dashboard over the DB
        logger.warning(f"could not save {league} predictions: {e}")


def _compute(league: str, days: int) -> dict[str, Any]:
    started = time.time()
    warnings: list[str] = []
    matches: list[dict[str, Any]] = []
    analyses: list[MatchAnalysis] = []
    try:
        if league == "UCL":
            history = load_history(cache_only=True)
            for u in analyse_ucl(days, history=history):
                analyses.append(u.match)
                row = _jsonable(u.match)
                row["tie"] = _jsonable(u.tie) if u.tie else None
                matches.append(row)
        else:
            analyses = analyse_league(league, days)
            matches = [_jsonable(a) for a in analyses]
    except Exception as e:  # noqa: BLE001 — surface the failure to the dashboard
        logger.exception(f"{league} analysis failed")
        warnings.append(f"{LEAGUE_NAMES[league]} analysis failed: {e}")
    _save_predictions(league, days, analyses)
    return {
        "league": league,
        "name": LEAGUE_NAMES[league],
        "generated_at": datetime.now(UTC).isoformat(),
        "compute_seconds": round(time.time() - started, 1),
        "matches": matches,
        "warnings": warnings,
    }


async def _get(league: str, days: int, force: bool = False) -> dict[str, Any]:
    key = (league, days)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        hit = _cache.get(key)
        if hit and not force and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        result = await asyncio.to_thread(_compute, league, days)
        _cache[key] = (time.time(), result)
        return result


@app.on_event("startup")
async def _warm() -> None:
    for league in LEAGUES:
        asyncio.create_task(_get(league, 7))


@app.get("/api/v1/health")
async def health() -> dict[str, Any]:
    return {
        "status": "ok",
        "cached": [f"{lg}:{d}" for (lg, d) in _cache],
        "policy": {"max_verdict": MAX_VERDICT, "min_liquidity": MIN_LIQUIDITY},
    }


@app.get("/api/v1/matches/{league}")
async def matches(league: str, days: int = Query(7, ge=1, le=14)) -> dict[str, Any]:
    league = league.upper()
    if league not in LEAGUES:
        raise HTTPException(404, f"unknown league {league}; use one of {LEAGUES}")
    return await _get(league, days)


@app.get("/api/v1/dashboard")
async def dashboard(days: int = Query(7, ge=1, le=14)) -> dict[str, Any]:
    results = await asyncio.gather(*(_get(lg, days) for lg in LEAGUES))
    top: list[dict[str, Any]] = []
    for res in results:
        for m in res["matches"]:
            for mk in m.get("markets", []):
                if mk["edge"]["best_edge"] >= 0.03:
                    top.append({
                        "league": res["league"], "slug": m["slug"], "kickoff": m["kickoff"],
                        "match": f"{m['home']} vs {m['away']}", "label": mk["label"],
                        "side": mk["edge"]["best_side"], "price": mk["price"],
                        "model_prob": mk["model_prob"], "edge": mk["edge"]["best_edge"],
                        "verdict": mk["edge"]["verdict"], "liquidity": mk["liquidity"],
                    })
    top.sort(key=lambda x: x["edge"], reverse=True)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "days": days,
        "leagues": results,
        "top_edges": top[:15],
        "policy": {"max_verdict": MAX_VERDICT, "min_liquidity": MIN_LIQUIDITY},
    }


@app.post("/api/v1/refresh")
async def refresh(days: int = Query(7, ge=1, le=14)) -> dict[str, Any]:
    results = await asyncio.gather(*(_get(lg, days, force=True) for lg in LEAGUES))
    return {"refreshed": [r["league"] for r in results]}


@app.get("/api/v1/history")
async def history(days: int = Query(7, ge=1, le=60), force: bool = False) -> dict[str, Any]:
    """Past predictions graded against results (live snapshots + backfill)."""
    async with _history_lock:
        hit = _history_cache.get(days)
        if hit and not force and time.time() - hit[0] < CACHE_TTL:
            return hit[1]
        graded = await asyncio.to_thread(graded_history, days)
        result = _jsonable({k: v for k, v in graded.items() if k != "matches"})
        result["matches"] = [_jsonable(g) | {"result": g.result} for g in graded["matches"]]
        _history_cache[days] = (time.time(), result)
        return result


@app.get("/api/v1/backtest")
async def backtest() -> dict[str, Any]:
    if not BACKTEST_FILE.exists():
        raise HTTPException(404, "no backtest summary yet")
    return json.loads(BACKTEST_FILE.read_text())
