"""Price upcoming EPL / La Liga Polymarket matches.

    python scripts/predict.py                   # next 7 days, both leagues
    python scripts/predict.py --leagues EPL --days 3 --min-edge 0.05
    python scripts/predict.py --json out.json
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402

from app.services.match_day import analyse_league  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=["EPL", "LALIGA"])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-edge", type=float, default=0.03)
    ap.add_argument("--json", type=Path)
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="WARNING")

    everything = []
    for league in args.leagues:
        for a in analyse_league(league, args.days):
            everything.append(a)
            p = a.probs
            print(f"\n{a.kickoff:%a %d %b %H:%M} UTC  {league}  {a.home} vs {a.away}")
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
                if m.edge.best_edge < args.min_edge:
                    continue
                side = m.label if m.edge.best_side == "YES" else f"NOT {m.label}"
                print(f"  {m.edge.verdict:<10} {side:<32} price {m.price:.3f}  "
                      f"model {m.model_prob:.3f}  edge {m.edge.best_edge:+.3f}  "
                      f"¼-Kelly {m.edge.suggested_bet_pct:.1f}%  liq ${m.liquidity:,.0f}")

    if args.json:
        args.json.write_text(json.dumps([asdict(a) for a in everything], default=str, indent=2))
        print(f"\nwrote {args.json}")


if __name__ == "__main__":
    main()
