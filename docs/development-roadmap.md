# GAINZALGO PRO MAX — Development Roadmap

This roadmap is the authoritative, ordered execution plan referenced by
`PROJECT_CONTRACT.md` §10. Each phase lists **scope**, **out of scope**,
and a phase-specific **Definition of Done (DoD)**, in addition to the
general DoD template in the contract.

Work proceeds strictly in order. A phase is not started until the prior
phase's DoD is fully met and reported.

---

## Phase 0 — Governance & Contract ✅ (this phase)

**Scope**
- Inspect repository state.
- Create `PROJECT_CONTRACT.md`.
- Create this roadmap.
- No application code, no dependencies, no Docker.

**Out of scope**
- Any backend/frontend code.
- Any infrastructure files.

**Definition of Done**
- [x] Repository inspected; no pre-existing conflicting structure.
- [x] `PROJECT_CONTRACT.md` created covering all 11 required sections.
- [x] `docs/development-roadmap.md` created with all phases through
      hardening.
- [x] Git repository initialized.
- [x] No code, dependency, or Docker files introduced.

---

## Phase 1 — Repository & Tooling Skeleton

**Scope**
- `docker-compose.yml` defining `frontend`, `backend`, `postgres`,
  `redis` services (backend/frontend Dockerfiles may initially be minimal
  images that just prove the network/volumes work).
- `.env.example` with every variable named in the contract, placeholder
  values only.
- `Makefile` with targets for common tasks (`up`, `down`, `logs`,
  `migrate`, `seed`, `test-backend`, `test-frontend`, `lint`).
- `.gitignore` covering Python, Node, Docker, and editor artifacts.
- Minimal backend scaffold: a FastAPI app with a single `/health` route,
  its own `requirements.txt`/`pyproject.toml`, and Dockerfile.
- Minimal frontend scaffold: a Next.js + TypeScript + Tailwind app with a
  single placeholder page, its own `package.json`, and Dockerfile.
- README updated with local dev and Docker instructions for this minimal
  skeleton.

**Out of scope**
- Database models, Alembic.
- Authentication.
- Any trading domain logic.

**Definition of Done**
- [~] `docker compose up --build` starts all four services without error
      (see verification note below — not run directly in this sandbox).
- [x] Backend `/health` returns 200.
- [x] Frontend placeholder page renders.
- [x] `.env.example` covers every variable referenced anywhere in compose
      files or app config.
- [x] README documents both Docker and local (non-Docker) run paths.

> **Verification note:** the build/dev sandbox used to build this phase
> has no Docker daemon available, so `docker compose up --build` itself
> could not be executed here. Compose YAML validity, the build-context/
> Dockerfile path resolution rules, and every service's actual runtime
> behavior were instead verified by running the equivalent processes
> directly (`uvicorn`, `next build && next start`, a local Postgres/
> Redis) and confirming the health endpoint and dashboard behave
> correctly in both the "infra reachable" and "infra unreachable" cases.
> Running `docker compose up --build` on a machine with Docker installed
> is still the recommended way to confirm this phase end-to-end, and is
> expected to work based on that verification — but it has not been
> executed by Claude directly.

---

## Phase 2 — Database & Authentication ✅

> **Numbering note:** this phase, as directed and executed, combined
> what this roadmap originally scoped as separate "Phase 2 — Core
> Backend Foundation" and "Phase 3 — Authentication & Authorization"
> steps, plus the subset of "Phase 4" tables needed to support a full
> auth flow with safe per-user defaults. That combined scope is
> documented here as the authoritative record of what Phase 2 actually
> delivered. Phase 4 below has been narrowed accordingly to the
> remaining, trading-domain-specific tables it still owns.

**Scope**
- `backend/app/core/config.py` (typed settings via Pydantic Settings),
  `logging.py` (structured logging), `security.py` (password hashing +
  JWT access/refresh helpers), `constants.py` (role/broker/trading-mode
  enums).
- `backend/app/database/base.py`, `session.py` — async SQLAlchemy engine,
  session dependency, and shared UUID/timestamp mixins.
- Alembic initialized and wired to the app's own `Settings.database_url`
  (never a hardcoded connection string); `alembic upgrade head` /
  `downgrade base` both verified to round-trip cleanly, including
  cleanup of Postgres ENUM types that autogenerate does not drop by
  default.
- SQLAlchemy models + one migration for: `User`, `Session`,
  `TradingAccount`, `Instrument`, `StrategyConfig`, `RiskConfig`,
  `AuditLog`, `SystemEvent`, `TradingSettings` — every important entity
  has `id` (UUID), `created_at`, `updated_at`, and indexes on
  user/lookup columns per `PROJECT_CONTRACT.md` §4.
- Endpoints: `POST /auth/register`, `POST /auth/login`,
  `POST /auth/refresh` (with refresh-token rotation and replay
  prevention), `POST /auth/logout` (revocation), `GET /auth/me`.
- `GET /api/v1/settings/me` and `PATCH /api/v1/settings/me` — a
  settings foundation deliberately scoped to exclude `trading_mode` and
  `auto_trading_enabled` from the editable fields, since no trading
  engine exists yet to give changing them real meaning.
- Roles: `ADMIN`, `TRADER`, `VIEWER` with a `require_role()`
  dependency factory usable by future routes (not yet used by any route
  in this phase, since nothing is role-gated yet).
