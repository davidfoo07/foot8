"""Fantasy Premier League API — EPL injuries/suspensions and per-player xG.

bootstrap-static lists every EPL player with:
  status: a (available), d (doubtful), i (injured), s (suspended),
          u (unavailable / left), n (not in squad)
  chance_of_playing_next_round: 0 / 25 / 50 / 75 / 100 / None
  minutes, starts, expected_goals, expected_assists,
  expected_goals_conceded (team xGA while he was on the pitch)
"""

from __future__ import annotations

from typing import Any

from app.connectors.http import fetch_json

BOOTSTRAP_URL = "https://fantasy.premierleague.com/api/bootstrap-static/"
POSITIONS = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}


def fetch_bootstrap(ttl_seconds: float = 1800) -> dict[str, Any]:
    return fetch_json(BOOTSTRAP_URL, ttl_seconds=ttl_seconds)


def _status(p: dict[str, Any]) -> str | None:
    """FPL status → foot8 absence status (None = expected to play)."""
    chance = p.get("chance_of_playing_next_round")
    st = p.get("status")
    if st == "s":
        return "SUSPENDED"
    if st == "i" or (st == "d" and chance == 0):
        return "OUT"
    if st == "d":
        return {25: "DOUBTFUL", 50: "QUESTIONABLE"}.get(chance)
    return None


def parse_players(raw: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """(absences, metrics_by_name).

    absences rows: {player_id, player_name, team, status, reason, position}
    metrics rows:  {MIN (per start-equivalent appearance), GP, POS, XGI90,
                    ON_XGA90, team}
    """
    teams = {t["id"]: t["name"] for t in raw.get("teams", [])}
    absences: list[dict[str, Any]] = []
    metrics: dict[str, dict[str, Any]] = {}
    for p in raw.get("elements", []):
        name = f"{p.get('first_name', '')} {p.get('second_name', '')}".strip()
        team = teams.get(p.get("team"), "")
        pos = POSITIONS.get(p.get("element_type"), "UNK")
        minutes = float(p.get("minutes") or 0)
        starts = float(p.get("starts") or 0)
        if minutes > 0:
            apps = max(starts, minutes / 90.0)
            metrics[name] = {
                "team": team,
                "POS": pos,
                "MIN": min(90.0, minutes / apps),
                "GP": apps,
                "XGI90": (float(p.get("expected_goals") or 0)
                          + float(p.get("expected_assists") or 0)) * 90.0 / minutes,
                "ON_XGA90": float(p.get("expected_goals_conceded") or 0) * 90.0 / minutes,
            }
        status = _status(p)
        if status:
            absences.append({
                "player_id": str(p.get("id")),
                "player_name": name,
                "team": team,
                "status": status,
                "reason": p.get("news") or None,
                "position": pos,
            })
    return absences, metrics
