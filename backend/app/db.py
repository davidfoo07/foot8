"""Database — Supabase Postgres via SQLAlchemy.

DATABASE_URL comes from the environment or backend/.env (gitignored).  The
Supabase session pooler requires SSL; the URL scheme is normalised to the
psycopg2 driver.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class DatabaseNotConfiguredError(RuntimeError):
    pass


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url or "[YOUR-PASSWORD]" in url or "<password>" in url:
        raise DatabaseNotConfiguredError(
            "Set DATABASE_URL (with the real password) in backend/.env"
        )
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


def make_engine(url: str | None = None) -> Engine:
    url = url or database_url()
    connect_args: dict = {}
    if url.startswith("postgresql") and "supabase" in url and "sslmode" not in url:
        connect_args["sslmode"] = "require"
    return create_engine(url, pool_pre_ping=True, connect_args=connect_args)


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class PredictionRun(Base):
    """One execution of scripts/predict.py."""
    __tablename__ = "prediction_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    leagues: Mapped[list] = mapped_column(JSON)
    days: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(32))

    matches: Mapped[list[MatchPredictionRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class MatchPredictionRow(Base):
    __tablename__ = "match_predictions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("prediction_runs.id", ondelete="CASCADE"),
                                        index=True)
    league: Mapped[str] = mapped_column(String(16))
    slug: Mapped[str] = mapped_column(String(128), index=True)
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    home: Mapped[str] = mapped_column(String(64))
    away: Mapped[str] = mapped_column(String(64))
    lambda_home: Mapped[float] = mapped_column(Float)
    lambda_away: Mapped[float] = mapped_column(Float)
    home_win: Mapped[float] = mapped_column(Float)
    draw: Mapped[float] = mapped_column(Float)
    away_win: Mapped[float] = mapped_column(Float)
    btts: Mapped[float] = mapped_column(Float)
    confidence: Mapped[str] = mapped_column(String(8))
    motivation: Mapped[list] = mapped_column(JSON)
    absences: Mapped[dict] = mapped_column(JSON)
    warnings: Mapped[list] = mapped_column(JSON)

    run: Mapped[PredictionRun] = relationship(back_populates="matches")
    quotes: Mapped[list[MarketQuoteRow]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class MarketQuoteRow(Base):
    """Polymarket price vs model probability for one market at prediction time."""
    __tablename__ = "market_quotes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    match_id: Mapped[int] = mapped_column(ForeignKey("match_predictions.id", ondelete="CASCADE"),
                                          index=True)
    market_type: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    label: Mapped[str] = mapped_column(String(96))
    price: Mapped[float] = mapped_column(Float)
    model_prob: Mapped[float] = mapped_column(Float)
    best_side: Mapped[str] = mapped_column(String(8))
    edge: Mapped[float] = mapped_column(Float)
    verdict: Mapped[str] = mapped_column(String(16))
    kelly_pct: Mapped[float] = mapped_column(Float)
    liquidity: Mapped[float] = mapped_column(Float)

    match: Mapped[MatchPredictionRow] = relationship(back_populates="quotes")


class Bet(Base):
    """Manual bet tracker (bk8's /bets equivalent)."""
    __tablename__ = "bets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    slug: Mapped[str] = mapped_column(String(128), index=True)
    market_type: Mapped[str] = mapped_column(String(32))
    line: Mapped[float | None] = mapped_column(Float, nullable=True)
    side: Mapped[str] = mapped_column(String(96))      # e.g. "Arsenal win YES", "Over 2.5 NO"
    price: Mapped[float] = mapped_column(Float)         # price paid per share
    stake: Mapped[float] = mapped_column(Float)         # USDC
    model_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(8), default="OPEN")  # OPEN, WON, LOST, PUSH, VOID
    pnl: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes: Mapped[str | None] = mapped_column(String(512), nullable=True)


class MatchResult(Base):
    """Final scores, for grading stored predictions and bets."""
    __tablename__ = "match_results"
    __table_args__ = (UniqueConstraint("slug"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128))
    league: Mapped[str] = mapped_column(String(16))
    kickoff: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    home_goals: Mapped[int] = mapped_column(Integer)
    away_goals: Mapped[int] = mapped_column(Integer)


def init_db(engine: Engine | None = None) -> Engine:
    engine = engine or make_engine()
    Base.metadata.create_all(engine)
    return engine


def session_factory(engine: Engine | None = None) -> sessionmaker:
    return sessionmaker(bind=engine or make_engine(), expire_on_commit=False)
