# Fantasy GM — Decision Platform for Competitive 12-Team Full-PPR Leagues

## 0. Honest scope note

The full spec this project is based on describes a multi-year, multi-team engineering
effort (live news ingestion + reliability scoring, a 12+ model ensemble, real-time
draft-room sync with opponent behavior modeling, full Monte Carlo season simulation,
SMS/push notification infra, etc.). This repository is a **real, working MVP** that
implements the same architecture and interfaces, but with simplified single-pass
versions of each engine so the whole thing actually runs end to end:

| Spec asked for | This MVP ships |
|---|---|
| 12+ model ensemble (GBM, Bayesian, time-series, comps...) | One transparent ensemble: recency-weighted rolling average + opportunity share + opponent adjustment + betting-market signal + Monte Carlo distribution. Swappable — see `app/projections.py`. |
| Live news feed with reliability scoring, SMS/push | A `news_events` table + ingestion interface (`app/news.py`) with a mock feed. Wire a real provider (see below) later; no code changes needed elsewhere. |
| Real player/stat data from nflverse/Sleeper/FantasyPros | Mock-data mode generates statistically plausible fake players/stats so you can run and test everything today. Real ingestion adapters are stubbed with the exact interface a real pull would fill. |
| Opponent draft-behavior learning across seasons | Basic tendency tracking (position run detection, ADP-deviation scoring) — no learned model yet. |
| 10,000+ sim Monte Carlo everywhere | Monte Carlo engine included (`app/simulation.py`), default 2,000 sims for MVP responsiveness, configurable up to whatever your hardware handles. |

Nothing here fabricates real player news or stats — mock mode is clearly labeled as
mock in every API response (`"data_source": "mock"`), matching the spec's rule against
inventing information.

## 1. Product summary

Fantasy GM is a decision-support tool for a single competitive fantasy manager. It
does not just rank players — for every decision point (draft pick, weekly lineup,
waiver claim, trade offer) it estimates the effect on **weekly win probability** and
**championship probability**, and shows the reasoning.

## 2. Tech stack (implemented)

- **Backend:** Python 3.11, FastAPI, SQLAlchemy, Pydantic v2
- **DB:** SQLite for the MVP (schema is Postgres-compatible; swap the connection
  string and it runs on Postgres unchanged — see `app/db.py`)
- **Simulation/ML:** NumPy, pandas (scikit-learn hook point left in `projections.py`
  for when you have enough real historical data to train a GBM)
- **Frontend:** Single-page vanilla JS + Tailwind (CDN) dashboard — deliberately
  framework-light so it's easy to port into Next.js later without fighting build
  config in this sandbox
- **Background jobs / live updates:** stubbed as a poll loop (`app/live_engine.py`);
  swap for Celery/Redis pub-sub in production, interface is unchanged

## 3. Architecture

```
┌─────────────────────┐        ┌──────────────────────────┐
│   Frontend (SPA)     │◀──────▶│   FastAPI app (app/main) │
└─────────────────────┘  REST  └──────────────────────────┘
                                        │
              ┌─────────────────────────┼──────────────────────────┐
              ▼                         ▼                          ▼
      app/projections.py        app/draft.py               app/lineup.py
      (ensemble + Monte Carlo)  (VORP/VONA, tiers, sims)    (optimizer, floor/ceiling)
              │                         │                          │
              ▼                         ▼                          ▼
      app/waiver.py              app/trade.py               app/simulation.py
      (FAAB, priority, ROS val)  (multi-team, win-prob Δ)   (Monte Carlo core)
              │                         │                          │
              └─────────────┬───────────┴──────────────┬───────────┘
                             ▼                          ▼
                       app/models.py (ORM)      app/mock_data.py
                             │                  (or real adapters)
                             ▼
                          SQLite / Postgres
```

Every recommendation endpoint returns a common envelope:

```json
{
  "recommendation": {...},
  "confidence": "Moderate",
  "model_version": "ensemble-v0.1-mvp",
  "data_source": "mock",
  "data_timestamp": "2026-07-23T00:00:00Z",
  "assumptions": ["..."],
  "missing_or_uncertain": ["..."]
}
```

so the "explainability / never fake precision" rule from the spec is enforced at the
API layer, not left to each endpoint to remember.

