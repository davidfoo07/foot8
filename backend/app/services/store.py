"""Persist matchday analyses to the database."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.db import MarketQuoteRow, MatchPredictionRow, PredictionRun
from app.services.match_day import MatchAnalysis

MODEL_VERSION = "foot8-v1"


def save_run(
    session: Session, analyses: list[MatchAnalysis], leagues: list[str], days: int,
) -> PredictionRun:
    run = PredictionRun(leagues=leagues, days=days, model_version=MODEL_VERSION)
    for a in analyses:
        row = MatchPredictionRow(
            league=a.league, slug=a.slug, kickoff=a.kickoff, home=a.home, away=a.away,
            lambda_home=a.lambda_home, lambda_away=a.lambda_away,
            home_win=a.probs["home_win"], draw=a.probs["draw"],
            away_win=a.probs["away_win"], btts=a.probs["btts"],
            confidence=a.confidence, motivation=list(a.motivation),
            absences={"home": a.home_absences, "away": a.away_absences},
            warnings=a.warnings,
        )
        for m in a.markets:
            row.quotes.append(MarketQuoteRow(
                market_type=m.market_type, line=m.line, label=m.label, price=m.price,
                model_prob=m.model_prob, best_side=m.edge.best_side, edge=m.edge.best_edge,
                verdict=m.edge.verdict, kelly_pct=m.edge.suggested_bet_pct,
                liquidity=m.liquidity,
            ))
        run.matches.append(row)
    session.add(run)
    session.commit()
    return run