- Every new user gets a `TradingSettings` row defaulting to `PAPER` and
  a `RiskConfig` row with platform defaults, created in the same
  transaction as the user — no user can exist without an explicit safe
  trading-mode default.
- Seed script (`backend/app/seed.py`, `make seed`) creating a demo user,
  idempotent on repeat runs.
- Frontend: `AuthProvider` context (hydrates session from stored tokens
  on load, with access-token validation falling back to refresh),
  `/login` page (login + register), `middleware.ts` redirecting
  unauthenticated requests to `/login` and authenticated requests away
  from it, a protected `(dashboard)` route group, and a `/settings`
  foundation page wired to the real endpoints above.
- Backend container entrypoint now runs `alembic upgrade head`
  automatically before starting the API.

**Out of scope**
- Trading-domain tables not needed for auth (`market_ticks`, `candles`,
  `market_regimes`, `strategy_versions`, `signals`, `signal_scores`,
  `trade_candidates`, `orders`, `order_events`, `positions`,
  `position_events`, `trades`, `trade_executions`, `daily_statistics`,
  `strategy_statistics`, `broker_connections`, `paper_accounts`,
  `backtest_runs`, `backtest_results`) — deferred to Phase 4/11/15/16 as
  originally scoped.
- Role-gated routes (the dependency exists; nothing uses it yet).
- httpOnly-cookie-based token storage (documented simplification — see
  `frontend/lib/auth-storage.ts`).
- Any trading logic or live broker integration.

**Definition of Done**
- [x] Backend boots against real Postgres/Redis (verified directly with
      a local Postgres/Redis in this sandbox; see the Phase 1
      verification note above for why `docker compose up --build`
      itself could not be run here).
- [x] `alembic upgrade head` and `alembic downgrade base` both succeed
      and round-trip cleanly, verified twice in sequence.
- [x] All five auth endpoints implemented and visible in `/docs`
      (Swagger), verified live: register, login (including wrong-password
      rejection), `/me` (including missing/invalid token rejection),
      refresh (including rotation and replay-prevention), logout
      (including post-logout refresh rejection and idempotent re-logout).
- [x] Passwords never logged, returned, or echoed in any response —
      checked directly in a test.
- [x] Every new user has a `PAPER`-mode `TradingSettings` row — checked
      directly in a test (safety-critical invariant, not just implied by
      code).
- [x] 13 backend tests pass against a real database; auth/settings
      tests correctly skip (without failing) when no database is
      reachable, while Phase 1's health tests correctly continue to run
      and pass in that same no-database case.
- [x] `ruff` and `black` clean.
- [x] Frontend: login page, protected dashboard, and settings page all
      built, type-checked, linted, and built with `next build`
      successfully (3 routes + middleware).
- [x] Full frontend-shaped API sequence (login → me → settings →
      logout) verified end-to-end against the real running backend.
- [x] `.env.example` and `README.md` updated for this phase's new
      variables and commands.

---

## Phase 3 — *(folded into Phase 2 above)*

Originally scoped as a standalone Authentication & Authorization phase;
delivered together with Phase 2's database foundation instead, per the
explicit Phase 2 instruction to include both. No separate work remains
under this phase number.

---

## Phase 4 — Remaining Core Domain Data Models

**Scope**
- SQLAlchemy models + Alembic migrations for the trading-domain tables
  not already created in Phase 2: `market_ticks`, `candles`,
  `market_regimes`, `strategy_versions`, `signals`, `signal_scores`,
  `trade_candidates`, `orders`, `order_events`, `positions`,
  `position_events`, `trades`, `trade_executions`, `daily_statistics`,
  `strategy_statistics`, `broker_connections`, `paper_accounts`,
  `backtest_runs`, `backtest_results`.
- Every table: `id`, `created_at`, `updated_at` where applicable; indexes
  on `symbol`, `timestamp`, and the relevant foreign keys per contract.

**Definition of Done**
- [ ] All listed tables exist with migrations that apply cleanly on top
      of Phase 2's existing migration.
- [ ] Indexes present as specified.
- [ ] No table lacks `id`/timestamps where applicable.

---

## Phase 5 — Market Data Layer ✅ *(delivered as "Phase 3" per direct instruction)*

> **Numbering note:** this work was directed and executed as "Phase 3 —
> Market Data Engine," ahead of this roadmap's original sequence
> position (Phase 5), because it was the next priority requested. It
> fully satisfies this Phase 5 entry's scope, plus a WebSocket
> broadcast layer originally scoped for Phase 12. Phase 3's own slot
> above (folded into Phase 2) remains empty; Phase 4 (remaining domain
> models) has not been executed and still precedes this phase's
> database-persistence concerns — this phase deliberately keeps
> ticks/candles in memory only, never touching `market_ticks`/`candles`
> tables, to avoid depending on work not yet done.

**Scope**
- `MarketDataProvider` abstract interface (`connect`, `disconnect`,
  `subscribe`, `unsubscribe`, `get_ltp`, `get_historical_data`,
  `get_instruments`, `health_check`, plus `stream_ticks` for the
  WebSocket layer below).
- `MockMarketDataProvider` — fully working, zero credentials required,
  simulates the NIFTY 50 universe (50 constituents + a synthetic
  `NIFTY50` composite index) with configurable per-symbol regimes
  (`TRENDING_UP`, `TRENDING_DOWN`, `RANGE`, `BREAKOUT`,
  `HIGH_VOLATILITY`).
