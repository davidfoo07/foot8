"""
Player absences from an injury / suspension list — football translation of
the NBA pipeline's _build_player_absences.

NBA used playerestimatedmetrics E_NET_RATING (on-court team net rating) minus
the team's own NRtg, weighted by MIN/48.  Football uses the player's on-pitch
xGD per 90 minus the team's xGD per 90, weighted by minutes/90.  Same reason
for the subtraction: on a bad side every player's on-pitch xGD is negative,
which says nothing about what losing him costs.
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from app.analytics.lineup_adjustment import compute_goalkeeper_impact
from app.schemas.team import PlayerAbsence

# Ignore squad players averaging under 25 minutes per appearance — their
# on-pitch splits are mostly late-game substitute noise.
MIN_MINUTES_THRESHOLD = 25.0
MIN_APPEARANCES = 5

# On-pitch xGD includes ten teammates, so the raw relative number overstates a
# single player.  NBA used 0.6; football has twice as many players sharing the
# pitch, so the share attributable to one is smaller.
DAMPENING = 0.5

DEFAULT_MISS: dict[str, float] = {
    "OUT": 1.0,
    "SUSPENDED": 1.0,      # red card / yellow accumulation: certain
    "DOUBTFUL": 0.75,
    "QUESTIONABLE": 0.50,  # "late fitness test"
}

# Impact when we have no stats for the player (scaled from NBA's −0.5 / +0.3).
NO_STATS_XGF_IMPACT = -0.05
NO_STATS_XGA_IMPACT = 0.03
NO_STATS_MINUTES_SHARE = 0.5

# Contribution model (sources without on/off splits: FPL, Understat).
# Attack: the player's own xG + xA per 90 above a positional replacement.
# xG and xA of one chance overlap (the assist and the shot are the same
# chance), so only half of xG + xA is attributed to the player.
REPLACEMENT_XGI90 = {"FWD": 0.35, "MID": 0.20, "DEF": 0.08, "GK": 0.0, "UNK": 0.20}
CONTRIBUTION_SHARE = 0.5


def _miss_probability(status: str, override: str | None) -> float:
    if override is not None:
        return {"FULL": 1.0, "HALF": 0.5}.get(override.upper(), 0.0)
    return DEFAULT_MISS.get(status, 0.0)


def build_player_absences(
    injuries_raw: list[dict[str, Any]],
    player_metrics: dict[str, dict[str, Any]] | None = None,
    injury_overrides: dict[str, str] | None = None,
    team_xgd: float = 0.0,
    backup_keeper_gp90: float = -0.10,
    team_xga: float | None = None,
) -> list[PlayerAbsence]:
    """Build the absence list with per-player impact.

    injuries_raw rows: {"player_id", "player_name", "status", "reason"?}.
    player_metrics by name: {"MIN" (minutes per appearance), "GP" (appearances),
      "POS" (GK/DEF/MID/FWD), "ON_XGD90", "ON_XGF90"?, "ON_XGA90"?,
      "GOALS_PREVENTED90" (keepers)}.
    injury_overrides: name → FULL (100% miss) / HALF (50%) / OFF (plays).

    Metrics with "XGI90" (+ optional "ON_XGA90") and no on/off "ON_XGD90" use
    the contribution model instead (FPL / Understat data).

    Outfield players split their relative xGD into attack/defence by the
    on-pitch xGF/xGA deviations when present, else by position (forwards
    mostly attack, defenders mostly defence).
    """
    overrides = injury_overrides or {}
    pm = player_metrics or {}
    absences: list[PlayerAbsence] = []

    for inj in injuries_raw:
        status = str(inj.get("status", "OUT")).upper()
        name = inj.get("player_name", "Unknown")
        miss = _miss_probability(status, overrides.get(name))
        if miss <= 0:
            continue

        stats = pm.get(name)
        position = str((stats or {}).get("POS", inj.get("position", "UNK"))).upper()

        if stats and position == "GK" and stats.get("GOALS_PREVENTED90") is not None:
            share = min(1.0, float(stats.get("MIN", 90) or 90) / 90.0)
            xgf_i, xga_i, xgd_i = compute_goalkeeper_impact(
                float(stats["GOALS_PREVENTED90"]), backup_keeper_gp90, share,
            )
            xgf_i, xga_i, xgd_i = xgf_i * miss, xga_i * miss, xgd_i * miss
        elif stats and stats.get("XGI90") is not None and stats.get("ON_XGD90") is None:
            minutes = float(stats.get("MIN", 0) or 0)
            apps = float(stats.get("GP", 0) or 0)
            if minutes < MIN_MINUTES_THRESHOLD or apps < MIN_APPEARANCES:
                continue
            share = minutes / 90.0
            above = max(0.0, float(stats["XGI90"]) - REPLACEMENT_XGI90.get(position, 0.2))
            xgf_i = -above * CONTRIBUTION_SHARE * share * miss
            xga_i = 0.0
            if team_xga is not None and stats.get("ON_XGA90") is not None:
                # Team concedes less with him on → losing him raises xGA.
                xga_i = (team_xga - float(stats["ON_XGA90"])) * share * DAMPENING * miss
                xga_i = max(xga_i, 0.0)
            xgd_i = xgf_i - xga_i
        elif stats:
            minutes = float(stats.get("MIN", 0) or 0)
            apps = float(stats.get("GP", 0) or 0)
            if minutes < MIN_MINUTES_THRESHOLD or apps < MIN_APPEARANCES:
                logger.debug(
                    f"  Skipping {name} ({status}) — {minutes:.0f} min/app, {apps:.0f} apps"
                )
                continue

            share = minutes / 90.0
            rel_xgd = float(stats.get("ON_XGD90", 0) or 0) - team_xgd
            xgd_i = -rel_xgd * share * DAMPENING * miss
            att_weight = _attack_weight(stats, position)
            xgf_i = xgd_i * att_weight
            xga_i = -xgd_i * (1.0 - att_weight)
        else:
            share = NO_STATS_MINUTES_SHARE
            xgf_i = NO_STATS_XGF_IMPACT * miss
            xga_i = NO_STATS_XGA_IMPACT * miss
            xgd_i = xgf_i - xga_i

        absences.append(PlayerAbsence(
            player_id=str(inj.get("player_id", "unknown")),
            name=name,
            position=position,
            status=status,
            reason=inj.get("reason"),
            xgf_impact=round(xgf_i, 3),
            xga_impact=round(xga_i, 3),
            xgd_impact=round(xgd_i, 3),
            minutes_share=round(share, 3),
        ))

    # Biggest impact first, so the star gets diminishing-returns factor 1.0
    absences.sort(key=lambda a: abs(a.xgd_impact), reverse=True)
    return absences


_POSITION_ATTACK_WEIGHT = {"FWD": 0.8, "MID": 0.5, "DEF": 0.2, "GK": 0.0}


def _attack_weight(stats: dict[str, Any], position: str) -> float:
    """Share of a player's xGD impact that lands on attack (rest on defence)."""
    on_f, on_a = stats.get("ON_XGF90"), stats.get("ON_XGA90")
    off_f, off_a = stats.get("OFF_XGF90"), stats.get("OFF_XGA90")
    if None not in (on_f, on_a, off_f, off_a):
        att = float(on_f) - float(off_f)  # type: ignore[arg-type]
        dfn = float(off_a) - float(on_a)  # type: ignore[arg-type]
        if att + dfn > 0 and att >= 0 and dfn >= 0:
            return att / (att + dfn)
    return _POSITION_ATTACK_WEIGHT.get(position, 0.5)
