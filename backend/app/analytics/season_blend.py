"""
Early-season rating blend — regress small-sample current-season xG ratings
toward last season's.

After 2-3 matches a team's xGF/xGA is mostly fixture noise (one match against
the champions, one against a promoted side).  Weight the current season by
matches played:

    w_current = gp / (gp + K)
    blended   = w_current * current + (1 - w_current) * prior

NBA used K=15 of 82 games.  xG per 90 stabilises faster per match than
basketball net rating does per game, but a season is only 38 matches; K=8 puts
a team 50/50 at 8 matches and ~83% current-season by matchday 38.

Football-only: promoted teams have no prior in this division.  Their prior is
PROMOTED_TEAM_PRIOR (a typical promoted side), not the league average — on
average they finish well below mid-table.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.services.validation import DataValidator

LEAGUE_AVG_RATINGS: dict[str, float] = {"xgf": 1.375, "xga": 1.375, "xgd": 0.0}
# Typical top-flight promoted side, xG per 90 (≈ −0.5 xGD).
PROMOTED_TEAM_PRIOR: dict[str, float] = {"xgf": 1.15, "xga": 1.65, "xgd": -0.5}

_RATING_KEYS = ("xgf", "xga")


def blend_weight(games_played: float, k: float) -> float:
    """Weight on the current season given matches played (0 → 0.0, ∞ → 1.0)."""
    gp = max(0.0, float(games_played or 0))
    if k <= 0:
        return 1.0 if gp > 0 else 0.0
    return gp / (gp + k)


def _ratings_valid(ratings: dict[str, float], validator: DataValidator, source: str) -> bool:
    results = validator.validate_team_ratings(
        ratings["xgf"], ratings["xga"], ratings["xgd"], source=source,
    )
    return all(r.is_valid for r in results)


def blend_team_ratings(
    current: dict[str, dict[str, float]],
    prior: dict[str, dict[str, float]],
    k: float = 8.0,
    validator: DataValidator | None = None,
    promoted: set[str] | None = None,
) -> dict[str, dict[str, float]]:
    """Blend per-team current-season ratings with prior-season ratings.

    Both inputs map team → {"xgf", "xga", "gp"?}.  xGD is recomputed as
    xGF − xGA so it stays consistent after blending.

    Fallbacks:
      - team missing from current (no matches yet) → prior as-is
      - team missing from prior → PROMOTED_TEAM_PRIOR if in ``promoted``
        (or simply absent from prior), regressed with the same weight
      - blended result outside validation ranges → prior, else league average
    """
    validator = validator or DataValidator()
    promoted = promoted or set()
    result: dict[str, dict[str, float]] = {}

    for team in set(current) | set(prior):
        cur = current.get(team)
        base = prior.get(team)
        if base is None or team in promoted:
            base = PROMOTED_TEAM_PRIOR
        gp = float(cur.get("gp", 0) or 0) if cur else 0.0
        w = blend_weight(gp, k) if cur else 0.0

        blended = {
            key: w * float(cur[key]) + (1 - w) * float(base[key]) if cur else float(base[key])
            for key in _RATING_KEYS
        }
        blended["xgd"] = blended["xgf"] - blended["xga"]
        blended = {key: round(val, 3) for key, val in blended.items()}

        if not _ratings_valid(blended, validator, source=f"blend:{team}"):
            fallback = prior.get(team) or LEAGUE_AVG_RATINGS
            logger.warning(f"{team} blended ratings out of range {blended}; falling back to prior")
            blended = {key: float(fallback[key]) for key in (*_RATING_KEYS, "xgd")}
            w = 0.0

        blended["gp"] = gp
        blended["current_weight"] = round(w, 3)
        result[team] = blended

    return result


def blend_player_metrics(
    current: dict[str, dict[str, Any]],
    prior: dict[str, dict[str, Any]],
    k: float = 8.0,
) -> dict[str, dict[str, Any]]:
    """Blend per-player on-pitch metrics (keyed by name) by appearances.

    Injured players often have 0 current-season appearances, so last season
    keeps their absence impact meaningful early on.  New signings from other
    leagues (no prior row) use current-season numbers as-is.
    """
    blended_keys = ("MIN", "ON_XGF90", "ON_XGA90", "ON_XGD90", "GOALS_PREVENTED90")
    result: dict[str, dict[str, Any]] = {}

    for name in set(current) | set(prior):
        cur = current.get(name)
        old = prior.get(name)
        if not cur or not old:
            result[name] = dict(cur or old)  # type: ignore[arg-type]
            continue

        w = blend_weight(float(cur.get("GP", 0) or 0), k)
        row = dict(cur)
        for key in blended_keys:
            c, p = cur.get(key), old.get(key)
            if c is None or p is None:
                row[key] = c if c is not None else p
            else:
                row[key] = w * float(c) + (1 - w) * float(p)
        result[name] = row

    return result