## 4. Data sources (real, for when you're ready to leave mock mode)

| Domain | Candidate provider | Notes |
|---|---|---|
| Player stats, snaps, routes, target share | `nflverse` / `nflfastR` (via `nfl_data_py`) | Free, open, well-maintained; 5-year history + current season |
| League/roster sync | Sleeper API (free, no auth) or Yahoo/ESPN (auth required) | Start with Sleeper — no key needed |
| Injuries / practice reports | Team-reported + aggregators like FantasyPros or RotoWire (licensing required) | Respect ToS; don't scrape |
| Betting market (totals, spreads) | The Odds API, or a licensed sportsbook-data provider | Paid tier for production volume |
| Weather | NOAA/NWS API (free) or OpenWeather | Stadium-level, dome detection needed |
| ADP / expert consensus | FantasyPros API (licensed) | Paid |

All are wired as **adapter interfaces** in `app/adapters/` (stub files) — implement
`fetch()` for a real one and flip `USE_MOCK_DATA=false` in `.env`.

## 5. Database schema (implemented in `app/models.py`)

Tables: `users, leagues, league_settings, teams, rosters, players, nfl_teams, games,
weekly_stats, advanced_metrics, injuries, practice_reports, depth_charts, news_events,
projections, projection_distributions, rankings, transactions, draft_picks,
waiver_claims, trades, trade_assets, lineups, matchup_simulations,
season_simulations, model_versions, data_sources, recommendation_logs,
notifications`.

All tables carry `created_at`, and rows produced by a model carry `model_version_id`
and `data_source_id` for full provenance/auditability, matching the spec's audit-log
requirement.

## 6. Prediction methodology (MVP)

For each player-week:

1. **Base rate**: recency-weighted average of trailing 6 games (weights `0.35, 0.25,
   0.17, 0.12, 0.07, 0.04` most-recent-first).
2. **Usage adjustment**: blend in target share / carry share / route participation
   trend (linear regression slope over trailing 4 weeks) to catch role changes
   before points catch up — this is the "opportunity" signal from expected-fantasy-points
   research.
3. **Opponent adjustment**: multiply by opponent's fantasy-points-allowed-to-position
   index (season-to-date, regressed toward league average by sample size).
4. **Market adjustment**: nudge team implied total (from spread/total) into
   the player's expected volume — QBs/pass-catchers scale with implied pass total,
   RBs scale with implied plays and game script (favorite → more rush volume).
5. **Distribution**: rather than one number, sample a Monte Carlo distribution
   (negative-binomial-like shape for volume × log-normal-like shape for efficiency)
   to get the 10th/25th/50th/75th/90th percentiles, floor, ceiling, and probability
   of clearing positional thresholds (e.g., "prob ≥ 15 PPR pts").
6. Correlated players (same-team QB/WR1, RB timeshares, DST vs. opposing offense)
   are sampled from the **same** simulation draw per game, not independently — see
   `app/simulation.py::simulate_game_correlated`.

This is a legitimate, defensible heuristic ensemble — it is **not** presented as a
trained ML model, because there's no real historical data loaded yet to train one
without leaking future information. `app/projections.py` has a clearly marked hook
(`train_gbm_from_history()`) for when you load real nflverse data — at that point
swap steps 1–4 for a LightGBM/XGBoost regressor with proper walk-forward validation
(see `app/backtest.py` stub for the harness).

## 7. Draft-optimization methodology (MVP)

- Value Over Replacement Player (VORP) computed against a replacement baseline
  (average of players ranked `#teams × starters_at_position + 1` through `+3`).
- Value Over Next Available (VONA) using a simple survival model: probability a
  player is gone by your next pick = `1 - (1 - p_opponent_takes_position)^picks_until_you`,
  calibrated from ADP standard deviation as a proxy for "how contested is this
  player."
- Tier breaks via k-means-style gap detection on projected points within position.
- Roster-construction fit: penalizes recommending a position you've already filled
  at replacement-level depth; rewards filling your only bench-depth hole.
- Monte Carlo draft-remainder simulation samples opponent picks (weighted toward
  ADP + positional need) to estimate "probability this player is available at your
  next pick."

