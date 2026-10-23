"""Understat connector — match-level xG for the big European leagues.

Understat serves each league-season as JSON from /getLeagueData/{league}/{season}
(season = starting year: 2025 → 2025-26).  The response holds:
  teams:   per-team match history (xG, xGA, goals, h/a, date)
  players: season aggregates (minutes, xG, xA, xGChain, position)
  dates:   every fixture with result + xG once played
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from app.connectors.http import fetch_json

BASE_URL = "https://understat.com"

# Foot8 league code → Understat league name
LEAGUES: dict[str, str] = {
    "EPL": "EPL",
    "LALIGA": "La_liga",
    "BUNDESLIGA": "Bundesliga",
    "SERIEA": "Serie_A",
    "LIGUE1": "Ligue_1",
}


@dataclass(frozen=True)
class MatchRecord:
    """One fixture.  Goals / xG are None until the match is played."""
    match_id: str
    league: str
    season: int
    kickoff: datetime
    home: str
    away: str
    home_goals: int | None
    away_goals: int | None
    home_xg: float | None
    away_xg: float | None

    @property
    def date(self) -> date:
        return self.kickoff.date()

    @property
    def is_result(self) -> bool:
        return self.home_goals is not None


def current_season(today: date | None = None) -> int:
    """Season starting year: Aug-Dec → this year, Jan-Jul → last year."""
    today = today or date.today()
    return today.year if today.month >= 7 else today.year - 1


def fetch_league_season(league: str, season: int, today: date | None = None) -> dict[str, Any]:
    """Raw league-season JSON.  Finished seasons are cached forever, the
    current one for 30 minutes."""
    name = LEAGUES[league]
    ttl = 1800 if season >= current_season(today) else None
    return fetch_json(
        f"{BASE_URL}/getLeagueData/{name}/{season}",
        headers={
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE_URL}/league/{name}/{season}",
        },
        ttl_seconds=ttl,
    )


def parse_matches(raw: dict[str, Any], league: str, season: int) -> list[MatchRecord]:
    out: list[MatchRecord] = []
    for m in raw.get("dates", []):
        played = bool(m.get("isResult"))
        goals, xg = m.get("goals") or {}, m.get("xG") or {}
        out.append(MatchRecord(
            match_id=str(m["id"]),
            league=league,
            season=season,
            kickoff=datetime.fromisoformat(m["datetime"]),
            home=m["h"]["title"],
            away=m["a"]["title"],
            home_goals=int(goals["h"]) if played and goals.get("h") is not None else None,
            away_goals=int(goals["a"]) if played and goals.get("a") is not None else None,
            home_xg=float(xg["h"]) if played and xg.get("h") is not None else None,
            away_xg=float(xg["a"]) if played and xg.get("a") is not None else None,
        ))
    out.sort(key=lambda r: r.kickoff)
    return out


def parse_players(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Season player rows with numeric fields converted.

    Understat has no on/off splits, so the player-impact model uses
    xG + xA per 90 (own contribution) as its signal — see ratings service.
    """
    rows = []
    for p in raw.get("players", []):
        minutes = float(p.get("time", 0) or 0)
        games = float(p.get("games", 0) or 0)
        rows.append({
            "player_id": str(p["id"]),
            "name": p["player_name"],
            "team": p["team_title"].split(",")[-1].strip(),  # mid-season movers: "A,B"
            "position": p.get("position", ""),
            "games": games,
            "minutes": minutes,
            "mpg": minutes / games if games else 0.0,
            "xg": float(p.get("xG", 0) or 0),
            "xa": float(p.get("xA", 0) or 0),
            "npxg": float(p.get("npxG", 0) or 0),
            "xg_chain": float(p.get("xGChain", 0) or 0),
        })
    return rows


def load_matches(league: str, season: int, today: date | None = None) -> list[MatchRecord]:
    return parse_matches(fetch_league_season(league, season, today), league, season)
