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

## Phase 6 — Market Intelligence Core

**Scope**
- Market Intelligence engine (`MarketSnapshot`).
- Market Regime Detector (`STRONG_BULLISH`, `STRONG_BEARISH`, `RANGE`,
  `BREAKOUT`, `HIGH_VOLATILITY`, `UNKNOWN`) returning regime + confidence
  + reasons + features used.
- Multi-Timeframe Engine (60m context / 15m setup / 5m entry).
- Market Structure Engine (swings, HH/HL/LH/LL, break of structure).
- Moving Average Engine + Trend Quality Score.
- VWAP Engine.
- Retracement Engine.
- Support/Resistance Engine + Level Strength Score.

**Definition of Done**
- [ ] Each engine returns structured, explainable output as specified in
      the contract.
- [ ] Regime detector, structure engine, and retracement engine each have
      unit tests covering at least one example per category/zone.
- [ ] No fixed lookback or threshold is hardcoded without a configuration
      path.

---

## Phase 7 — Price Action, Volume, Momentum, Relative Strength

**Scope**
- Price action detectors: COG, NR/NRB, WRB, BT/TT — each returns
  detected/confidence/context/direction/strength/reasons. No detector
  creates a trade directly.
- Volume Engine + Relative Volume Score.
- Momentum Engine + Momentum Score.
- Relative Strength Engine (vs. NIFTY) + Relative Strength Score.

**Definition of Done**
- [ ] Each detector/engine unit tested against synthetic candle fixtures.
- [ ] Confirmed no detector output is wired directly to order creation
      (only into the signal/scoring layer, built in Phase 9).

---

## Phase 8 — Strategy Engine

**Scope**
- `BaseStrategy` abstract class (`analyze`, `generate_signal`,
  `validate`, `calculate_entry`, `calculate_stop_loss`,
  `calculate_target`, `explain`).
- `TrendContinuationStrategy`, `BreakoutMomentumStrategy`,
  `MeanReversionStrategy`.
- Strategy Router (regime → strategy selection, including
  defensive/no-trade for `HIGH_VOLATILITY`).

**Definition of Done**
- [ ] Each strategy implements the full base interface.
- [ ] Router selects the documented mapping under test for each regime.
- [ ] No strategy calls a broker or order manager directly.

---

## Phase 9 — Signal Engine, Scoring, Ranking, Scanner

**Scope**
- `TradingSignal` model + `SignalDirection` enum.
- Signal Confidence Engine with configurable weighted factors (market
  alignment, relative strength, trend quality, VWAP, structure,
  retracement, volume, momentum, price action) and configurable category
  thresholds (ELITE/STRONG/WATCH/IGNORE) and auto-trade threshold.
- Signal explainability payload ("Why Signal?").
- Candidate Ranking Engine across the NIFTY 50 universe.
- Stock Scanner pipeline (symbols → data → filter → analysis → strategy →
  signal → score → risk check → rank).

**Definition of Done**
- [ ] Weights and thresholds are read from config/DB, not hardcoded.
- [ ] Scanner produces a ranked `TradeCandidate[]` on mock data in tests.
- [ ] Explainability payload present on every generated signal.

---

## Phase 10 — Risk Engine

**Scope**
- `RiskManager`: position sizing from capital/risk%/stop distance, daily
  loss limit, max trades/day, max consecutive losses, max position size,
  max exposure, volatility adjustment.
- Approve/reject decisions with explicit reasons.
- Stop Loss Engine (structure/ATR/volatility/fixed) and Target Engine
  (RR/fixed/structure/trailing/momentum/time), strategy-recommended,
  risk-validated.

**Definition of Done**
- [ ] Position sizing formula matches the contract's documented formula
      under test.
- [ ] Each rejection reason (daily loss, max trades, consecutive losses,
      max position size, max exposure) has a dedicated test.
- [ ] Risk engine is independently callable/testable without the
      strategy or order layers.

---

## Phase 11 — Orders, Paper Trading, Positions, Exits