Full opponent-tendency learning (from multi-season league history) is stubbed —
`app/draft.py::TendencyModel` — and activates automatically once `league_history`
rows exist.

## 8. Live-event decision workflow (MVP)

`app/live_engine.py` polls `news_events` (mock feed today, real feed later) and for
each new event:

1. Resolves affected player(s) + any "beneficiary" players (e.g., backup RB when
   starter is ruled out) via `depth_charts`.
2. Re-runs `projections.py` for all affected players only (not the whole slate —
   keeps it fast).
3. Re-runs `lineup.py` for any user roster containing an affected player.
4. Re-runs `waiver.py` priority for any newly-relevant free agent.
5. Writes a `notifications` row with urgency = `impact_on_projection × reliability_of_source`,
   so a "backup RB questionable" doesn't out-rank "your RB1 is out."
6. Reliability scoring: beat-reporter-tier sources score higher than
   aggregator-tier; **rumors are always labeled `unconfirmed` until a second
   independent source or team confirmation lands** — this logic lives in
   `app/news.py::score_reliability`.

## 9. MVP feature list (what's actually running in this build)

- Full DB schema + migrations (Alembic-ready)
- Mock data generator: 12 teams, ~450 rostered-relevant players, 5 mock seasons of
  weekly stats with realistic variance
- Player projections w/ percentile distributions + explainability text
- Draft assistant: live big board, VORP/VONA, tiers, "best available" by 5 lenses
  (best overall / safest / upside / need / value-vs-ADP), Monte Carlo
  availability-at-next-pick
- Lineup optimizer: optimal lineup, floor/ceiling/win-probability per configuration,
  FLEX handling, "why this lineup"
- Waiver assistant: ranked adds, suggested/aggressive/conservative FAAB, drop
  candidates compared against ROS value
- Trade analyzer: multi-team support, before/after lineups, win-probability delta,
  fairness verdict, counteroffer suggestion
- League intelligence: power rankings, luck-adjusted record, playoff odds via
  season simulation
- Dashboard UI wiring all of the above together
- Every response includes confidence, model version, data source/timestamp,
  assumptions, and stated uncertainty — no fake precision anywhere

## 10. Phased plan (per spec section 18)

- **Phase 1 (this doc):** product spec, architecture, schema, methodology ✅
- **Phase 2:** system design — done above; API contracts are the FastAPI
  OpenAPI schema, auto-generated at `/docs` when you run the server
- **Phase 3 (UX):** the shipped dashboard covers dashboard/draft/lineup/waiver/trade;
  league-intelligence and settings pages are minimal placeholders — extend
  `frontend/index.html`
- **Phase 4 (functional MVP):** shipped in this repo
- **Phase 5 (testing):** `backend/tests/` has unit tests for VORP math, projection
  percentile monotonicity, and lineup-optimizer FLEX logic; a real backtest needs
  real historical data, so `app/backtest.py` is a harness with mock inputs today
- **Phase 6 (deployment):** `Dockerfile` + `docker-compose.yml` + `.env.example`
  included

## 11. Running it

**One command (recommended):**

```bash
python3 run.py
```

Or double-click `run.command` (Mac) / `run.bat` (Windows). This handles everything —
creates a virtual environment, installs dependencies, seeds the mock database, starts
the API, and opens the dashboard in your browser — and is smart enough to skip steps
that are already done on subsequent runs. Press Ctrl+C in the terminal to stop it.

**Manual / step-by-step (if you want more control):**

```bash
cd backend
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python -m app.seed_mock_data     # populates SQLite with a full mock league
uvicorn app.main:app --reload    # http://localhost:8000/docs
```

Then open `frontend/index.html` in a browser (it points at `localhost:8000` by
default — see the `API_BASE` field at the top of the page).

To start fresh, delete `backend/fantasy_gm.db` and rerun.

## 12. Disclaimer (per spec §17)

This tool is for entertainment and decision-support purposes only. It never
guarantees a win. Every probability shown is a model estimate, not a fact.
Injury/news items are labeled confirmed vs. unconfirmed and should be verified
against a primary source before acting on them, especially close to lineup
deadlines. Betting-market data, where used, informs projections only and is not
gambling advice.
