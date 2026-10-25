# Foot8: Football Polymarket Analytics

## What this is
Foot8 is a football (soccer) prediction engine and dashboard. It prices **Polymarket** match markets for the **Premier League, La Liga and Champions League** and compares the model's probabilities with Polymarket's prices, showing the edge, EV, quarter-Kelly stake and a verdict.

It is the football port of **CourtEdge / bk8** (`../courtedge/bk8`, the NBA version). The pipeline is the same: ratings, then lineup adjustment, then the match model, then the edge against the market price. The maths is football-specific:

- **Ratings:** xG for and against per 90, blended with last season early on. Promoted sides get their own prior.
- **Match model:** a Dixon-Coles Poisson score matrix, not a Normal margin. 1X2, Asian handicap (half, whole and quarter lines), totals, BTTS and correct score all come from that one matrix.
- **Champions League:** a separate cross-league Poisson model (`analytics/global_ratings.py`), fitted on ESPN results from about 20 domestic leagues plus all UEFA matches. Two-legged ties and finals, including extra time and penalties, are priced in `analytics/knockout.py`.

The user is based in Singapore. Show times in **SGT** in the UI. "Football" always means soccer.

## Where it stands (as of 25 Oct 2026)
**Bottom line: the backtests find no profitable edge, and every verdict is deliberately capped at LEAN** (`MAX_VERDICT` in `app/services/match_day.py`). Keep that cap unless a new out-of-sample backtest shows a statistically meaningful edge.

| Test | Model | Benchmark |
|---|---|---|
| EPL + La Liga vs Pinnacle close, 2022-25 (3,040 matches) | 0.976 log loss | Pinnacle 0.960 |
| EPL + La Liga vs Polymarket T−60 min (915 matches) | 0.999 | Polymarket 0.983 |
| UCL vs Polymarket T−60 min (226 matches) | 0.889 | Polymarket 0.887 |

- **EPL / La Liga:** an hour before kick-off, Polymarket is as sharp as Pinnacle's close. Buying wherever the model disagrees loses 5–28% depending on the market.
- **UCL:** the model is close to the market. Edges of 5% or more returned +11% over 185 bets, but a bootstrap by match gives a 95% interval of about −21% to +49%. That is not significant.
- **Live History page:** over the first live week the market also beat the model on pick hit rate and log loss.

The full numbers are in `README.md` and `backend/data/backtest_summary.json`, which the dashboard's Backtest page reads.

## Layout
```
backend/
  app/analytics/   prediction_model (Poisson/DC, markets), lineup_adjustment, player_impact,
                   schedule_engine (congestion, motivation), season_blend, squad_prior,
                   live_model (in-play), edge_calculator, global_ratings (UCL), knockout (ties)
  app/connectors/  understat (xG), football_data (historic results + Pinnacle odds), polymarket
                   (Gamma events + CLOB price history), fpl (EPL injuries), espn (cross-league
                   results), team_names (cross-source name matching), http (retry + disk cache)
  app/services/    ratings (as-of-date, no look-ahead), standings, match_day (EPL/La Liga live
                   pipeline), ucl (UCL pipeline + To-Advance markets), history (grading),
                   store (DB writes), validation
  app/backtest/    engine (vs Pinnacle), polymarket_bt (vs real Polymarket), ucl_bt
  app/main.py      FastAPI app          app/db.py  SQLAlchemy models (Supabase Postgres)
  scripts/         predict, backtest, polymarket_backtest, tune, ucl_backtest, init_db
  data/            backtest_summary.json, absences.example.json; cache/ (gitignored)
frontend/          Next.js 16 + Tailwind v4: / (dashboard), /history, /backtest
```

## Commands
Run these from `backend/` using its own venv (`./venv/bin/python`, Python 3.12).
- **Tests:** `./venv/bin/python -m pytest -q` (105 tests, offline, no network)
- **Lint:** `./venv/bin/ruff check .`
- **API:** `./venv/bin/uvicorn app.main:app --reload --port 8100`
- **CLI picks:** `python scripts/predict.py [--leagues EPL LALIGA UCL] [--days 7] [--save]`
- **Backtests:** `scripts/backtest.py`, `scripts/polymarket_backtest.py`, `scripts/ucl_backtest.py --write`, `scripts/tune.py`
- **Database:** `python scripts/init_db.py` creates the tables.