**Scope**
- Trade state machine (`CREATED` → ... → `CLOSED`/`CANCELLED`/`FAILED`).
- `OrderManager`: create/validate/submit/track/partial-fill/cancel/
  retry/reconcile; duplicate-order and race-condition prevention.
- Full Paper Trading Engine: virtual capital/positions/orders/P&L,
  configurable slippage/brokerage/charges/execution delay.
- `PositionManager` and Exit Management (`STOP_LOSS`, `TARGET`,
  `TRAILING_STOP`, `TIME_EXIT`, `MOMENTUM_EXIT`, `MANUAL_EXIT`,
  `EMERGENCY_EXIT`, `SYSTEM_FAILURE`, `MARKET_CLOSE`).
- One-active-trade-at-a-time enforcement via explicit state.

**Definition of Done**
- [ ] Full lifecycle test: signal → risk-approved → paper order → fill →
      position → SL or TP exit → trade history → P&L update, passes
      end-to-end.
- [ ] Duplicate order prevention test passes.
- [ ] One-active-trade rule test passes.
- [ ] Emergency stop test passes (blocks new trades, cancels safe
      pending orders, does not auto-close positions).

---

## Phase 12 — WebSocket Layer & REST API Completion

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

## Phase 13 — Frontend Foundation & Dashboard

**Scope**
- Next.js app: layout, left sidebar nav, top bar (market status, trading
  mode, algo status, system health, notifications, user).
- Dashboard command center: NIFTY market KPI, market regime card, algo
  status, today P&L, system health, AI Market Brain panel, live scanner
  table, active signal panel, active position panel, trade history
  preview.
- WebSocket client with reconnect logic and visible connection status.
- Demo/seed data wired so the dashboard is populated on first load.

**Definition of Done**
- [ ] Dashboard renders fully against the running backend in demo mode
      with zero external credentials.
- [ ] WebSocket reconnect behavior verified (simulated disconnect).
- [ ] No `any` types in new frontend code.

---

## Phase 14 — Remaining Frontend Pages

**Scope**
- `/market`, `/scanner`, `/signals`, `/positions`, `/trades`,
  `/analytics`, `/strategies`, `/risk`, `/settings`, `/system`.
- Signal details panel, active position panel with trade progress
  visualization, trade history detail view, analytics charts with time
  filters, risk dashboard, system health page, settings UI, emergency
  controls.

**Definition of Done**
- [ ] Every page listed loads without error against the backend.
- [ ] Emergency Stop control is reachable and calls the real endpoint.
- [ ] Settings changes are validated, logged, and audited end-to-end.

---

## Phase 15 — Backtesting Engine & Strategy Versioning

**Scope**
- Backtest engine (historical / out-of-sample / walk-forward) avoiding
  lookahead bias and data leakage, including brokerage/slippage/charges/
  sizing/SL/target/exit logic.
- Backtest results: total/winning/losing trades, win rate, profit
  factor, expectancy, net P&L, max drawdown, avg win/loss, RR,
  consecutive wins/losses, Sharpe where meaningful.
- Strategy versioning tied to parameter set, date range, and instrument
  universe.

**Definition of Done**
- [ ] A lookahead-bias regression test exists and passes.
- [ ] Backtest results match hand-computed expectations on a fixed
      synthetic dataset.
- [ ] Strategy version + backtest result linkage enforced in the schema.

---

## Phase 16 — Broker Adapter Architecture & Zerodha Structure

**Scope**
- `BrokerAdapter` abstract interface; `PaperBrokerAdapter`,
  `MockBrokerAdapter`, `ZerodhaBrokerAdapter` (structure only — real
  credentials optional, app runs fully without them).
- Zerodha auth via environment variables only; documented credential
  requirements; current official Zerodha developer docs consulted before
  implementing any live call.

**Definition of Done**
- [ ] App runs with `ZerodhaBrokerAdapter` unconfigured, falling back to
      Paper/Mock with a clear system-health message.
- [ ] No credential is hardcoded anywhere in the diff.

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
