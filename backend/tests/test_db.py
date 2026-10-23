"""DB layer tests against in-memory SQLite (same models as Supabase Postgres)."""

from datetime import UTC, datetime

import pytest

from app import db
from app.analytics.edge_calculator import calculate_edge
from app.services.match_day import MatchAnalysis, PricedMarket
from app.services.store import save_run


def test_database_url_normalisation(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secret@host.pooler.supabase.com:5432/postgres")
    assert db.database_url().startswith("postgresql+psycopg2://")
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:[YOUR-PASSWORD]@h:5432/postgres")
    with pytest.raises(db.DatabaseNotConfiguredError):
        db.database_url()


def test_save_run_roundtrip():
    engine = db.init_db(db.make_engine("sqlite://"))
    a = MatchAnalysis(
        league="EPL", slug="epl-ars-eve-2026-10-24", kickoff=datetime(2026, 10, 24, 14, tzinfo=UTC),
        home="Arsenal", away="Everton", lambda_home=1.7, lambda_away=0.7,
        probs={"home_win": 0.62, "draw": 0.24, "away_win": 0.14, "btts": 0.4},
        home_absences=["Saka"], away_absences=[], motivation=("FIGHTING", "NEUTRAL"),
        confidence="MEDIUM",
        markets=[PricedMarket("home_win", None, "Arsenal win", 0.68, 0.62,
                              calculate_edge(0.62, 0.68), 125000.0)],
    )
    with db.session_factory(engine)() as s:
        run = save_run(s, [a], ["EPL"], 7)
        assert run.id is not None
        stored = s.query(db.MarketQuoteRow).one()
        assert stored.best_side == "NO" and stored.match.home == "Arsenal"
        assert s.query(db.MatchPredictionRow).one().absences == {"home": ["Saka"], "away": []}
