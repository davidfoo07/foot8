"""Polymarket connector — football match markets via the Gamma API, price
history via the CLOB API.

Each match is several Gamma events sharing a slug stem:
  epl-ips-not-2026-10-23               moneyline ×3 (home win / draw / away win)
  epl-ips-not-2026-10-23-more-markets  spreads, totals, team totals, BTTS, halves
  …-exact-score, -halftime-result, -first-to-score, -total-corners
Event titles are "Home vs. Away" (the series' ordering is home-first).
"""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from loguru import logger

from app.connectors.http import FetchError, fetch_json
from app.connectors.team_names import to_canonical

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"

SERIES: dict[str, str] = {"EPL": "10188", "LALIGA": "10193", "UCL": "10204"}
_MAIN_SLUG = re.compile(r"^[a-z0-9]+-[a-z0-9]+-[a-z0-9]+-\d{4}-\d{2}-\d{2}$")
_LINE = re.compile(r"\(([+-]?\d+(?:\.\d+)?)\)")


@dataclass
class OutcomeMarket:
    """One binary market.  ``yes_price`` is the price of ``yes_label``."""
    market_type: str          # home_win, draw, away_win, asian_handicap, total, btts
    line: float | None        # home handicap / total line
    yes_label: str
    yes_price: float | None
    yes_token: str | None
    question: str
    liquidity: float = 0.0


@dataclass
class MatchMarkets:
    league: str
    slug: str
    kickoff: datetime
    home: str                 # Polymarket name
    away: str
    markets: list[OutcomeMarket] = field(default_factory=list)
    closed: bool = False

    def get(self, market_type: str, line: float | None = None) -> OutcomeMarket | None:
        for m in self.markets:
            if m.market_type == market_type and (line is None or m.line == line):
                return m
        return None


def _list(value: Any) -> list[Any]:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return []
    return list(value or [])


def _price(m: dict[str, Any], idx: int = 0) -> float | None:
    prices = _list(m.get("outcomePrices"))
    try:
        return float(prices[idx])
    except (IndexError, TypeError, ValueError):
        return None


def _token(m: dict[str, Any], idx: int = 0) -> str | None:
    toks = _list(m.get("clobTokenIds"))
    return str(toks[idx]) if len(toks) > idx else None


def fetch_events(
    league: str,
    closed: bool,
    start: date | None = None,
    end: date | None = None,
    page_size: int = 100,
    max_pages: int = 60,
    ttl_seconds: float | None = 300,
) -> list[dict[str, Any]]:
    """All Gamma events of a league series (paginated)."""
    params: dict[str, Any] = {
        "series_id": SERIES[league], "closed": str(closed).lower(),
        "limit": page_size, "order": "startDate", "ascending": "true",
    }
    if start:
        params["end_date_min"] = f"{start.isoformat()}T00:00:00Z"
    if end:
        params["end_date_max"] = f"{end.isoformat()}T23:59:59Z"
    # Gamma stops paginating around offset 2000, so long ranges are fetched
    # in 30-day windows.
    if start and end and (end - start).days > 30:
        events: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            window_end = min(cursor + timedelta(days=29), end)
            events.extend(fetch_events(league, closed, cursor, window_end, page_size,
                                       max_pages, ttl_seconds))
            cursor = window_end + timedelta(days=1)
        return list({e["id"]: e for e in events}.values())

    events = []
    for page in range(max_pages):
        batch = fetch_json(f"{GAMMA_URL}/events", {**params, "offset": page * page_size},
                           ttl_seconds=ttl_seconds)
        events.extend(batch)
        if len(batch) < page_size:
            break
    return events


def parse_match_events(league: str, events: list[dict[str, Any]]) -> list[MatchMarkets]:
    """Group Gamma events into one MatchMarkets per fixture."""
    stems: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        slug = e.get("slug", "")
        stem = slug if _MAIN_SLUG.match(slug) else re.sub(r"-(more-markets)$", "", slug)
        if _MAIN_SLUG.match(stem):
            stems.setdefault(stem, []).append(e)

    out: list[MatchMarkets] = []
    for stem, evs in stems.items():
        main = next((e for e in evs if e["slug"] == stem), None)
        if main is None or " vs. " not in main.get("title", ""):
            continue
        home, away = (s.strip() for s in main["title"].split(" vs. ", 1))
        kickoff = _kickoff(main)
        if kickoff is None:
            continue
        mm = MatchMarkets(league=league, slug=stem, kickoff=kickoff, home=home, away=away,
                          closed=bool(main.get("closed")))
        for e in evs:
            for m in e.get("markets", []):
                parsed = _parse_market(m, home, away)
                if parsed:
                    mm.markets.append(parsed)
        out.append(mm)
    out.sort(key=lambda x: x.kickoff)
    return out


