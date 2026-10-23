# Foot8

**Lineup-adjusted football (soccer) prediction engine for Polymarket edge detection.**

The football version of the CourtEdge (bk8) NBA model. It uses the same pipeline: team ratings, then lineup adjustment, then the match model, then the edge against the Polymarket price. Each step has been rebuilt for football: expected goals instead of points per possession, a Poisson score matrix instead of a Normal margin, and three-way markets instead of two-way ones.

> ⚠️ The calibration constants come from public football-modelling literature for the top European leagues. None of them has been backtested on foot8's own data yet. Fit them on historical matches before you size real bets.

## Quick start

```bash
cd backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest -q
```

```python
from app.analytics import predict_match

p = predict_match(
    home_adj_xgf=2.1, home_adj_xga=0.9,   # home attack / defence, xG per 90
    away_adj_xgf=1.3, away_adj_xga=1.4,
    ah_line=-1.0, total_line=2.5,
)
# λ 2.29–0.70 · 1X2 72.4 / 18.6 / 9.0 · AH −1.0 fair 0.643 · O2.5 0.574 · BTTS 0.461 · 2-0
```

## NBA → football translation

| CourtEdge (NBA) | Foot8 (football) |
|---|---|
| ORtg / DRtg / NRtg (pts per 100 poss.) | xGF / xGA / xGD (per 90) |
| NRtg diff → margin → logistic win prob (σ 5.8) | attack × defence → λ_home, λ_away → **Dixon-Coles Poisson** score matrix |
| Home court +3.0 pts | Home advantage +0.30 goals of supremacy (or fitted from league home−away goals) |
| Moneyline (2-way) | **Home / Draw / Away** (3 separate Polymarket markets) + overround removal |
| Spread cover, Normal σ 11.5 | **Asian handicap** settled on the score matrix (half, whole and quarter lines; push refunds) |
| Total, pace-adjusted, σ 19 | Total goals from the same matrix (2.5, 2.25, 3.0 …) |
| — | BTTS, most likely correct score |
| Pace (geometric mean) | dropped: tempo is already in xG per 90 |
| OT adjustment, late-season bias | dropped: no overtime in league play, no established month effect |
| League scoring baseline (K = 20 games) | League goals/match + home advantage (K = 60 matches) |
| On/off ORtg/DRtg × MIN/48 | On/off xGF/xGA per 90 × MIN/90; **goalkeepers** use goals prevented per 90 |
| E_NET − team NRtg, dampening 0.6, ≥10 MPG | on-pitch xGD − team xGD, dampening 0.5, ≥25 min/app & ≥5 apps |
| OUT / DOUBTFUL / QUESTIONABLE | + **SUSPENDED** (red card / accumulation = 100%) |
| B2B / 3-in-4 / 4-in-6 | <72h rest / 3 in 7 days / 4 in 10 days |
| Road-trip length | European tie midweek before a league match |
| Travel miles (500 / 2000) | Travel km (500 / 1500) |
| Seed / clinch / play-in motivation | Title race, European places, relegation fight, dead rubber |
| Season blend K = 15 of 82 | K = 8 of 38; **promoted teams** get a promoted-side prior, not league average |
| Roster prior: 240 rotation minutes | Squad prior: 990 XI minutes (11 × 90) |
| Live: margin blend, σ × √t | Live: current score + Poisson(λ × time left), second-half weighting, **red cards** |
| Edge / EV / quarter-Kelly / verdicts | unchanged (12% / 6% / 3%) |

## Layout

```
backend/app/
├── analytics/
│   ├── prediction_model.py   # λ, score matrix, 1X2, AH, totals, BTTS, schedule modifiers
│   ├── lineup_adjustment.py  # OnOffSplitModel, outfield + goalkeeper impact
│   ├── player_impact.py      # injury/suspension list → PlayerAbsence
│   ├── schedule_engine.py    # fixture congestion, motivation from the table
│   ├── season_blend.py       # early-season regression, promoted-team prior
│   ├── squad_prior.py        # squad-based prior, turnover, stale-prior flag
│   ├── live_model.py         # in-play 1X2 / AH / totals
│   └── edge_calculator.py    # model vs Polymarket → edge, EV, Kelly, verdict
├── schemas/                  # Pydantic models
└── services/validation.py    # range + cross-source checks (xG 0.4–3.0, xGD ±2 …)
```

## Not ported yet

Only the formula layer has been ported. The bk8 data connectors (stats.nba.com, pbpstats, the NBA injury feed), the pipeline orchestrator, the FastAPI endpoints and the Next.js frontend are not here yet. They need football data sources to plug into, for example an xG provider (FBref/Opta, Understat), an injury and suspension feed, fixtures, and Polymarket soccer event matching.