- `PaperMarketDataProvider` — the interface the paper trading engine
  (Phase 11) will depend on; currently delegates to
  `MockMarketDataProvider`, swappable later without changing callers.
- `BrokerMarketDataProvider` — abstract extension point for a real
  broker feed (e.g. Zerodha, Phase 16); no concrete/connectable
  implementation exists yet, by design.
- Candle Engine (`CandleBuilder`): builds 1m candles from ticks and
  aggregates into 3m/5m/15m/30m/60m, UTC internally, aligned to true
  clock boundaries. A candle is only confirmed closed once a tick from
  the *next* period arrives — matching real exchange semantics.
- Market data validation (`TickValidator`): rejects missing price,
  invalid price (zero/negative/NaN/Inf), missing symbol, stale ticks,
  duplicate ticks, future timestamps, and out-of-order ticks — each
  independently tested.
- `MarketDataService`: orchestrates provider → validate → candle-build
  → WebSocket-broadcast as a background task, started/stopped via
  `app.main`'s lifespan; tracks accepted/rejected tick counts by issue
  type for `/market/status`.
- WebSocket foundation (`ConnectionManager` + `/api/v1/ws/market`):
  broadcasts `market_tick` and `candle_closed` events; sends a
  `connected` ack plus a `market_snapshot` per watchlist symbol
  immediately on connect; reconnect-friendly by design (no server-side
  session state tied to a specific socket).
- REST: `GET /market/status`, `GET /market/instruments`,
  `GET /market/snapshot/{symbol}`, `GET /market/candles`.
- Frontend: `/market` page showing the NIFTY 50 index, a live watchlist
  table, market status, and WebSocket connection status (with
  reconnect), via a `useMarketSocket` hook.

**Out of scope (unchanged from original Phase 5 scope, still deferred)**
- Persisting ticks/candles to `market_ticks`/`candles` tables (Phase 4).
- Any real regime *detection* from candle data (Phase 6) — the
  `SimulatedRegime` enum here describes what the *simulator* is doing,
  not a detected market condition.
- Any strategy, signal, or order — this phase only ever reads/derives
  prices.

**Definition of Done**
- [x] App runs fully with zero external credentials using
      `MockMarketDataProvider` — verified live (server booted and
      streamed ticks with no `ZERODHA_*` variables set).
- [x] Candle engine produces correct OHLCV for all six timeframes under
      test, including two real bugs caught and fixed by the tests
      themselves: a `history_length` constructor argument that wasn't
      actually applied, and hour-boundary alignment math that assumed
      candles close immediately rather than only once the next period
      is confirmed.
- [x] Validation layer has a dedicated test for every listed failure
      mode (stale, duplicate, invalid price, missing symbol/price,
      future timestamp, out-of-order), plus per-symbol isolation and
      "rejected ticks don't corrupt state" tests.
- [x] WebSocket foundation tested at two layers: `ConnectionManager`
      unit tests (deterministic, fake sockets) and a real
      `/api/v1/ws/market` connection via `TestClient` (which correctly
      drives the app's lifespan, unlike the `httpx.AsyncClient` pattern
      used for auth tests) — confirmed to receive the connection ack
      and a real, live `market_tick` broadcast.
- [x] 53 new backend tests pass (66 total with Phases 1-2); market data
      tests correctly do not depend on a database at all, and continue
      to pass in the "no database reachable" scenario alongside the
      Phase 1/2 tests that specifically exercise that case.
- [x] `ruff` and `black` clean.
- [x] Frontend `/market` page built, type-checked, linted, and built
      with `next build` successfully (5 routes + middleware); verified
      live end-to-end (login → market status → instruments → WebSocket
      stream) via a Node script driving the exact calls the page makes.
- [x] No fake "live market data" claim anywhere — `/market/status`'s
      `notes` field and the Market page's subtitle both say simulated,
      not live.

---

## Phase 6 — Market Intelligence Core ✅ *(delivered as "Phase 4" and "Phase 5" per direct instruction)*

> **Numbering note:** this work was directed and executed as "Phase 4 —
> Market Intelligence + Regime Engine". It delivers most of this Phase 6
> entry plus the Volume, Momentum and Relative Strength engines from
> Phase 7 below. Roadmap Phase 4 (remaining domain tables) is still open.

**Delivered**
- `backend/app/market_intelligence/`: pure engines over closed candles,
  each returning structured output with human-readable `reasons`, and
  raising `InsufficientDataError` instead of guessing on short history:
  - VWAP (session-anchored; VWAP, slope, price relationship, distance,
    recent crossings).
  - Moving averages (configurable 20 EMA / 50 EMA / 200 MA; price
    relationship, slope, distance, separation and its trend, alignment).
  - Volatility (ATR, ATR vs its own baseline, level, expansion, wick
    share, reversals).
  - Momentum (volatility-normalised multi-lookback rate of change →
    0-100 Momentum Score, configurable lookbacks/weights).
  - Volume (relative volume, spike, volume trend, price confirmation).
  - Market structure (fractal swing highs/lows, HH/HL/LH/LL, Break of
    Structure, Structure Shift).
  - Range/compression and Trend Quality Score.
  - Relative strength vs NIFTY 50 (timestamp-aligned) and ranking.
