"""football-data.co.uk connector — historic results with bookmaker odds.

Used for backtesting: Pinnacle closing odds are the sharpest public price,
so "does the model beat Pinnacle's close" is the real test of an edge.

CSV per league-season: /mmz4281/{yy}{yy+1}/{code}.csv, e.g. 2425/E0.csv.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date, datetime

from app.connectors.http import fetch_bytes

BASE_URL = "https://www.football-data.co.uk/mmz4281"

LEAGUE_CODES: dict[str, str] = {
    "EPL": "E0",
    "LALIGA": "SP1",
    "BUNDESLIGA": "D1",
    "SERIEA": "I1",
    "LIGUE1": "F1",
}


@dataclass(frozen=True)
class OddsRecord:
    date: date
    home: str
    away: str
    home_goals: int
    away_goals: int
    # Pinnacle closing (fallback: market average closing) decimal odds
    close_home: float | None
    close_draw: float | None
    close_away: float | None
    close_over25: float | None
    close_under25: float | None
    ah_line: float | None        # home handicap at close, e.g. -0.75
    close_ah_home: float | None
    close_ah_away: float | None
    # Pinnacle pre-match odds, collected Friday/Tuesday afternoon before the
    # round (≈ 1-2 days before kick-off) — usable without look-ahead.
    early_home: float | None = None
    early_draw: float | None = None
    early_away: float | None = None
    early_over25: float | None = None
    early_under25: float | None = None


def _season_code(season: int) -> str:
    return f"{season % 100:02d}{(season + 1) % 100:02d}"


def _num(row: dict[str, str], *keys: str) -> float | None:
    for k in keys:
        v = (row.get(k) or "").strip()
        if v:
            try:
                return float(v)
            except ValueError:
                continue
    return None


def _parse_date(s: str) -> date:
    for fmt in ("%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"bad date {s!r}")


def parse_csv(text: str) -> list[OddsRecord]:
    out: list[OddsRecord] = []
    for row in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        if not row.get("HomeTeam") or not (row.get("FTHG") or "").strip():
            continue
        out.append(OddsRecord(
            date=_parse_date(row["Date"]),
            home=row["HomeTeam"].strip(),
            away=row["AwayTeam"].strip(),
            home_goals=int(row["FTHG"]),
            away_goals=int(row["FTAG"]),
            close_home=_num(row, "PSCH", "AvgCH", "PSH"),
            close_draw=_num(row, "PSCD", "AvgCD", "PSD"),
            close_away=_num(row, "PSCA", "AvgCA", "PSA"),
            close_over25=_num(row, "PC>2.5", "AvgC>2.5", "P>2.5"),
            close_under25=_num(row, "PC<2.5", "AvgC<2.5", "P<2.5"),
            ah_line=_num(row, "AHCh", "AHh"),
            close_ah_home=_num(row, "PCAHH", "AvgCAHH", "PAHH"),
            close_ah_away=_num(row, "PCAHA", "AvgCAHA", "PAHA"),
            early_home=_num(row, "PSH"),
            early_draw=_num(row, "PSD"),
            early_away=_num(row, "PSA"),
            early_over25=_num(row, "P>2.5"),
            early_under25=_num(row, "P<2.5"),
        ))
    return out


def load_season(league: str, season: int, cache_forever: bool = True) -> list[OddsRecord]:
    url = f"{BASE_URL}/{_season_code(season)}/{LEAGUE_CODES[league]}.csv"
    raw = fetch_bytes(url, ttl_seconds=None if cache_forever else 3600)
    return parse_csv(raw.decode("utf-8", errors="replace"))


def devig(*odds: float) -> list[float]:
    """Decimal odds → fair probabilities (proportional overround removal)."""
    implied = [1.0 / o for o in odds]
    total = sum(implied)
    return [p / total for p in implied]
