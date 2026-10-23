"""Champions League pipeline.

Polymarket UCL fixtures (series 10204) + "(To Advance)" tie markets
  → matched to ESPN fixtures by date and name similarity (team ids)
  → cross-league global model fitted on ~3 seasons of domestic + UEFA results
  → match markets priced from expected goals (UEFA home advantage; finals neutral)
  → knockout second legs / ties priced with two_leg_tie (ET + penalties)

No lineup / injury adjustment yet: there is no free cross-league injury feed.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from loguru import logger

from app.analytics.edge_calculator import calculate_edge, cap_verdict
from app.analytics.global_ratings import EURO_GROUP, GlobalConfig, GlobalRatings, fit_global
from app.analytics.knockout import single_match_advance, two_leg_tie
from app.analytics.prediction_model import predict_from_lambdas, score_matrix
from app.connectors import espn
from app.connectors import polymarket as pm
from app.connectors.http import fetch_json
from app.connectors.team_names import normalise
from app.schemas.market import EdgeResult
from app.services.match_day import MAX_VERDICT, MatchAnalysis, price_markets

HISTORY_START = date(2023, 7, 1)
UCL = "uefa.champions"
KNOCKOUT_GAP_DAYS = (5, 15)   # leg 1 → leg 2 spacing in UCL knockouts

_STOP = {"fc", "cf", "sc", "sk", "ac", "as", "afc", "club", "de", "fk", "sv", "vfb", "vfl",
         "bc", "cp", "sl", "ssc", "rc", "the", "1", "04", "05", "09", "1899", "1909"}
_ALIAS = {"munchen": "munich", "munich": "munich", "psg": "paris saint germain",
          "inter": "internazionale", "internazionale": "internazionale",
          "man": "manchester", "utd": "united", "atletico": "atletico",
          "atleti": "atletico", "olympiakos": "olympiacos", "olympiacos": "olympiacos",
          "sporting": "sporting", "spurs": "tottenham", "qarabag": "qarabag",
          "bodo": "bodo", "glimt": "glimt", "leverkusen": "leverkusen"}


def _key(name: str) -> str:
    toks = [_ALIAS.get(t, t) for t in normalise(name).replace("-", " ").split()]
    return " ".join(t for t in toks if t not in _STOP) or normalise(name)


def name_similarity(a: str, b: str) -> float:
    """Token coverage: every token of the shorter name must fuzzy-match a token
    of the longer one ("Olympiakós SFP" ~ "Olympiacos", but "Real Madrid" is
    not "Atletico Madrid")."""
    ka, kb = _key(a), _key(b)
    if ka == kb:
        return 1.0
    ta, tb = ka.split(), kb.split()
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    scores = [max(difflib.SequenceMatcher(None, t, u).ratio() for u in long_) for t in short]
    return min(scores)


def match_fixture(
    home: str, away: str, kickoff: datetime, candidates: list[espn.EspnMatch],
    max_days: int = 1, min_score: float = 0.55,
) -> espn.EspnMatch | None:
    """Best ESPN fixture for a Polymarket "Home vs. Away" within ±max_days."""
    best, best_score = None, 0.0
    for m in candidates:
        if abs((m.kickoff.date() - kickoff.date()).days) > max_days:
            continue
        s = min(name_similarity(home, m.home_name), name_similarity(away, m.away_name))
        if s > best_score:
            best, best_score = m, s
    return best if best_score >= min_score else None


@dataclass
class TiePricing:
    team_a: str           # leg-1 home side
    team_b: str
    leg1_score: tuple[int, int] | None
    advance_a: float
    detail: dict[str, float]
    market_price_a: float | None = None
    edge: EdgeResult | None = None


@dataclass
class UclAnalysis:
    match: MatchAnalysis
    espn_event: str
    tie: TiePricing | None = None
    notes: list[str] = field(default_factory=list)


def load_history(
    today: date | None = None, days_ahead: int = 21, cache_only: bool = False,
) -> list[espn.EspnMatch]:
    """ESPN results + upcoming fixtures.  cache_only: past days only from the
    disk cache (recent days and fixtures are still fetched)."""
    today = today or datetime.now(UTC).date()
    return espn.load_all(HISTORY_START, today + timedelta(days=days_ahead), today=today,
                         cache_only_before=today - timedelta(days=14) if cache_only else None)


def _first_leg(m: espn.EspnMatch, history: list[espn.EspnMatch]) -> espn.EspnMatch | None:
    """The reversed fixture 5-15 days earlier in the same competition (knockouts)."""
    lo, hi = KNOCKOUT_GAP_DAYS
    for x in history:
        gap = (m.kickoff - x.kickoff).days
        if (x.competition == m.competition and x.home_id == m.away_id
                and x.away_id == m.home_id and lo <= gap <= hi):
            return x
    return None


def _is_final(m: espn.EspnMatch) -> bool:
    return m.neutral and m.competition == UCL


def analyse_ucl(
    days: int = 7,
    today: date | None = None,
    cfg: GlobalConfig | None = None,
    history: list[espn.EspnMatch] | None = None,
) -> list[UclAnalysis]:
    today = today or datetime.now(UTC).date()
    history = history if history is not None else load_history(today)
    ratings = fit_global(history, today, cfg)
    ucl_fixtures = [m for m in history if m.competition == UCL]
    advance_markets = fetch_advance_markets()

    out: list[UclAnalysis] = []
    for mm in pm.fetch_upcoming("UCL", days, today):
        fx = match_fixture(mm.home, mm.away, mm.kickoff, ucl_fixtures)
        if fx is None:
            logger.warning(f"{mm.slug}: no ESPN fixture for {mm.home} vs {mm.away}")
            continue
        out.append(_analyse_fixture(mm, fx, ratings, history, advance_markets))
    return out


def _analyse_fixture(
    mm: pm.MatchMarkets,
    fx: espn.EspnMatch,
    ratings: GlobalRatings,
    history: list[espn.EspnMatch],
    advance_markets: list[dict[str, Any]],
) -> UclAnalysis:
    notes: list[str] = []
    neutral = _is_final(fx)
    lh, la = ratings.expected_goals(fx.home_id, fx.away_id, EURO_GROUP, neutral=neutral)
    for tid, name in ((fx.home_id, fx.home_name), (fx.away_id, fx.away_name)):
        if ratings.team_matches.get(tid, 0.0) < 8:
            notes.append(f"{name}: thin history ({ratings.team_matches.get(tid, 0):.0f} "
                         "weighted matches) — rating is mostly its league average")
    confidence = "LOW" if any("thin history" in n for n in notes) else "MEDIUM"
    pred = predict_from_lambdas(lh, la, confidence=confidence)
    matrix = score_matrix(lh, la)

    analysis = MatchAnalysis(
        league="UCL", slug=mm.slug, kickoff=mm.kickoff, home=fx.home_name, away=fx.away_name,
        lambda_home=pred.lambda_home, lambda_away=pred.lambda_away,
        probs={"home_win": pred.home_win_prob, "draw": pred.draw_prob,
               "away_win": pred.away_win_prob, "btts": pred.btts_prob},
        home_absences=[], away_absences=[], motivation=("NEUTRAL", "NEUTRAL"),
        confidence=confidence,
        warnings=["No injury adjustment for UCL (no cross-league feed)", *notes],
    )
    analysis.markets = price_markets(mm, fx.home_name, fx.away_name, pred, matrix, confidence)

    tie = None
    leg1 = _first_leg(fx, history)
    if leg1 is not None:
        # fx is leg 2 (B at home); A = leg-1 home side = fx.away
        l1 = ratings.expected_goals(leg1.home_id, leg1.away_id, EURO_GROUP)
        score = ((leg1.home_goals or 0, leg1.away_goals or 0) if leg1.is_result else None)
        detail = two_leg_tie(l1, (lh, la), leg1_score=score)
        tie = TiePricing(fx.away_name, fx.home_name, score, detail["advance"], detail)
    elif neutral:
        detail = single_match_advance(lh, la)
        tie = TiePricing(fx.home_name, fx.away_name, None, detail["advance"], detail)
    if tie is not None:
        _attach_advance_price(tie, advance_markets)
    return UclAnalysis(match=analysis, espn_event=fx.event_id, tie=tie, notes=notes)


# ─── "(To Advance)" markets ──────────────────────────────────────────

_ADVANCE = re.compile(r"Champions League:\s*(.+?)\s+vs\.?\s+(.+?)\s*\(To Advance\)", re.I)


def fetch_advance_markets() -> list[dict[str, Any]]:
    """Open per-tie advance markets: [{team_a, team_b, price_a, liquidity, slug}]."""
    try:
        raw = fetch_json(f"{pm.GAMMA_URL}/public-search",
                         {"q": "Champions League To Advance", "limit_per_type": 50},
                         ttl_seconds=300)
    except Exception as e:  # noqa: BLE001 — optional market type
        logger.warning(f"advance market search failed: {e}")
        return []
    return parse_advance_events(raw.get("events", []))


def parse_advance_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for e in events:
        found = _ADVANCE.search(e.get("title", ""))
        if not found or e.get("closed"):
            continue
        for m in e.get("markets", []):
            outcomes = pm._list(m.get("outcomes"))
            prices = pm._list(m.get("outcomePrices"))
            if len(outcomes) == 2 and len(prices) == 2:
                out.append({
                    "slug": e.get("slug"), "team_a": outcomes[0], "team_b": outcomes[1],
                    "price_a": float(prices[0]),
                    "liquidity": float(m.get("liquidityNum") or 0),
                })
    return out


def _attach_advance_price(tie: TiePricing, markets: list[dict[str, Any]]) -> None:
    for mk in markets:
        direct = min(name_similarity(tie.team_a, mk["team_a"]),
                     name_similarity(tie.team_b, mk["team_b"]))
        flipped = min(name_similarity(tie.team_a, mk["team_b"]),
                      name_similarity(tie.team_b, mk["team_a"]))
        if max(direct, flipped) < 0.55:
            continue
        price_a = mk["price_a"] if direct >= flipped else 1.0 - mk["price_a"]
        if not 0.01 <= price_a <= 0.99:
            return
        tie.market_price_a = price_a
        tie.edge = cap_verdict(calculate_edge(tie.advance_a, price_a), MAX_VERDICT)
        return