- `backend/app/regime/`: Market Regime Detector. Weighted, configurable
  conditions per regime (`RegimeConfig`); returns `regime`, `confidence`,
  `features`, `reasons`, `counter_reasons`, per-regime `scores` and a
  `timestamp`, plus a fixed note that a regime is descriptive, not a
  forecast. `HIGH_VOLATILITY` overrides directional reads; anything
  below the minimum score is `UNKNOWN`.
- `MarketIntelligenceService`: re-analyses every watchlist symbol from
  closed 5m candles (no repainting) every few seconds, builds the
  `MarketSnapshot` (index regime, breadth, regime distribution, factor
  scores) and broadcasts `market_intelligence` / `market_regime` over
  `/ws/market`. REST: `/market/intelligence`, `/market/intelligence/config`,
  `/market/intelligence/{symbol}`, `/market/regime`.
- Mock provider recalibrated so each simulated regime produces
  recognisable behaviour, with warm-up history seeded at startup.
- Frontend: Market Intelligence page (index regime + explanation,
  breadth, factor scores, regime-change feed, sortable regime table,
  per-symbol engine breakdown with recent swings) and a regime card on
  the dashboard home.

**Completed in "Phase 5 — Price Action Intelligence Engine"**
- `backend/app/price_action/`:
  - `swing_structure.py`: significant swings via an ATR(14) zig-zag
    (alternating pivots, legs, the leg in progress), alongside the
    Phase 4 HH/HL/LH/LL structure.
  - `retracement.py`: the impulse is the largest leg of at least 2.5 ATR
    among the last four; returns retracement % now and at its deepest,
    zone (< 40 shallow, 40-50 healthy, 50-60 deep, 60-80 weakening,
    80-100 major weakness, >= 100 invalidation context), quality, the
    40/50/60/80/100% level prices, swing high/low, strength, confidence.
  - `support_resistance.py`: previous day high/low/close and intraday
    high/low (IST calendar days), significant swing highs/lows, range
    high/low (or breakout/breakdown level once broken), VWAP, EMA 20/50,
    MA 200 and retracement levels. Each level carries price, type, role,
    strength, touch_count, timeframe, recency and confluence; the 0-100
    Level Strength Score blends type, touches, confluence, recency and
    timeframe with configurable weights.
  - `multi_timeframe.py`: configurable context/setup/entry timeframes
    (default 60m/15m/5m) returning context_analysis, setup_analysis,
    entry_analysis, overall_bias, alignment_score, conflicts and reasons.
- Warm-up history for 5m/15m/30m/60m is aggregated from one simulated 5m
  path per symbol, so timeframes and previous-day levels are consistent.

**Definition of Done**
- [x] Each delivered engine returns structured, explainable output.
- [x] Regime detector and structure engine have unit tests covering
      every regime and every swing label/event (39 engine tests, 18
      detector tests, 11 service tests, plus API/WebSocket tests).
- [x] Retracement, swing, support/resistance and multi-timeframe engines
      unit tested (every zone boundary, day boundaries in IST, touch
      episodes, confluence, every strength component, every setup state).
- [x] Lookbacks and thresholds are function parameters or fields on
      `AnalysisConfig` / `RegimeConfig`; none are buried in logic.
- [x] Frontend formatting helpers unit-tested (vitest).

---

## Phase 7 — Price Action, Volume, Momentum, Relative Strength ✅ *(price action delivered as "Phase 5")*

> **Numbering note:** "Phase 5 — Price Action Intelligence Engine" was
> directed after "Phase 4". It completes this Phase 7 entry and the
> remainder of Phase 6 above. Roadmap Phase 4 (remaining domain tables)
> is still open.

**Scope**
- ~~Price action detectors: COG, NR/NRB, WRB, BT/TT.~~ Delivered:
  `candle_patterns.py` runs `changing_guard.py` (COG), `narrow_range.py`
  (NR, NRB), `wide_range.py` (WRB) and `tails.py` (BT, TT). Each returns
  detected, direction, confidence, strength, context (prior trend,
  location relative to support/resistance, relative volume, range vs
  average) and reasons, including why a pattern was *not* detected.
  Detection uses only candles up to the one evaluated; chart markers for
  past candles are evaluated without today's levels (no look-ahead).
- ~~Volume Engine + Relative Volume Score.~~ Delivered in Phase 6 work.
- ~~Momentum Engine + Momentum Score.~~ Delivered in Phase 6 work.
- ~~Relative Strength Engine (vs. NIFTY) + Relative Strength Score.~~
  Delivered in Phase 6 work.

**Definition of Done**
- [x] Each price action detector unit tested against synthetic candle
      fixtures: positive, negative (with reason) and context cases, plus
      a no-look-ahead test.
- [x] Confirmed no engine output is wired to order creation; there is
      no order code in the repository, and an API test asserts price
      action responses contain no order/quantity/stop/target fields.

---

## Phase 8 — Strategy Engine ✅ *(delivered as "Phase 6 — Adaptive Strategy Engine")*

> **Numbering note:** directed and executed as "Phase 6". It completes
> this entry; roadmap Phase 4 (remaining domain tables) is still open.

