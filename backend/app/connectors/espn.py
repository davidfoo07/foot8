"""ESPN soccer scoreboard — results for European domestic leagues and UEFA
competitions, keyed by ESPN team id.

Used for cross-league ratings: domestic matches rate teams within a league,
European matches (UCL / UEL / UECL incl. qualifiers) link the leagues.  One
team id across every competition means no cross-source name matching.

The scoreboard only accepts single dates, so the league calendar (returned
with any scoreboard) is used to fetch only days with matches.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from loguru import logger

from app.connectors.http import FetchError, fetch_json, is_cached

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer"

DOMESTIC_LEAGUES: dict[str, str] = {
    "eng.1": "England", "esp.1": "Spain", "ger.1": "Germany", "ita.1": "Italy",
    "fra.1": "France", "por.1": "Portugal", "ned.1": "Netherlands", "bel.1": "Belgium",
    "tur.1": "Turkey", "sco.1": "Scotland", "gre.1": "Greece", "aut.1": "Austria",
    "sui.1": "Switzerland", "den.1": "Denmark", "nor.1": "Norway", "swe.1": "Sweden",
    "cze.1": "Czechia", "cyp.1": "Cyprus", "isr.1": "Israel", "rou.1": "Romania",
}
EUROPEAN_COMPETITIONS: dict[str, str] = {
    "uefa.champions": "UCL", "uefa.champions_qual": "UCL-Q",
    "uefa.europa": "UEL", "uefa.europa_qual": "UEL-Q",
    "uefa.europa.conf": "UECL", "uefa.europa.conf_qual": "UECL-Q",
}


@dataclass(frozen=True)
class EspnMatch:
    competition: str          # ESPN slug
    event_id: str
    kickoff: datetime
    home_id: str
    home_name: str
    away_id: str
    away_name: str
    home_goals: int | None    # 90-minute + extra time score as ESPN reports it
    away_goals: int | None
    neutral: bool
    status: str               # STATUS_FULL_TIME, STATUS_SCHEDULED, ...

    @property
    def is_result(self) -> bool:
        return self.home_goals is not None and self.status in FINISHED

    @property
    def is_european(self) -> bool:
        return self.competition in EUROPEAN_COMPETITIONS


FINISHED = {"STATUS_FULL_TIME", "STATUS_FINAL", "STATUS_FINAL_AET", "STATUS_FINAL_PEN",
            "STATUS_END_OF_EXTRATIME", "STATUS_FINAL_ET"}


_lock = threading.Lock()
_last_request = [0.0]
MIN_INTERVAL = 0.25          # seconds between uncached requests (ESPN 403s on bursts)
BACKOFF = (10.0, 30.0, 90.0)


def _throttle() -> None:
    with _lock:
        wait = _last_request[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_request[0] = time.monotonic()


def _scoreboard(
    slug: str, day: date, today: date, cache_only_before: date | None = None,
) -> dict[str, Any]:
    ttl = None if day < today - timedelta(days=3) else 900
    url, params = f"{BASE_URL}/{slug}/scoreboard", {"dates": day.strftime("%Y%m%d")}
    if is_cached(url, params, ttl):
        return fetch_json(url, params, ttl_seconds=ttl)
    if cache_only_before is not None and day < cache_only_before:
        return {}
    for attempt, pause in enumerate((0.0, *BACKOFF)):
        if pause:
            logger.info(f"ESPN throttled; retrying {slug} {day} in {pause:.0f}s")
            time.sleep(pause)
        _throttle()
        try:
            return fetch_json(url, params, ttl_seconds=ttl)
        except FetchError as e:
            if attempt == len(BACKOFF) or not any(c in str(e) for c in ("403", "429")):
                logger.warning(f"ESPN {slug} {day}: {e}")
                return {}
    return {}


def parse_events(slug: str, raw: dict[str, Any]) -> list[EspnMatch]:
    out: list[EspnMatch] = []
    for e in raw.get("events", []):
        comp = (e.get("competitions") or [{}])[0]
        teams = {c.get("homeAway"): c for c in comp.get("competitors", [])}
        h, a = teams.get("home"), teams.get("away")
        if not h or not a:
            continue
        status = ((comp.get("status") or {}).get("type") or {}).get("name", "")
        played = status in FINISHED
        out.append(EspnMatch(
            competition=slug,
            event_id=str(e.get("id")),
            kickoff=datetime.fromisoformat(e["date"].replace("Z", "+00:00")),
            home_id=str(h["team"]["id"]), home_name=h["team"].get("displayName", ""),
            away_id=str(a["team"]["id"]), away_name=a["team"].get("displayName", ""),
            home_goals=_goals(h) if played else None,
            away_goals=_goals(a) if played else None,
            neutral=bool(comp.get("neutralSite")),
            status=status,
        ))
    return out


def _goals(c: dict[str, Any]) -> int | None:
    try:
        return int(c.get("score"))
    except (TypeError, ValueError):
        return None


def _calendar_days(raw: dict[str, Any]) -> set[date]:
    """Match days from a scoreboard's league calendar (day list or stage ranges)."""
    days: set[date] = set()
    for lg in raw.get("leagues", []):
        for entry in lg.get("calendar", []):
            if isinstance(entry, str):
                days.add(datetime.fromisoformat(entry.replace("Z", "+00:00")).date())
                continue
            for stage in entry.get("entries", []) or [entry]:
                try:
                    s = datetime.fromisoformat(stage["startDate"].replace("Z", "+00:00")).date()
                    t = datetime.fromisoformat(stage["endDate"].replace("Z", "+00:00")).date()
                except (KeyError, ValueError):
                    continue
                while s <= t:
                    days.add(s)
                    s += timedelta(days=1)
    return days


def load_competition(
    slug: str, start: date, end: date, today: date | None = None, workers: int = 4,
    cache_only_before: date | None = None,
) -> list[EspnMatch]:
    """All matches of one competition between start and end (inclusive).

    cache_only_before: days earlier than this are served from the disk cache
    only (the API uses it so a request never waits on ESPN rate limits).
    """
    today = today or datetime.now(UTC).date()
    probes, d = [], date(start.year, start.month, 15)
    while d <= end + timedelta(days=200):
        probes.append(d)
        d = date(d.year + (d.month + 3 > 12), (d.month + 3 - 1) % 12 + 1, 15)
    days: set[date] = set()
    for p in probes:
        days |= _calendar_days(_scoreboard(slug, p, today, cache_only_before))
    days = {x for x in days if start <= x <= end}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        raws = list(pool.map(lambda x: _scoreboard(slug, x, today, cache_only_before),
                             sorted(days)))
    matches = {m.event_id: m for raw in raws for m in parse_events(slug, raw)}
    return sorted(matches.values(), key=lambda m: m.kickoff)


def load_all(
    start: date, end: date, slugs: list[str] | None = None, today: date | None = None,
    cache_only_before: date | None = None,
) -> list[EspnMatch]:
    slugs = slugs or [*DOMESTIC_LEAGUES, *EUROPEAN_COMPETITIONS]
    out: list[EspnMatch] = []
    for slug in slugs:
        ms = load_competition(slug, start, end, today, cache_only_before=cache_only_before)
        logger.info(f"ESPN {slug}: {len(ms)} matches")
        out += ms
    return sorted(out, key=lambda m: m.kickoff)
