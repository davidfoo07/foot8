"""
Schedule Context Engine — fixture congestion, rest advantage, and motivation.

NBA → football:
  back-to-back (0 rest days)  → short rest (< 72h: Sat → Tue)
  3 games in 4 nights         → 3 matches in 7 days
  4 games in 6 nights         → 4 matches in 10 days
  road trip length            → dropped (football away trips are one match)
  —                           → European tie midweek before a league match
  conference seed / clinch    → league position vs title, European places
                                and relegation zone
"""

from __future__ import annotations

from datetime import date, timedelta

from app.schemas.team import ScheduleContext, StandingsInfo

EUROPEAN_COMPETITIONS = {
    "UCL", "UEL", "UECL", "CHAMPIONS LEAGUE", "EUROPA LEAGUE", "CONFERENCE LEAGUE",
}


def calculate_schedule_context(
    team_id: str,
    match_date: date,
    recent_matches: list[dict] | None = None,
    is_home: bool = True,
    travel_distance_km: float = 0.0,
) -> ScheduleContext:
    """
    Schedule context for a team on a match date.

    recent_matches rows: {"date", "competition"?}.  Any competition counts
    toward congestion — a cup tie tires legs as much as a league match.
    """
    if not recent_matches:
        return ScheduleContext(is_home=is_home, travel_distance_km=travel_distance_km)

    sorted_matches = sorted(recent_matches, key=lambda g: str(g.get("date", "")), reverse=True)
    rest_days = _calculate_rest_days(match_date, sorted_matches)

    return ScheduleContext(
        rest_days=rest_days,
        is_short_rest=rest_days < 3,
        is_3_in_7=_check_matches_in_span(match_date, sorted_matches, 2, span_days=6),
        is_4_in_10=_check_matches_in_span(match_date, sorted_matches, 3, span_days=9),
        played_europe_midweek=_played_europe_recently(match_date, sorted_matches),
        is_home=is_home,
        travel_distance_km=travel_distance_km,
    )


def _calculate_rest_days(match_date: date, sorted_matches: list[dict]) -> int:
    """Full days between the last match and this one (Sat → Tue = 2)."""
    for m in sorted_matches:
        dt = _parse_date(m.get("date"))
        if dt and dt < match_date:
            return (match_date - dt).days - 1
    return 6  # Default: a normal weekly cycle


def _check_matches_in_span(
    match_date: date,
    sorted_matches: list[dict],
    matches_needed: int,
    span_days: int,
) -> bool:
    """True if the team played N matches in the span_days before this one."""
    start = match_date - timedelta(days=span_days)
    count = 0
    for m in sorted_matches:
        dt = _parse_date(m.get("date"))
        if dt and start <= dt < match_date:
            count += 1
            if count >= matches_needed:
                return True
    return False


def _played_europe_recently(
    match_date: date, sorted_matches: list[dict], within_days: int = 4,
) -> bool:
    for m in sorted_matches:
        dt = _parse_date(m.get("date"))
        if dt and dt < match_date:
            comp = str(m.get("competition", "")).upper()
            return (match_date - dt).days <= within_days and comp in EUROPEAN_COMPETITIONS
    return False


def _parse_date(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except (ValueError, IndexError):
            return None
    return None


def determine_motivation(
    team: str,
    record: str,
    position: int,
    points: int,
    games_remaining: int,
    points_above_relegation: int,
    points_behind_europe: int = 0,
    points_behind_leader: int = 0,
    status: str = "NONE",
    num_teams: int = 20,
    relegation_spots: int = 3,
    europe_spots: int = 6,
    season_length: int = 38,
    early_season_games: int = 6,
) -> StandingsInfo:
    """
    Motivation from the league table.

    points_above_relegation: team points − points of the highest team in the
      drop zone (≤ 0 when in the zone).
    points_behind_europe: points of the last European place − team points
      (≤ 0 when in a European place).
    points_behind_leader: leader's points − team points.

    Flags:
    - REST_EXPECTED: champions / relegated, or "on the beach" — mathematically
      safe and out of European reach in the last 5 rounds
    - DESPERATE: in or within 3 points of the drop zone with ≤ 8 to play
    - FIGHTING: title race or European places within reach late on; top 2 all
      season
    - NEUTRAL: default, and always for the first ``early_season_games`` rounds
    """
    flag, note = "NEUTRAL", ""
    played = season_length - games_remaining
    max_swing = 3 * games_remaining
    in_drop_zone = position > num_teams - relegation_spots

    if played < early_season_games:
        note = f"Early season ({played} played) — table not yet meaningful."
    elif status == "CHAMPIONS":
        flag, note = "REST_EXPECTED", "Title already won. Expect rotation."
    elif status == "RELEGATED":
        flag, note = "REST_EXPECTED", "Relegated. Expect youth/rotation."
    elif (in_drop_zone and games_remaining <= 10) or (
        points_above_relegation <= 3 and games_remaining <= 8
    ):
        flag = "DESPERATE"
        note = (
            f"#{position}, {points_above_relegation:+d} pts vs drop zone, "
            f"{games_remaining} left. Relegation fight."
        )
    elif (
        games_remaining <= 5
        and points_above_relegation > max_swing
        and points_behind_europe > max_swing
        and points_behind_leader > max_swing
    ):
        flag = "REST_EXPECTED"
        note = f"#{position}, safe with nothing to play for — dead rubber."
    elif position <= 2 or (games_remaining <= 10 and (
        0 < points_behind_leader <= 3 or 0 < points_behind_europe <= 3 or position <= europe_spots
    )):
        flag = "FIGHTING"
        note = f"#{position}, title / European places still in play."

    return StandingsInfo(
        team=team,
        record=record,
        position=position,
        points=points,
        games_remaining=games_remaining,
        status=status,
        motivation_flag=flag,
        motivation_note=note,
    )