**Delivered** (`backend/app/strategies/`)
- `BaseStrategy` with `analyze`, `validate`, `calculate_entry`,
  `calculate_stop_loss`, `calculate_target` and `explain`, run in that
  order by `evaluate()`. Shared candidate rules: stop between 0.5 and
  3 ATR from entry (too tight is widened, too wide blocks), target at
  least the minimum reward:risk. A strategy that raises returns an
  ineligible result naming the error instead of breaking the others.
- `TrendContinuationStrategy`: trend -> impulse -> pullback (30-65%) ->
  support -> confirmation -> entry candidate, weighted by regime, higher
  timeframe, structure, VWAP, trend quality, volume, momentum, relative
  strength and price action.
- `BreakoutMomentumStrategy`: consolidation or a configurable opening
  range (5/15/30 min from 09:15 IST) -> compression (NR/NRB) -> volume ->
  breakout -> confirmation; rejects extended (chasing) entries.
- `MeanReversionStrategy`: range only; refuses strong trends (trend
  quality, directional efficiency or higher-timeframe bias); needs the
  range edge, VWAP deviation and a reversal pattern; targets VWAP.
- Each returns `eligible`, `direction`, `entry_candidate`,
  `stop_candidate`, `target_candidate`, `confidence`, `reasons`,
  `blockers`, every `check`, `features` and the concept `stage` reached.
- `StrategyRouter`: configurable regime -> strategies (and allowed
  directions) map plus a minimum regime confidence. Defaults: strong
  trends -> trend continuation (with the trend only), breakout ->
  breakout momentum, range -> mean reversion and breakout momentum,
  high volatility / unknown -> nothing. The order is evaluation order,
  not a ranking.
- `StrategyService` re-evaluates after each price action refresh, on
  the same candles; read-only `/api/v1/strategies` endpoints and a
  Strategies page.

**Deviations from the original scope**
- No `generate_signal()`: turning a candidate into a `TradingSignal`
  belongs to the Signal Engine (Phase 9), per the brief's flow
  STRATEGY -> SIGNAL -> RISK. Strategies stop at candidates.
- RANGE also routes to breakout momentum (the brief's example maps it
  to mean reversion only): a breakout starts from a consolidation, and
  the BREAKOUT regime is only detected once a large breakout candle has
  printed, usually too late for a non-extended entry.

**Definition of Done**
- [x] Each strategy implements the full base interface (66 tests: an
      eligible baseline with exact candidates per strategy and side,
      every required check failing on its own, geometry limits,
      configurability, error isolation).
- [x] Router selects the documented mapping under test for each regime,
      including direction restrictions, confidence floor, disabled
      strategies and custom routes.
- [x] No strategy calls a broker or order manager: a test parses every
      module in `app/strategies` and fails on any broker, order or
      execution import, and API tests assert no order fields.

---

## Phase 9 — Signal Engine, Scoring, Ranking, Scanner ✅ *(delivered as "Phase 7")*

> **Numbering note:** directed and executed as "Phase 7 — Signal Engine +
> Scoring + NIFTY 50 Scanner". It also adds the `signals` table, one of
> the domain tables roadmap Phase 4 lists; the rest of Phase 4 is open.

