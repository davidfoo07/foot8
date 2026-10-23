"""Pydantic schemas for team-related data.

Football translation of the NBA schemas:
  ORtg / DRtg / NRtg (points per 100 possessions)
    → xGF / xGA / xGD (expected goals for / against / difference per 90)
"""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel


class TeamRatingSchema(BaseModel):
    team_id: str
    date: date
    xgf: float  # attack: expected goals for per 90
    xga: float  # defence: expected goals against per 90 (lower is better)
    xgd: float  # net: xgf - xga
    source: str | None = None


class PlayerAbsence(BaseModel):
    """A player who is unavailable for this match."""
    player_id: str
    name: str
    position: str = "UNK"  # GK, DEF, MID, FWD
    status: str  # OUT, SUSPENDED, DOUBTFUL, QUESTIONABLE, AVAILABLE
    reason: str | None = None
    xgf_impact: float = 0.0  # negative = team attack worse without him
    xga_impact: float = 0.0  # positive = team defence worse without him
    xgd_impact: float = 0.0
    minutes_share: float = 0.0  # share of the 90 minutes he normally plays


class AdjustedRatings(BaseModel):
    """Lineup-adjusted team ratings for a specific match."""
    team: str
    season_xgf: float
    season_xga: float
    season_xgd: float
    adjusted_xgf: float
    adjusted_xga: float
    adjusted_xgd: float
    xgf_delta: float
    xga_delta: float
    xgd_delta: float
    missing_players: list[PlayerAbsence] = []
    confidence: str = "MEDIUM"  # HIGH, MEDIUM, LOW
    data_source: str = "xg"
    last_updated: datetime | None = None


class ScheduleContext(BaseModel):
    """Fixture-congestion context (football's version of B2B / 3-in-4)."""
    rest_days: int = 6
    is_short_rest: bool = False       # < 72h since last match (midweek turnaround)
    is_3_in_7: bool = False           # 3rd match in 7 days
    is_4_in_10: bool = False          # 4th match in 10 days
    played_europe_midweek: bool = False  # Thursday/Wednesday European tie before a weekend match
    is_home: bool = False
    travel_distance_km: float = 0.0


class StandingsInfo(BaseModel):
    team: str
    record: str  # W-D-L
    position: int
    points: int = 0
    games_remaining: int = 0
    status: str = "NONE"  # CHAMPIONS, RELEGATED, SAFE, NONE
    motivation_flag: str = "NEUTRAL"  # REST_EXPECTED, NEUTRAL, DESPERATE, FIGHTING
    motivation_note: str = ""
