"""Champions League backtest + tuning of the cross-league model.

    python scripts/ucl_backtest.py            # tune on 2024-25, test on 2025-26 → today
    python scripts/ucl_backtest.py --no-tune  # default config only
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from loguru import logger  # noqa: E402

from app.analytics.global_ratings import GlobalConfig  # noqa: E402
from app.backtest.ucl_bt import attach_polymarket, simulate, summarise, walk_forward  # noqa: E402
from app.services.ucl import load_history  # noqa: E402

TRAIN = (date(2024, 9, 1), date(2025, 6, 30))
TEST = (date(2025, 7, 1), date.today())
GRID = {
    "half_life_days": [120.0, 240.0, 365.0],
    "team_ridge": [2.0, 4.0, 8.0],
    "euro_weight": [1.0, 1.5, 2.5],
    "league_ridge": [0.3, 1.0, 3.0],
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-tune", action="store_true")
    args = ap.parse_args()
    logger.remove()
    logger.add(sys.stderr, level="ERROR")

    history = load_history()
    cfg = GlobalConfig()
    if not args.no_tune:
        best = summarise(walk_forward(history, cfg, *TRAIN))["ll_model"]
        print(f"train start {best}")
        for key, values in GRID.items():
            for v in values:
                cand = replace(cfg, **{key: v})
                ll = summarise(walk_forward(history, cand, *TRAIN))["ll_model"]
                if ll < best - 1e-4:
                    best, cfg = ll, cand
                    print(f"  {key}={v}: {ll}")
        print("BEST", cfg)

    for label, (s, e) in (("TRAIN 2024-25", TRAIN), ("TEST 2025-26 →", TEST)):
        rows = walk_forward(history, cfg, s, e)
        if label.startswith("TEST"):
            attach_polymarket(rows, history)
        print(label, summarise(rows))
        if label.startswith("TEST"):
            for edge in (0.03, 0.05, 0.08):
                print(f"  bets edge>={edge}:", simulate(rows, edge))


if __name__ == "__main__":
    main()
