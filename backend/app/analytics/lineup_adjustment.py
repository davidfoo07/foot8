"""
Lineup Adjustment Engine — the CORE feature, translated to football.

NBA: on-court vs off-court ORtg/DRtg per 100 possessions, weighted by
minutes/48.  Football: on-pitch vs off-pitch xG for/against per 90, weighted
by the share of the 90 minutes the player normally plays.  Goalkeepers get
their own impact model (shot-stopping), because a keeper's on/off split only
measures his backup's minutes — usually a handful of cup games.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import UTC, datetime

from loguru import logger

from app.schemas.team import AdjustedRatings, PlayerAbsence


class LineupAdjustmentModel(ABC):
    """Abstract interface for lineup adjustment models."""

    @abstractmethod
    def calculate_adjusted_ratings(
        self,
        team_id: str,
        season_xgf: float,
        season_xga: float,
        missing_players: list[PlayerAbsence],
        minutes_context: dict | None = None,
    ) -> AdjustedRatings:
        """Calculate lineup-adjusted ratings for a team."""
        ...


class OnOffSplitModel(LineupAdjustmentModel):
    """
    V1: Additive on/off splits.

    For each missing player:
    1. Player impact = (on-pitch xG/90 − off-pitch xG/90) × minutes share
    2. For 2+ missing: diminishing returns (0.85 per additional player) —
       the replacement for the 3rd absentee is rarely as bad as the sum implies
    3. Apply summed deltas to the team baseline

    Confidence from on/off minutes sample size.  Football needs far more
    minutes than basketball: ~2.7 goals a match vs ~225 points.
    """

    DIMINISHING_RETURNS_FACTOR = 0.85
    HIGH_CONFIDENCE_MINUTES = 1800.0   # ≈ 20 full matches
    MEDIUM_CONFIDENCE_MINUTES = 900.0  # ≈ 10 full matches

    def calculate_adjusted_ratings(
        self,
        team_id: str,
        season_xgf: float,
        season_xga: float,
        missing_players: list[PlayerAbsence],
        minutes_context: dict | None = None,
    ) -> AdjustedRatings:
        season_xgd = season_xgf - season_xga

        if not missing_players:
            return AdjustedRatings(
                team=team_id,
                season_xgf=season_xgf,
                season_xga=season_xga,
                season_xgd=season_xgd,
                adjusted_xgf=season_xgf,
                adjusted_xga=season_xga,
                adjusted_xgd=season_xgd,
                xgf_delta=0.0,
                xga_delta=0.0,
                xgd_delta=0.0,
                missing_players=[],
                confidence="HIGH",
                last_updated=datetime.now(UTC),
            )

        total_xgf_impact = 0.0
        total_xga_impact = 0.0

        for i, player in enumerate(missing_players):
            factor = self.DIMINISHING_RETURNS_FACTOR ** i
            xgf_impact = player.xgf_impact * factor
            xga_impact = player.xga_impact * factor
            total_xgf_impact += xgf_impact
            total_xga_impact += xga_impact

            logger.debug(
                f"Player {player.name} ({player.position}): xGF impact={player.xgf_impact:+.3f} "
                f"(weighted={xgf_impact:+.3f}), xGA impact={player.xga_impact:+.3f} "
                f"(weighted={xga_impact:+.3f}), factor={factor:.2f}"
            )

        # xG can't go to zero: a side missing its whole front line still creates chances.
        adjusted_xgf = max(0.2, season_xgf + total_xgf_impact)
        adjusted_xga = max(0.2, season_xga + total_xga_impact)
        adjusted_xgd = adjusted_xgf - adjusted_xga

        confidence = self._calculate_confidence(missing_players, minutes_context)

        logger.info(
            f"{team_id} adjusted: xGF {season_xgf:.2f} → {adjusted_xgf:.2f}, "
            f"xGA {season_xga:.2f} → {adjusted_xga:.2f}, "
            f"xGD {season_xgd:+.2f} → {adjusted_xgd:+.2f}, confidence={confidence}"
        )

        return AdjustedRatings(
            team=team_id,
            season_xgf=season_xgf,
            season_xga=season_xga,
            season_xgd=round(season_xgd, 3),
            adjusted_xgf=round(adjusted_xgf, 3),
            adjusted_xga=round(adjusted_xga, 3),
            adjusted_xgd=round(adjusted_xgd, 3),
            xgf_delta=round(adjusted_xgf - season_xgf, 3),
            xga_delta=round(adjusted_xga - season_xga, 3),
            xgd_delta=round(adjusted_xgd - season_xgd, 3),
            missing_players=missing_players,
            confidence=confidence,
            last_updated=datetime.now(UTC),
        )

    def _calculate_confidence(
        self,
        missing_players: list[PlayerAbsence],
        minutes_context: dict | None = None,
    ) -> str:
        """
        - HIGH: ≥1800 on+off minutes for every missing player
        - MEDIUM: 900-1800
        - LOW: <900 or no data
        Without minutes context: by number of absentees (a starting XI with
        4+ changes is guesswork).
        """
        if not minutes_context:
            return "MEDIUM" if len(missing_players) <= 3 else "LOW"

        min_minutes = float("inf")
        for player in missing_players:
            mins = minutes_context.get(player.player_id, {})
            total = mins.get("on_minutes", 0) + mins.get("off_minutes", 0)
            min_minutes = min(min_minutes, total)

        if min_minutes == float("inf"):
            return "LOW"
        if min_minutes >= self.HIGH_CONFIDENCE_MINUTES:
            return "HIGH"
        if min_minutes >= self.MEDIUM_CONFIDENCE_MINUTES:
            return "MEDIUM"
        return "LOW"


def compute_player_impact(
    on_xgf90: float,
    off_xgf90: float,
    on_xga90: float,
    off_xga90: float,
    minutes_share: float,
) -> tuple[float, float, float]:
    """
    A player's impact on team ratings from on/off splits (per 90).

    Returns (xgf_impact, xga_impact, xgd_impact) for the team WITHOUT him.
    Negative xgf_impact: team attacks worse.  Positive xga_impact: team
    concedes more.
    """
    xgf_diff = on_xgf90 - off_xgf90  # positive = helps attack
    xga_diff = on_xga90 - off_xga90  # negative = helps defence

    xgf_impact = -(xgf_diff * minutes_share)
    xga_impact = -(xga_diff * minutes_share)
    xgd_impact = xgf_impact - xga_impact

    return (round(xgf_impact, 3), round(xga_impact, 3), round(xgd_impact, 3))


def compute_goalkeeper_impact(
    starter_goals_prevented90: float,
    backup_goals_prevented90: float,
    minutes_share: float = 1.0,
) -> tuple[float, float, float]:
    """
    Keeper absence via shot-stopping: goals prevented per 90 (post-shot xG
    faced − goals conceded).  A starter at +0.15 replaced by a backup at −0.10
    costs the team 0.25 goals/90 on the defensive side.

    Returns (xgf_impact, xga_impact, xgd_impact).  Keepers don't move xGF.
    """
    xga_impact = (starter_goals_prevented90 - backup_goals_prevented90) * minutes_share
    return (0.0, round(xga_impact, 3), round(-xga_impact, 3))
