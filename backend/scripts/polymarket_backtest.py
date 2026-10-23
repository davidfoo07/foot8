"""Backtest the model against real historic Polymarket prices.

    python scripts/polymarket_backtest.py [--minutes-before 60]
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.polymarket_bt import build_rows, compare, simulate  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=["EPL", "LALIGA"])
    ap.add_argument("--minutes-before", type=int, default=60)
    args = ap.parse_args()

    rows = build_rows(args.leagues, date(2025, 8, 1), date.today(), args.minutes_before)
    print(compare(rows))
    for edge in (0.03, 0.05, 0.08):
        for blend in (1.0, 0.5):
            print(f"model blend={blend} edge>={edge}:", simulate(rows, edge, blend=blend))
        print(f"Pinnacle close (look-ahead) edge>={edge}:",
              simulate(rows, edge, use_pinnacle=True))
        print(f"Pinnacle early (no look-ahead) edge>={edge}:",
              simulate(rows, edge, use_pinnacle=True, pinnacle_source="early"))


if __name__ == "__main__":
    main()
