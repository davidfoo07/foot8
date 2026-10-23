"""
Squad-based early-season prior — rate *this* season's squad, not last
season's team.

Football's transfer window reshapes squads more than NBA free agency.  Using
last season's TEAM xG as the prior ignores that; instead rebuild each team's
prior from last season's player-level on-pitch xG (team xGF/xGA per 90 while
the player was on the pitch), weighted by projected minutes for the CURRENT
squad:

  1. Each squad player gets last season's minutes per appearance and on-pitch
     xGF/xGA, shrunk toward replacement level by appearances.  Players with no
     prior row (academy, signings from leagues we don't cover) are replacement
     level.
  2. Projected minutes fill an 11 × 90 = 990-minute XI in minutes order
     (NBA: 5 × 48 = 240); leftovers go to replacement level.
  3. Minutes-weighted xGF/xGA per team, re-centred on last season's league
     average (deviations stretched by SPREAD_SCALE).

For an unchanged squad this lands close to last season's team rating; a
transferred player takes his on-pitch impact with him.

Caveat vs NBA: on-pitch xG for a player moving between leagues is not
comparable (Eredivisie xG ≠ Premier League xG).  Rows should already be
league-adjusted, or such players treated as no-data.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.analytics.season_blend import LEAGUE_AVG_RATINGS
from app.services.validation import DataValidator

XI_MINUTES = 990.0
# Appearances of prior-season data at which a player is 50% his own numbers.
PLAYER_SHRINK_GP = 8.0
# Replacement-level on-pitch xG relative to league average (xGD ≈ −0.3/90).
REPLACEMENT_OFFSET = 0.15
NO_DATA_MPG = 20.0  # minutes per appearance expected of a player with no data
MAX_MPG = 88.0      # even ever-presents get subbed / miss the odd match
MIN_TEAMS_FOR_ANCHOR = 16  # of 20: anchoring needs most of the league
# Minutes-weighted on-pitch numbers are compressed vs real team ratings (every
# player's on-pitch xG includes ten teammates).  Carried over from the NBA
# backtest (slope 0.83 → ×1.2); NOT yet re-fitted on football data.
SPREAD_SCALE = 1.2


def _valid_player_row(row: dict[str, Any], validator: DataValidator, source: str) -> bool:
    xgf, xga, mpg = row.get("ON_XGF90"), row.get("ON_XGA90"), row.get("MIN")
    if xgf is None or xga is None or mpg is None:
        return False
    results = validator.validate_team_ratings(
        float(xgf), float(xga), float(xgf) - float(xga), source=source,
    )
    results.append(validator.validate_range("player_minutes", float(mpg), source=source))
    return all(r.is_valid for r in results)


def _player_projection(
    row: dict[str, Any] | None,
    league_avg: float,
    validator: DataValidator,
) -> tuple[float, float, float, bool]:
    """(mpg, xgf, xga, has_data) for one squad player."""
    repl_xgf = league_avg - REPLACEMENT_OFFSET
    repl_xga = league_avg + REPLACEMENT_OFFSET
    pid = row.get("PLAYER_ID") if row else None
    if not row or not _valid_player_row(row, validator, source=f"player_metrics:{pid}"):
        return NO_DATA_MPG, repl_xgf, repl_xga, False

    gp = max(0.0, float(row.get("GP", 0) or 0))
    w = gp / (gp + PLAYER_SHRINK_GP)
    xgf = w * float(row["ON_XGF90"]) + (1 - w) * repl_xgf
    xga = w * float(row["ON_XGA90"]) + (1 - w) * repl_xga
    mpg = min(MAX_MPG, float(row["MIN"]))
    return mpg, xgf, xga, True


def _raw_squad_rating(
    player_ids: list[int],
    metrics_by_id: dict[int, dict[str, Any]],
    league_avg: float,
    validator: DataValidator,
) -> dict[str, float]:
    """Un-anchored minutes-weighted on-pitch xG for one squad."""
    projections = [
        _player_projection(metrics_by_id.get(pid), league_avg, validator) for pid in player_ids
    ]
    projections.sort(key=lambda p: p[0], reverse=True)

    remaining = XI_MINUTES
    xgf_sum = xga_sum = covered = 0.0
    for mpg, xgf, xga, has_data in projections:
        minutes = min(mpg, remaining)
        if minutes <= 0:
            break
        xgf_sum += minutes * xgf
        xga_sum += minutes * xga
        covered += minutes if has_data else 0.0
        remaining -= minutes

    xgf_sum += remaining * (league_avg - REPLACEMENT_OFFSET)
    xga_sum += remaining * (league_avg + REPLACEMENT_OFFSET)

    return {
        "xgf": xgf_sum / XI_MINUTES,
        "xga": xga_sum / XI_MINUTES,
        "coverage": covered / XI_MINUTES,
    }


def league_average_rating(prior_team_ratings: dict[str, dict[str, float]]) -> float:
    """Last season's league-average xGF (= xGA), or the static default."""
    xgfs = [float(r["xgf"]) for r in prior_team_ratings.values() if r.get("xgf") is not None]
    return sum(xgfs) / len(xgfs) if xgfs else LEAGUE_AVG_RATINGS["xgf"]