**Delivered**
- `app/signals/`: `SignalDirection` (BUY/SELL/NONE), `TradingSignal`
  (symbol, direction, strategy, strategy_version, entry, stop_loss,
  target, risk_reward, quantity_candidate, score, category, executable,
  confidence, market_regime, reasons, timestamp, status, full score
  breakdown and the strategy's checks).
- `ConfidenceScoringEngine`: the nine factors with the brief's initial
  weights in `ScoringConfig` (overridable via `SIGNAL_SCORE_WEIGHTS`).
  Components score 0-1 relative to the trade's direction and strategy;
  components that do not apply (retracement for breakout and mean
  reversion) are excluded and the rest scaled to 100; a component that
  fails to score counts as 0, never as missing. Categories ELITE >= 90,
  STRONG >= 80, WATCH >= 70, IGNORE below; executable threshold 85
  (`SIGNAL_EXECUTABLE_THRESHOLD`). "Why this signal" lists every
  component with a tick or cross and the final score.
- `SignalEngine` (candidates -> signals, quantity candidate from the
  platform's default capital, risk per trade and position cap) and
  `CandidateRankingEngine` (score, then risk:reward, relative strength,
  liquidity, trend quality, volume, strategy confidence).
- `app/scanner/Nifty50Scanner`: one row per constituent covering market
  context, regime, 60m/15m/5m, structure, VWAP, trend, retracement,
  support/resistance, price action, volume, momentum, relative strength
  and strategy, plus ranked and top candidates. The index is market
  context, not a row.
- `signals` table (Alembic `8c7183c8fd0d`): one row per setup episode,
  ACTIVE while scans keep finding it, then EXPIRED; values frozen at
  creation; a partial unique index allows one ACTIVE signal per
  (symbol, strategy, direction). Below `SIGNAL_STORE_MIN_SCORE` (70)
  candidates are shown but not stored.
- `SignalService` runs after every strategy refresh and keeps scanning
  if the database is down (signals are then not stored, reported in
  status). Endpoints: `GET /scanner`, `GET /signals`, `GET /signals/{id}`,
  `GET /signals/config`. Frontend: Scanner and Signals pages with
  signal details, score breakdown and reason breakdown.
- Strategy results now carry `strategy_version` = semver + hash of the
  strategy's config.

**Not in this phase**
- The risk check step of the brief's scanner pipeline (Phase 10): signals
  are not risk-validated and quantities are candidates.
- Database-stored scoring weights: weights come from configuration and
  environment; per-user / DB overrides come with the settings work.

**Definition of Done**
- [x] Weights and thresholds are read from configuration (environment
      overridable), not hardcoded in logic.
- [x] Scanner produces a ranked `TradeCandidate[]` on mock data in tests
      (unit tests with constructed contexts and an integration test on
      the full simulated pipeline).
- [x] Explainability payload present on every generated signal, stored
      with it and returned by the API.

---

## Phase 10 — Risk Engine ✅ *(delivered with Phase 11 as "Phase 8 — Risk Engine + Paper Trading")*

> **Numbering note:** directed and executed as "Phase 8 — Risk Engine +
> Paper Trading", which covers this phase and Phase 11 together.

**Delivered**
- `app/risk/`: `RiskLimits` (all configurable via `RISK_*`,
  `RISK_PER_TRADE`, `MAX_DAILY_LOSS`, `MAX_TRADES_PER_DAY`,
  `SIGNAL_EXECUTABLE_THRESHOLD` and `TRADING_*`), `calculate_position_size`
  (risk amount = capital x risk %, quantity = risk amount / |entry - stop|;
  tick rounding with the stop rounded away from entry, rounded down to
  lots, capped by max position value, available capital x margin
  multiplier and max order quantity; every binding cap named).
- `RiskManager.evaluate` runs every check and reports every failure:
  trading mode (PAPER only), emergency stop, system health, market hours
  (NSE 09:15-15:30 IST Mon-Fri, configured holidays, no entries after
  15:00), data freshness (price age and signal age / expiry), one active
  trade, duplicate order (signal already sent today, open order for the
  symbol), daily loss limit, max trades, consecutive losses, executable
  score, shorts allowed, stop validity, target validity, price moved away
  from the planned entry, quantity, sufficient capital (incl. estimated
  charges) and max exposure.
- A trade's quantity is reduced so its planned risk fits inside what is
  left of today's loss limit. Daily counters roll over on the IST date.
- `TRADING_SESSION_MODE=SIMULATED_24H` treats every moment as in-session
  so the simulated feed can be exercised outside exchange hours; the
  default is `EXCHANGE`.

**Not in this phase**
- Volatility-adjusted sizing and the separate Stop Loss / Target Engines
  (structure, ATR, trailing, momentum): stops and targets come from the
  strategies (Phase 6) and are validated here, not re-derived.
- Per-user risk configuration: the paper engine is one simulated account
  configured from the environment; the `risk_configs` table is not read yet.

**Definition of Done**
- [x] Position sizing formula matches the contract's documented formula
      under test.
- [x] Each rejection reason has a dedicated test (18 reasons).
- [x] Risk engine is independently callable/testable without the
      strategy or order layers (`test_risk_manager.py` uses plain intents).

---

## Phase 11 — Orders, Paper Trading, Positions, Exits ✅ *(delivered as part of "Phase 8")*

**Delivered**
- Trading state machine `IDLE / SCANNING -> SIGNAL_FOUND -> RISK_CHECK ->
  ORDER_PENDING -> POSITION_ACTIVE -> EXIT -> CLOSED` with an explicit
  transition table; only one trade can be between SIGNAL_FOUND and EXIT.
- `app/execution/`: `BrokerAdapter` (place_order, modify_order,
  cancel_order, get_order_status, get_positions, get_balance) and
  `PaperBrokerAdapter`: MARKET / LIMIT / SL_M orders, adverse slippage
  (bps), execution delay, partial fills (max fill fraction), lot-size
  and margin validation, duplicate `client_order_id` rejection, stop
  triggers already crossed rejected, position and balance accounting.
  `CostModel`: brokerage (capped per order across partial fills), STT,
  exchange, SEBI, stamp duty, GST, all configurable assumptions.
- `PaperTradingEngine`: SIGNAL -> RISK -> MARKET entry -> fills ->
  protective SL_M + LIMIT target sized to the filled quantity (resized on
  partial fills, one-cancels-other) -> exit -> trade with average entry /
  exit, gross P&L, charges, net P&L and R multiple -> daily risk counters.
  Exits: STOP_LOSS, TARGET, TIME_EXIT (square-off 15:15 IST), MANUAL,
  EMERGENCY (only when explicitly requested), entry expiry. An entry is
  cancelled if unfilled after `PAPER_ENTRY_TIMEOUT_SECONDS`.
- Emergency stop: disables new trading, turns automatic trading off,
  cancels pending entry orders; an open position keeps its stop and
  target and is closed only on an explicit request (or when
  `PAPER_EMERGENCY_CLOSE_POSITIONS=true`). Reset is explicit.
- `paper_orders` / `paper_trades` tables (Alembic `a71fadaf6cb3`), engine
  events as `system_events` (`PAPER_*`), operator actions in `audit_logs`.
  On restart, capital and today's counters are rebuilt from stored
  trades; a trade open at shutdown is marked INTERRUPTED.
- `TradingService` (prices every 0.5 s, health = feed + scanner + trade
  storage; automatic mode takes the best-ranked READY signal once per
  scan, OFF by default). Endpoints under `/trading` (status, risk,
  positions, orders, trades; execute signal, auto, exit, emergency stop
  and reset for TRADER/ADMIN). Frontend: Paper Trading, Trades and Risk
  pages, "Paper trade this signal" on Signals, live Algo badge.

**Not in this phase**
- Trailing stop and momentum exits; order retry / reconciliation against
  a real broker (there is none); resuming an open paper trade after a
  restart (marked INTERRUPTED instead).
- Any real broker connection: no Zerodha code path exists. LIVE and
  APPROVAL modes make every risk check fail.

**Definition of Done**
- [x] Full lifecycle test: signal -> risk-approved -> paper order -> fill
      -> position -> SL or TP exit -> trade history -> P&L update, passes
      end-to-end (engine, service with Postgres, and API).
- [x] Duplicate order prevention test passes (risk manager and broker).
- [x] One-active-trade rule test passes.
- [x] Emergency stop test passes (blocks new trades, cancels safe
      pending orders, does not auto-close positions unless asked).

---

## Phase 12 — WebSocket Layer & REST API Completion *(partly delivered in "Phase 9")*

> Delivered so far: `trading_event` messages on `/ws/market`, `GET
> /analytics/performance`, `GET /system/components`, `GET /system/events`.

**Scope**
- WebSocket event set exactly as specified in the contract (market_tick,
  signal_created, position_updated, pnl_updated, system_health, etc.),
  with reconnect-friendly design.
- Remaining REST endpoints under `/api/v1` (market, scanner, signals,
  positions, orders, trades, analytics, risk, strategies, system,
  backtests) beyond what Phases 3–11 already exposed.
- `docs/api.md` fully updated.

**Definition of Done**
- [ ] All endpoints listed in the contract exist and are documented in
      Swagger.
- [ ] A WebSocket client (test or script) receives at least one event
      end-to-end from a simulated trading loop tick.

---

## Phase 13 — Frontend Foundation & Dashboard ✅ *(delivered with Phase 14 as "Phase 9 — Professional Trading Dashboard")*

> **Numbering note:** directed and executed as "Phase 9 — Professional
> Trading Dashboard", which covers this phase and Phase 14 together, plus
> the read-only endpoints the dashboard needed (part of Phase 12).

**Delivered**
- Stack: Next.js 14, TypeScript, Tailwind, shadcn/ui components (table,
  tabs, tooltip, dropdown menu, progress, separator, skeleton; written in
  shadcn's source form, `components.json`), Recharts, TanStack Query
  (every dashboard read; keys in `lib/query-keys.ts`), Zustand (live
  market store; per-browser UI store) and one WebSocket connection
  (`SocketBridge`) for the whole dashboard.
- Sidebar: Dashboard, Market (Price Action under it), Scanner, Signals,
  Positions, Trades, Analytics, Strategies, Backtesting, Risk, System,
  Settings, with the current section highlighted.
- Top bar: NIFTY 50 (live price, change, regime), market session, trading
  mode, algo status, overall system health, WebSocket status,
  notifications (recent system events, unread count per browser).
- Command center: NIFTY, market regime, regime confidence, algo status,
  trading mode, today's P&L (realized net + open trade at the live
  price), trade count, win/loss, system health; AI Market Brain (regime,
  confidence, active / routed strategy, trend, momentum, VWAP, volume,
  volatility, structure, relative-strength leaders); live scanner (rank,
  stock, live price and change, signal, score, strategy, status); signal
  panel with every scoring factor ("Why?"); active position with live
  price, P&L, duration and the SL ── ENTRY ── CURRENT ── TARGET bar; trade
  history; risk meters; system health list.
- Realtime: prices, intelligence and regime changes from `/ws/market`;
  every `trading_event` invalidates the trading, analytics and system
  queries, so panels update without waiting for a poll. Reconnects after
  a drop and shows its state.
- No fake data: every panel reads the backend. Empty and error states say
  so instead of filling in values. The market data itself is the
  simulated feed, labelled "Simulated" everywhere.

**Definition of Done**
- [x] Dashboard renders fully against the running backend in demo mode
      with zero external credentials (Playwright e2e).
- [x] WebSocket reconnect behaviour verified (integration test drops the
      socket and checks the reconnect).
- [x] No `any` types in new frontend code.

---

## Phase 14 — Remaining Frontend Pages ✅ *(delivered as part of "Phase 9")*

**Delivered**
- `/analytics` (new): net P&L, win rate, profit factor, expectancy, max
  drawdown, average R, equity curve, drawdown chart, P&L per trade / per
  day, performance by strategy and by exit reason (`GET
  /analytics/performance`).
- `/system` (new): Market Data, Backend, Database, Redis, WebSocket,
  Strategy Engine, Risk Engine and Paper Broker as HEALTHY / WARNING /
  CRITICAL with reasons and metrics (`GET /system/components`), this
  browser's socket, and the event log (`GET /system/events`).
- `/backtesting`: stated that the engine was Phase 15 and showed no results (replaced in Phase 10 below).
- `/positions`, `/trades`, `/risk` moved to TanStack Query; Positions has
  the position bar and duration; Trades has the complete trade table
  with filters; Risk adds daily loss, risk used, trades used, exposure,
  consecutive losses and system status meters. Emergency stop remains on
  Positions and calls the real endpoint.

**Not in this phase**
- Analytics time-range filters (per trade / per day only).
- `/market`, `/scanner`, `/signals`, `/strategies`, `/price-action` keep
  their own polling (they work; moving them to TanStack Query is
  housekeeping).
- Settings audit trail beyond what Phase 2 built.

**Definition of Done**
- [x] Every page listed loads without error against the backend
      (Playwright visits all twelve sections and fails on any console
      or page error).
- [x] Emergency Stop control is reachable and calls the real endpoint.
- [ ] Settings changes are validated, logged, and audited end-to-end
      (unchanged from Phase 2).

---

## Phase 15 — Backtesting Engine & Strategy Versioning

> **Numbering note.** Delivered as "Phase 10 — Backtesting + Validation"
> per direct instruction, together with the Backtesting page that Phase
> 14 left as a placeholder. Details: `docs/backtesting.md`.

**Scope**
- Backtest engine (historical / out-of-sample / walk-forward) avoiding
  lookahead bias and data leakage, including brokerage/slippage/charges/
  sizing/SL/target/exit logic.
- Backtest results: total/winning/losing trades, win rate, profit
  factor, expectancy, net P&L, max drawdown, avg win/loss, RR,
  consecutive wins/losses, Sharpe where meaningful.
- Strategy versioning tied to parameter set, date range, and instrument
  universe.

**Delivered**
- Signal replay of the live analysis, strategies and scoring on closed
  bars (look-ahead guard, session-anchored higher timeframes, NIFTY
  context per decision, disk cache, process pool); execution replay
  through the real paper engine, risk manager, broker and cost model with
  next-open fills, a pessimistic intrabar path, gap fills and trailing
  stops (NONE / BREAKEVEN / ATR, also added to the paper engine).
- Historical, out-of-sample and walk-forward modes; selection by mean R
  minus one standard error with neighbour smoothing; robustness report
  and flags; optional no-edge control dataset.
- Synthetic (seeded, regime-based) and CSV data sources; per-day
  universe membership; data-quality reporting.
- Tables `strategy_versions`, `parameter_sets`, `backtest_runs`,
  `backtest_trades`, `backtest_equity_points`; `/backtests` API; background
  run queue with progress, cancel and delete; Backtesting page.
- R multiples (paper and backtest) are measured against the planned risk
  the position was sized for.

**Definition of Done**
- [x] A lookahead-bias regression test exists and passes (closed-bar
      guard plus a perturbation test).
- [x] Backtest results match hand-computed expectations on a fixed
      synthetic dataset (hand-built bars: fills, stops, gaps, costs,
      square-off, trailing).
- [x] Strategy version + backtest result linkage enforced in the schema
      (foreign keys from runs to versions and parameter sets).

---

## Phase 16 — Broker Adapter Architecture & Zerodha Structure

> **Numbering note.** Delivered as "Phase 11 — Zerodha Broker Adapter" per
> direct instruction. Details: `docs/broker.md`.

**Scope**
- `BrokerAdapter` abstract interface; `PaperBrokerAdapter`,
  `MockBrokerAdapter`, `ZerodhaBrokerAdapter` (structure only — real
  credentials optional, app runs fully without them).
- Zerodha auth via environment variables only; documented credential
  requirements; current official Zerodha developer docs consulted before
  implementing any live call.

**Delivered**
- Interface: connect, disconnect, health_check, get_profile, get_balance,
  get_positions, get_orders, get_order_status, place_order, modify_order,
  cancel_order, get_instruments — implemented by all three adapters.
- Zerodha (Kite Connect v3, verified October 2026 incl. the 1 April 2026
  static-IP, 10 orders/second and market-protection rules): login flow,
  token expiry, rate limiting, error mapping, status mapping, tags for
  idempotency; read-only unless armed by the execution gate.
- `OrderManager` + `ExecutionGate` (contract §9 conditions), duplicate
  protection, reconciliation, partial fills, failed-order, network-failure
  and disconnect handling; `BrokerService` supervision; `/broker` API;
  `live_broker` health component; Broker panel on the System page.

**Definition of Done**
- [x] App runs with `ZerodhaBrokerAdapter` unconfigured, falling back to
      Paper/Mock with a clear system-health message.
- [x] No credential is hardcoded anywhere in the diff (checked by a test).

**Not in this phase:** an arming action or any live-order endpoint,
routing the paper engine to Zerodha, Kite WebSocket order updates.

---

## Phase 17 — Hardening & Final Validation

**Scope**
- Full backend + frontend test suite pass.
- Security review against contract §8.
- Full documentation pass (`README.md`, `docs/architecture.md`,
  `docs/api.md`, `docs/trading-engine.md`).
- Final integration validation against the 20-point checklist in the
  original project brief (Docker up, migrations, backend/frontend start,
  dashboard loads, demo mode, mock data flow, WebSocket updates, scanner,
  signals, risk checks, paper trade execution, position updates, SL,
  target, trade history, analytics, emergency stop, tests pass, README
  accuracy).

**Definition of Done**
- [ ] All 20 final-validation checklist items pass.
- [ ] No known safety-rule violation (contract §9) remains open.

---

## Change Control

Any reordering, merging, or splitting of the phases above must be made by
editing this file in its own commit with a rationale in the commit
message — never implied silently by skipping ahead in code.