Frontend (from `frontend/`): `npm run dev` serves on **port 3008**. `npx tsc --noEmit` and `npx eslint src` must stay clean.

**Ports:** the API runs on **8100** and the dashboard on **3008**, because bk8 uses 8000/3001 and Docker holds 3000. The preview launch configs (`foot8-backend`, `foot8-frontend`) live in `../courtedge/.claude/launch.json` and reach this repo through the symlink `courtedge/foot8 → ../foot8`.

## API
- `GET /api/v1/dashboard?days=7`: every league, plus the top model–market gaps.
- `GET /api/v1/matches/{EPL|LALIGA|UCL}?days=`
- `POST /api/v1/refresh`
- `GET /api/v1/history?days=7&force=`: graded past predictions.
- `GET /api/v1/backtest`
- `GET /api/v1/health`

Analyses run in a worker thread and are cached for 10 minutes. The first load after a start takes up to a minute.

## Database (Supabase Postgres)
- **Connection:** `DATABASE_URL` lives in `backend/.env`, which is **gitignored and holds the real password**. Never print it, commit it or paste it into chat; mask it if you ever need to show the URL.
- **Tables:** `prediction_runs` → `match_predictions` → `market_quotes`, plus `bets` (no UI yet) and `match_results`.
- **Auto-save:** the API stores a snapshot of upcoming predictions **at most hourly per league** (`SAVE_INTERVAL`). `/history` grades the latest snapshot taken before kick-off ("live"). Played matches with no snapshot are "backfill": rebuilt walk-forward with the Polymarket price 60 minutes before kick-off.

## Data sources and gotchas
- **Understat** (`getLeagueData/{league}/{season}`, needs the `X-Requested-With` header, gzip): the current season is cached for 30 minutes, past seasons forever.
- **Polymarket Gamma:** football series ids are EPL `10188`, La Liga `10193`, UCL `10204`. Each match is several events sharing a slug stem (`epl-ars-eve-2026-10-24`, `…-more-markets`, …).
  - Pagination stops around offset 2,000, so fetch in 30-day windows.
  - Early-2025 markets have no `sportsMarketType`, so the type is inferred from the question text.
  - Main moneyline events can stay open after the side markets close, so fetch both closed and open events for past dates.
- **Polymarket CLOB `prices-history`:** needs `startTs`/`endTs`; `interval=max` returns nothing for old markets.
- **ESPN scoreboard:** single dates only. Use the calendar field to find match days.
  - It **returns 403s on request bursts**. The connector throttles to 4 requests per second and backs off on 403/429.
  - The API reads history with `cache_only=True`, so a request never waits on ESPN.
  - Czech league coverage is missing.
- **ClubElo** (the usual cross-league source) was down, which is why the ESPN-based global model exists.
- **FPL:** EPL injuries only. There is no free La Liga or UCL injury feed; use `backend/data/absences.json` (template in `absences.example.json`).
- **Liquidity filter:** markets under $5,000 liquidity (`MIN_LIQUIDITY`) are hidden, because extreme lines show stale 0.12/0.88 quotes.
- **Name matching:** Understat names are canonical for EPL and La Liga (`connectors/team_names.py` aliases). UCL fixtures are matched to ESPN by date plus token-coverage similarity (`services/ucl.py`).

## Conventions
- **Code style:** Python with type hints, dataclasses or Pydantic, loguru for logging, and ruff with line length 100. Every connector needs a timeout, retries and the disk cache.
- **Tests:** offline tests with fixture payloads; never hit the network in tests.
- **No look-ahead:** backtests and backfills only use data from before kick-off (`build_ratings(as_of=…)`, `fit_global(as_of=…)`). Keep it that way.
- **Reporting results:** state them honestly, with sample sizes and intervals. Don't oversell edges.
- **Git:** commit or push only when the user asks. End commit messages with the Co-Authored-By line. Remote: `github.com/davidfoo07/foot8`, branch `main`.

## Known gaps and ideas
- Injury adjustment is EPL only; the UCL has no lineup adjustment.
- Schedule context only sees league matches, so cup congestion is undercounted.
- Stage-reach UCL markets ("reach the quarterfinal") are not priced; they need a tournament simulation.
- There is no bet-logging UI, although the `bets` table exists.
- Possible UI improvement: an explicit "model ahead / market ahead" badge on the History cards.
- Totals/BTTS bias: in high-scoring spells the model sits below the market on totals, partly by design (it regresses the league's scoring level).
