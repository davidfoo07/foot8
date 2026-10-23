"""Price upcoming EPL / La Liga / Champions League Polymarket matches.

    python scripts/predict.py                   # next 7 days, EPL + La Liga + UCL
    python scripts/predict.py --leagues EPL --days 3 --min-edge 0.05
    python scripts/predict.py --json out.json
    python scripts/predict.py --save            # store in the database (backend/.env)
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402

from app.services.match_day import MatchAnalysis, analyse_league  # noqa: E402
from app.services.ucl import analyse_ucl  # noqa: E402


def _print_match(a: MatchAnalysis, min_edge: float) -> None:
    p = a.probs
    print(f"\n{a.kickoff:%a %d %b %H:%M} UTC  {a.league}  {a.home} vs {a.away}")
    print(f"  xG {a.lambda_home:.2f}-{a.lambda_away:.2f}  "
          f"1X2 {p['home_win']:.0%}/{p['draw']:.0%}/{p['away_win']:.0%}  "
          f"BTTS {p['btts']:.0%}  motivation {a.motivation[0]}/{a.motivation[1]}  "
          f"confidence {a.confidence}")
    if a.home_absences or a.away_absences:
        print(f"  out: {', '.join(a.home_absences) or '-'}  |  "
              f"{', '.join(a.away_absences) or '-'}")
    for w in a.warnings:
        print(f"  ! {w}")
    for m in a.markets:
        if m.edge.best_edge < min_edge:
            continue
        side = m.label if m.edge.best_side == "YES" else f"NOT {m.label}"
        print(f"  {m.edge.verdict:<10} {side:<32} price {m.price:.3f}  "
              f"model {m.model_prob:.3f}  edge {m.edge.best_edge:+.3f}  "
              f"¼-Kelly {m.edge.suggested_bet_pct:.1f}%  liq ${m.liquidity:,.0f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=["EPL", "LALIGA", "UCL"])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-edge", type=float, default=0.03)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--save", action="store_true", help="store the run in DATABASE_URL")
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    everything: list[MatchAnalysis] = []
    for league in args.leagues:
        if league == "UCL":
            for u in analyse_ucl(args.days):
                everything.append(u.match)
                _print_match(u.match, args.min_edge)
                if u.tie:
                    t = u.tie
                    leg1 = f"leg 1 {t.leg1_score[0]}-{t.leg1_score[1]}, " if t.leg1_score else ""
                    line = (f"  TIE {t.team_a} vs {t.team_b}: {leg1}"
                            f"{t.team_a} advances {t.advance_a:.0%} "
                            f"(ET {t.detail['win_in_extra_time']:.0%}, "
                            f"pens {t.detail['penalties']:.0%})")
                    if t.market_price_a is not None and t.edge is not None:
                        line += (f"  market {t.market_price_a:.3f}  {t.edge.verdict} "
                                 f"{t.edge.best_side} edge {t.edge.best_edge:+.3f}")
                    print(line)
            continue
        for a in analyse_league(league, args.days):
            everything.append(a)
            _print_match(a, args.min_edge)

    if args.save:
        from app.db import init_db, session_factory
        from app.services.store import save_run

        engine = init_db()
        with session_factory(engine)() as session:
            run = save_run(session, everything, args.leagues, args.days)
        print(f"\nsaved run {run.id} ({len(everything)} matches) to the database")

    if args.json:
        args.json.write_text(json.dumps([asdict(a) for a in everything], default=str, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
