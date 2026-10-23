"""Run the walk-forward backtest.

    python scripts/backtest.py                 # default config, all seasons
    python scripts/backtest.py --seasons 2022 2023 2024 2025
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.engine import ModelConfig, betting_sim, run_backtest, summarise  # noqa: E402
from app.services.ratings import RatingConfig  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", nargs="+", default=["EPL", "LALIGA"])
    ap.add_argument("--seasons", nargs="+", type=int, default=list(range(2018, 2026)))
    ap.add_argument("--edge", type=float, default=0.03)
    args = ap.parse_args()

    res = run_backtest(args.leagues, args.seasons, RatingConfig(), ModelConfig())
    for k, v in summarise(res).items():
        print(f"{k:>18}: {v}")
    for blend in (1.0, 0.5, 0.3):
        print(f"bets (blend={blend}, edge>={args.edge}):", betting_sim(res, args.edge, blend))


if __name__ == "__main__":
    main()
