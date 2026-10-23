"""League table from Understat results → motivation inputs."""

from __future__ import annotations

from datetime import date

from app.analytics.schedule_engine import determine_motivation
from app.connectors.understat import MatchRecord
from app.schemas.team import StandingsInfo

EUROPE_SPOTS = {"EPL": 6, "LALIGA": 6}
RELEGATION_SPOTS = {"EPL": 3, "LALIGA": 3}


def league_table(matches: list[MatchRecord], as_of: date) -> list[dict]:
    """Rows sorted by points, goal difference, goals for."""
    teams = {m.home for m in matches} | {m.away for m in matches}
    t = {name: {"team": name, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0} for name in teams}
    for m in matches:
        if not m.is_result or m.date >= as_of:
            continue
        hg, ag = m.home_goals or 0, m.away_goals or 0
        t[m.home]["gf"] += hg
        t[m.home]["ga"] += ag
        t[m.away]["gf"] += ag
        t[m.away]["ga"] += hg
        if hg > ag:
            t[m.home]["w"] += 1
            t[m.away]["l"] += 1
        elif hg < ag:
            t[m.away]["w"] += 1
            t[m.home]["l"] += 1
        else:
            t[m.home]["d"] += 1
            t[m.away]["d"] += 1
    rows = list(t.values())
    for r in rows:
        r["played"] = r["w"] + r["d"] + r["l"]
        r["points"] = 3 * r["w"] + r["d"]
        r["gd"] = r["gf"] - r["ga"]
    rows.sort(key=lambda r: (-r["points"], -r["gd"], -r["gf"], r["team"]))
    for i, r in enumerate(rows, 1):
        r["position"] = i
    return rows


def motivation_for(
    league: str, matches: list[MatchRecord], as_of: date, season_length: int = 38,
) -> dict[str, StandingsInfo]:
    table = league_table(matches, as_of)
    n = len(table)
    if n == 0:
        return {}
    releg, europe = RELEGATION_SPOTS.get(league, 3), EUROPE_SPOTS.get(league, 6)
    first_drop_pts = table[n - releg]["points"]
    europe_pts = table[min(europe, n) - 1]["points"]
    leader_pts = table[0]["points"]
    out: dict[str, StandingsInfo] = {}
    for r in table:
        out[r["team"]] = determine_motivation(
            team=r["team"],
            record=f"{r['w']}-{r['d']}-{r['l']}",
            position=r["position"],
            points=r["points"],
            games_remaining=season_length - r["played"],
            points_above_relegation=r["points"] - first_drop_pts,
            points_behind_europe=europe_pts - r["points"],
            points_behind_leader=leader_pts - r["points"],
            num_teams=n,
            relegation_spots=releg,
            europe_spots=europe,
            season_length=season_length,
        )
    return out