def _kickoff(e: dict[str, Any]) -> datetime | None:
    for m in e.get("markets", []):
        gst = m.get("gameStartTime")
        if gst:
            try:
                return datetime.fromisoformat(gst.replace("Z", "+00:00")).astimezone(UTC)
            except ValueError:
                pass
    end = e.get("endDate")
    return datetime.fromisoformat(end.replace("Z", "+00:00")) if end else None


def _parse_market(m: dict[str, Any], home: str, away: str) -> OutcomeMarket | None:
    smt = m.get("sportsMarketType")
    q = m.get("question", "")
    if smt is None:  # early-2025 markets carry no type tag
        smt = _infer_type(q)
    liq = float(m.get("liquidityNum") or m.get("liquidity") or 0)
    if smt == "moneyline":
        title = (m.get("groupItemTitle") or "").strip()
        if title.lower().startswith("draw"):
            mtype = "draw"
        elif title == home or home in q:
            mtype = "home_win"
        elif title == away or away in q:
            mtype = "away_win"
        else:
            return None
        return OutcomeMarket(mtype, None, "Yes", _price(m), _token(m), q, liq)
    if smt == "totals":
        line = m.get("line")
        return OutcomeMarket("total", float(line) if line is not None else None, "Over",
                             _price(m), _token(m), q, liq)
    if smt == "both_teams_to_score":
        return OutcomeMarket("btts", None, "Yes", _price(m), _token(m), q, liq)
    if smt == "spreads":
        outcomes = _list(m.get("outcomes"))
        line = m.get("line")
        if line is None:
            found = _LINE.search(q)
            line = float(found.group(1)) if found else None
        if not outcomes or line is None:
            return None
        # Line belongs to outcomes[0]; express as the HOME handicap with YES = home.
        if outcomes[0] == home:
            return OutcomeMarket("asian_handicap", float(line), home, _price(m, 0),
                                 _token(m, 0), q, liq)
        if outcomes[0] == away:
            return OutcomeMarket("asian_handicap", -float(line), home, _price(m, 1),
                                 _token(m, 1), q, liq)
    return None


def _infer_type(question: str) -> str | None:
    ql = question.lower()
    if "end in a draw" in ql or re.search(r"^will .+ win on \d{4}-\d{2}-\d{2}", ql):
        return "moneyline"
    if ql.startswith("spread:"):
        return "spreads"
    if "both teams to score" in ql and "half" not in ql:
        return "both_teams_to_score"
    if re.search(r": o/u \d+(\.\d+)?$", ql):
        return "totals"
    return None


def canonical_teams(mm: MatchMarkets, candidates: set[str]) -> tuple[str | None, str | None]:
    return to_canonical(mm.home, candidates), to_canonical(mm.away, candidates)


def fetch_upcoming(league: str, days: int = 7, today: date | None = None) -> list[MatchMarkets]:
    today = today or datetime.now(UTC).date()
    events = fetch_events(league, closed=False, start=today, end=today + timedelta(days=days),
                          ttl_seconds=120)
    now = datetime.now(UTC)
    return [m for m in parse_match_events(league, events) if m.kickoff > now]


# ─── Price history (backtesting against real Polymarket prices) ──────

def price_at(token: str, ts: datetime, lookback_hours: float = 12.0) -> float | None:
    """Last traded/mid price of ``token`` at or before ``ts`` (None if no history)."""
    end = int(ts.timestamp())
    start = end - int(lookback_hours * 3600)
    try:
        data = fetch_json(
            f"{CLOB_URL}/prices-history",
            {"market": token, "startTs": start, "endTs": end, "fidelity": 10},
            ttl_seconds=None,
        )
    except FetchError as e:
        logger.warning(f"price history failed for {token[:12]}…: {e}")
        return None
    hist = [h for h in data.get("history", []) if h.get("t", 0) <= end]
    return float(hist[-1]["p"]) if hist else None


def snapshot_prices(
    matches: list[MatchMarkets],
    minutes_before: int = 60,
    market_types: tuple[str, ...] = ("home_win", "draw", "away_win", "total", "btts"),
    total_lines: tuple[float, ...] = (2.5,),
    workers: int = 8,
) -> dict[str, dict[str, float]]:
    """slug → {market key: price} as of ``minutes_before`` kick-off."""
    jobs: list[tuple[str, str, str, datetime]] = []
    for mm in matches:
        ts = mm.kickoff - timedelta(minutes=minutes_before)
        for m in mm.markets:
            if m.market_type not in market_types or not m.yes_token:
                continue
            if m.market_type == "total" and m.line not in total_lines:
                continue
            key = m.market_type if m.line is None else f"{m.market_type}:{m.line}"
            jobs.append((mm.slug, key, m.yes_token, ts))

    out: dict[str, dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        prices = list(pool.map(lambda j: price_at(j[2], j[3]), jobs))
    for (slug, key, _, _), p in zip(jobs, prices, strict=True):
        if p is not None:
            out.setdefault(slug, {})[key] = p
    return out
