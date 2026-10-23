# Foot8

**Lineup-adjusted football (soccer) prediction engine for Polymarket edge detection.**

The football version of the CourtEdge (bk8) NBA model. It uses the same pipeline: team ratings, then lineup adjustment, then the match model, then the edge against the Polymarket price. Each step has been rebuilt for football: expected goals instead of points per possession, a Poisson score matrix instead of a Normal margin, and three-way markets instead of two-way ones.

> ⚠️ **Read the backtest section before betting.** Against real Polymarket prices, the model does not show a profitable edge. Every verdict is capped at LEAN (`MAX_VERDICT` in `app/services/match_day.py`).

## Quick start

```bash
cd backend
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
pytest -q

python scripts/predict.py                      # price the next 7 days of EPL + La Liga Polymarket matches
python scripts/predict.py --leagues EPL --days 2 --min-edge 0.05 --json picks.json
python scripts/backtest.py                     # walk-forward vs Pinnacle closing odds, 2018-19 → 2025-26
python scripts/polymarket_backtest.py          # vs real historic Polymarket prices (Aug 2025 →)
python scripts/tune.py                         # re-fit the rating constants
```

## Data sources

| Source | Used for | Notes |
|---|---|---|
| Understat `getLeagueData` | match xG, results, fixtures (EPL, La Liga, other big-5 leagues) | current season cached for 30 min, past seasons forever |
| football-data.co.uk | historic results + Pinnacle closing 1X2 / O/U 2.5 / AH odds | backtest benchmark |
| Polymarket Gamma + CLOB | live match markets (1X2, spreads, totals, BTTS), price history | the stake is placed against these prices |
| FPL `bootstrap-static` | EPL injuries/suspensions (chance of playing), player xG/xA, on-pitch xGC | EPL only |
| `backend/data/absences.json` | manual absences for any league (copy `absences.example.json`) | there is no free La Liga injury feed |

Responses are cached under `backend/data/cache/`, which is gitignored.

## Backtest results (as of 23 Oct 2026)

**Walk-forward vs Pinnacle's close.** EPL + La Liga, constants tuned on 2018-21 and tested on 2022-25 (3,040 matches). Lower log loss is better.

| | 1X2 log loss | O/U 2.5 log loss |
|---|---|---|
| foot8 model | 0.9758 | 0.6758 |
| Pinnacle close (de-vigged) | **0.9600** | **0.6666** |

The model is well calibrated: 2.76 goals per match projected vs 2.79 actual, and its home and draw rates are within 1.5 percentage points of reality. It is still clearly less sharp than Pinnacle. Blending it with the close does not improve on the close, and flat-stake betting at closing odds loses money in the test seasons.

**Against real Polymarket prices.** 915 settled EPL + La Liga matches, Aug 2025 to Oct 2026, priced 60 minutes before kick-off:

| | 1X2 log loss |
|---|---|
| foot8 model | 0.9994 |
| Polymarket, T−60 min | **0.9825** |
| Pinnacle close | 0.9847 |

An hour before kick-off, Polymarket is as sharp as Pinnacle's close. The average gap between the two is 1.0 percentage point. Buying wherever the model disagrees with the price loses money in every market at every edge threshold tested: about −5% to −28% ROI with 1¢ slippage. A day out (T−24 h), Polymarket is further from Pinnacle's close: a 1.55-point gap, and buying wherever it was cheaper than the close returned +17% to +25% over ~280 bets. That return relies on look-ahead, because the closing price isn't known a day early. Using Pinnacle's own earlier odds instead (no look-ahead) gave no reliable profit. Only ~400 matches have those odds; at T−1 h the strategy loses money, and at T−24 h there are too few bets to judge. The movement comes from news, such as team sheets and late injuries, that arrives after T−24 h. **Conclusion: on its own, the model is not a betting signal for these leagues.** Its realistic uses are as a sanity check and as a scenario tool (what a confirmed absence should do to the price), not as an automatic bet list.

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

## Layout additions

```
backend/app/connectors/   understat, football_data, polymarket, fpl, team_names, http (retry + cache)
backend/app/services/     ratings (as-of-date, no look-ahead), standings, match_day (live pipeline)
backend/app/backtest/     engine (vs Pinnacle), polymarket_bt (vs real Polymarket prices)
backend/scripts/          predict.py, backtest.py, polymarket_backtest.py, tune.py
```

## Not done yet

- **Champions League.** UCL matches pit leagues against each other, so team ratings need a league-strength adjustment. ClubElo, the usual source, is currently down, and many UCL clubs play outside Understat's leagues.
- **No live injury feed for La Liga.** Absences have to be entered by hand in `data/absences.json`.
- **Schedule context only sees league matches.** Cup and European fixtures are not loaded, so midweek congestion is undercounted.
- **No API or frontend.** The bk8 FastAPI endpoints and Next.js frontend are not ported; for now there is a CLI only.