def compute_squad_priors(
    squads: dict[str, list[int]],
    prior_player_metrics: dict[int, dict[str, Any]],
    prior_team_ratings: dict[str, dict[str, float]],
    validator: DataValidator | None = None,
    min_teams: int = MIN_TEAMS_FOR_ANCHOR,
) -> dict[str, dict[str, float]]:
    """Squad-based prior per team → {team: {xgf, xga, xgd, coverage}}.

    Returns {} when too few squads are available to anchor the league
    average.  Teams whose anchored prior fails validation are dropped (the
    caller falls back to last season's team rating / promoted prior).
    """
    validator = validator or DataValidator()
    if not prior_player_metrics:
        return {}
    squads = {team: ids for team, ids in squads.items() if ids}
    if len(squads) < min_teams:
        logger.warning(f"Squad prior: only {len(squads)} squads (need {min_teams}); skipping")
        return {}

    league_avg = league_average_rating(prior_team_ratings)
    raw = {
        team: _raw_squad_rating(ids, prior_player_metrics, league_avg, validator)
        for team, ids in squads.items()
    }

    mean_xgf = sum(r["xgf"] for r in raw.values()) / len(raw)
    mean_xga = sum(r["xga"] for r in raw.values()) / len(raw)

    result: dict[str, dict[str, float]] = {}
    for team, r in raw.items():
        xgf = league_avg + SPREAD_SCALE * (r["xgf"] - mean_xgf)
        xga = league_avg + SPREAD_SCALE * (r["xga"] - mean_xga)
        row = {
            "xgf": round(xgf, 3),
            "xga": round(xga, 3),
            "xgd": round(xgf - xga, 3),
            "coverage": round(r["coverage"], 3),
        }
        checks = validator.validate_team_ratings(
            row["xgf"], row["xga"], row["xgd"], source=f"squad_prior:{team}",
        )
        if not all(c.is_valid for c in checks):
            logger.warning(f"{team} squad prior out of range {row}; dropping")
            continue
        result[team] = row

    return result


def compute_squad_turnover(
    current_squads: dict[str, list[int]],
    prior_squads: dict[str, list[int]],
    prior_player_metrics: dict[int, dict[str, Any]],
) -> dict[str, dict[str, float]]:
    """Per-team squad turnover vs last season → {team: {minutes_left, minutes_new, turnover}}.

    minutes_left: share of last season's minutes (MIN × GP) whose player is gone.
    minutes_new:  share of this season's projected XI minutes from new players.
    turnover:     mean of the two (0 = same team, 1 = entirely new team).
    """
    result: dict[str, dict[str, float]] = {}
    for team, current_ids in current_squads.items():
        old_ids = prior_squads.get(team)
        if not current_ids or not old_ids:
            continue
        current, old = set(current_ids), set(old_ids)

        def total_minutes(pid: int) -> float:
            row = prior_player_metrics.get(pid) or {}
            return float(row.get("MIN", 0) or 0) * float(row.get("GP", 0) or 0)

        old_total = sum(total_minutes(pid) for pid in old)
        left = sum(total_minutes(pid) for pid in old - current)
        minutes_left = left / old_total if old_total > 0 else 0.0

        def projected_mpg(pid: int) -> float:
            mpg = (prior_player_metrics.get(pid) or {}).get("MIN")
            return min(MAX_MPG, float(mpg or NO_DATA_MPG))

        projected = sorted(((projected_mpg(pid), pid) for pid in current), reverse=True)
        remaining, new_minutes, filled = XI_MINUTES, 0.0, 0.0
        for mpg, pid in projected:
            minutes = min(mpg, remaining)
            if minutes <= 0:
                break
            filled += minutes
            new_minutes += minutes if pid not in old else 0.0
            remaining -= minutes
        minutes_new = new_minutes / filled if filled > 0 else 0.0

        result[team] = {
            "minutes_left": round(minutes_left, 3),
            "minutes_new": round(minutes_new, 3),
            "turnover": round((minutes_left + minutes_new) / 2, 3),
        }
    return result


def is_stale_prior(
    prior_source: str,
    turnover: float | None,
    current_weight: float,
    turnover_threshold: float = 0.35,
    max_current_weight: float = 0.5,
) -> bool:
    """True when a team's rating leans on a prior that ignores its new squad.

    Only matters while the prior dominates the blend.  Unknown turnover is
    treated as high — we can't rule out a summer rebuild.
    """
    if prior_source == "squad" or current_weight >= max_current_weight:
        return False
    return turnover is None or turnover >= turnover_threshold
