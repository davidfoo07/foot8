"""Coordinate-descent tuning of rating/model constants on train seasons,
scored by 1X2 + O/U 2.5 log loss; then reports the held-out test seasons.

    python scripts/tune.py
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.backtest.engine import ModelConfig, betting_sim, run_backtest, summarise  # noqa: E402
from app.services.ratings import RatingConfig  # noqa: E402

LEAGUES = ["EPL", "LALIGA"]
TRAIN = [2018, 2019, 2020, 2021]
TEST = [2022, 2023, 2024, 2025]

GRID: dict[str, list] = {
    "k_blend": [4.0, 6.0, 8.0, 12.0, 16.0],
    "xg_weight": [0.5, 0.7, 0.85, 1.0],
    "prior_regression": [0.0, 0.1, 0.2, 0.35],
    "promoted_xgd": [-0.2, -0.35, -0.5, -0.65],
    "opponent_adjust": [False, True],
    "ha_scale": [0.8, 1.0, 1.2],
    "home_advantage": [None, 0.25, 0.30, 0.35],
    "rho": [0.0, -0.05, -0.10, -0.15],
}
RATING_KEYS = set(RatingConfig.__dataclass_fields__)


def score(rc: RatingConfig, mc: ModelConfig, seasons: list[int]) -> tuple[float, dict]:
    s = summarise(run_backtest(LEAGUES, seasons, rc, mc))
    return s["ll_model"] + s["ou_ll_model"], s


def main() -> None:
    rc, mc = RatingConfig(), ModelConfig()
    best, _ = score(rc, mc, TRAIN)
    print(f"start train score {best:.5f}")
    for _ in range(2):
        for key, values in GRID.items():
            for v in values:
                if key in RATING_KEYS:
                    cand_rc, cand_mc = replace(rc, **{key: v}), mc
                else:
                    cand_rc, cand_mc = rc, replace(mc, **{key: v})
                sc, _ = score(cand_rc, cand_mc, TRAIN)
                if sc < best - 1e-5:
                    best, rc, mc = sc, cand_rc, cand_mc
                    print(f"  {key}={v}: {best:.5f}")
    print("BEST", rc, mc)
    for label, seasons in (("TRAIN", TRAIN), ("TEST", TEST), ("2026 so far", [2026])):
        res = run_backtest(LEAGUES, seasons, rc, mc)
        print(label, summarise(res))
        for blend in (1.0, 0.5, 0.3):
            print(f"   blend {blend}:", betting_sim(res, 0.03, blend))
    default = run_backtest(LEAGUES, TEST, RatingConfig(), ModelConfig())
    print("TEST with untuned defaults", summarise(default))


if __name__ == "__main__":
    main()
