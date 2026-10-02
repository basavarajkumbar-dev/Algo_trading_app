# GAINZALGO PRO MAX

Modular, explainable, risk-controlled algorithmic intraday trading
platform for the Indian equity market (NIFTY 50 universe), with a
Zerodha broker adapter architecture.

**Status: Phase 9 — Professional Trading Dashboard complete. Mode: PAPER only.**

See:

- [`PROJECT_CONTRACT.md`](./PROJECT_CONTRACT.md) — rules, architecture
  principles, tech stack, and safety requirements.
- [`docs/development-roadmap.md`](./docs/development-roadmap.md) —
  phase-by-phase build plan and this phase's full Definition of Done.
- [`docs/api.md`](./docs/api.md) — full endpoint reference, including
  the analytics, system health and event endpoints added in this phase.

## What exists right now

- **Auth & settings** (Phase 2): JWT register/login/refresh/logout,
  per-user trading settings, all backed by real PostgreSQL models.
- **Market data engine** (Phase 3): a fully simulated NIFTY 50
  feed — 50 constituents plus a synthetic `NIFTY50` composite index —
  with realistic tick generation, configurable per-symbol regimes
  (trending/range/breakout/high-volatility), multi-timeframe candle
  aggregation (1m/3m/5m/15m/30m/60m), and a validation layer that
  rejects stale/duplicate/invalid/out-of-order ticks before they ever
  reach a candle. Runs entirely as a background task with **zero
  external credentials or network access required**.
- **Real-time layer**: `GET /api/v1/market/*` REST endpoints plus a
  `WS /api/v1/ws/market` WebSocket broadcasting live ticks and closed
  candles.
- **Market intelligence + regime engine** (Phase 4): VWAP, moving
  averages (20 EMA / 50 EMA / 200 MA), ATR/volatility, momentum, volume,
  market structure (swings, HH/HL/LH/LL, break of structure, structure
  shift), range/compression, trend quality and relative strength vs
  NIFTY 50. A Market Regime Detector classifies each symbol as
  `STRONG_BULLISH`, `STRONG_BEARISH`, `RANGE`, `BREAKOUT`,
  `HIGH_VOLATILITY` or `UNKNOWN`, with a confidence, the features used,
  and the reasons for and against. Everything is computed from closed
  5m candles and re-run every 5 seconds.
- **Frontend `/market` page — Market Intelligence**: index regime and
  why, breadth, factor scores, a live regime-change feed, a sortable
  regime table for all 51 symbols and a per-symbol engine breakdown.
  The dashboard home shows a market regime card.
- **Price action engine** (Phase 5): COG, NR, NRB, WRB, BT and TT
  detectors (each with detected, direction, confidence, strength,
  context and reasons), significant swings, a retracement engine with
  40/50/60/80/100% zones, support/resistance (previous day, intraday,
  swing, range/breakout, VWAP, EMA/MA and retracement levels) with a
  configurable Level Strength Score, and a configurable 60m context /
  15m setup / 5m entry multi-timeframe engine with an alignment score.
- **Frontend `/price-action` page**: candle chart with level lines,
  retracement levels and pattern markers; multi-timeframe panel; all six
  detectors with their reasons; retracement gauge; full levels table.

- **Adaptive strategy engine** (Phase 6): trend continuation,
  breakout momentum (consolidation or a configurable 5/15/30-minute
  opening range) and mean reversion (range only, never against a strong
  trend), behind a configurable regime router. Each returns eligible,
  direction, entry/stop/target candidates, confidence, reasons and
  features. **Frontend `/strategies` page** lists current candidates,
  the routing table and each strategy's checks.

- **Signal engine, scoring and NIFTY 50 scanner** (Phase 7):
  every eligible strategy candidate becomes a scored, explained
  `TradingSignal` (score 0-100 from the nine configurable factors,
  ELITE/STRONG/WATCH/IGNORE, READY at 85 or above), candidates are
  ranked, and signals are stored with an ACTIVE/EXPIRED lifecycle.
  **Frontend `/scanner`** (scanner table, top candidates, signal details)
  and **`/signals`** (stored signals with score and reason breakdowns).

- **Risk engine and paper trading** (Phase 8): a `RiskManager`
  sizes every trade (capital x risk % / stop distance, rounded to lots and
  capped) and rejects it, with every reason, on emergency stop, an
  unhealthy system, market closed, stale data, an active trade, a
  duplicate, the daily loss limit, max trades, consecutive losses, an
  invalid stop/target/quantity, insufficient capital or exposure. A
  `PaperBrokerAdapter` simulates MARKET / LIMIT / SL-M orders with
  configurable slippage, charges, execution delay and partial fills. The
  paper trading engine runs SIGNAL -> RISK -> ORDER -> FILL -> POSITION ->
  SL/TP -> EXIT -> TRADE -> P&L, **one trade at a time**, with manual exit,
  square-off and an emergency stop. **Frontend `/positions`** (Paper
  Trading: state machine, active trade, controls), **`/trades`** and
  **`/risk`**; Signals has a "Paper trade this signal" button.

- **Professional trading dashboard** (Phase 9, new): a command center
  (NIFTY, regime and confidence, algo status, trading mode, today's P&L,
  trade count, win/loss, system health), AI Market Brain, live scanner,
  signal panel with every scoring factor, active position with an
  SL ── ENTRY ── CURRENT ── TARGET bar, trade history, risk meters and
  system health — plus new **Analytics** (equity curve, drawdown, P&L,
  win rate, profit factor, strategy performance) and **System** (eight
  components, HEALTHY / WARNING / CRITICAL, event log) pages, a top bar
  with live NIFTY, market session, mode, algo, health and notifications.
  Built with TanStack Query, Zustand, Recharts and shadcn/ui, live over
  one WebSocket. Every panel reads the backend; nothing is invented.

**Paper only.** Orders go to a simulated broker; no broker (Zerodha or any
other) is connected, no real order can be placed and no real money is
involved. Automatic trading is **off** at every start. Fills, slippage and
charges are configurable assumptions, so paper results will differ from
live results, and nothing here claims a win rate or profitability.
Scores and ranks are not proof of profitability. Patterns are contextual features, not trade signals;
levels are reference zones, not guaranteed reversal points; and a
regime describes recent behaviour, not a forecast. Every price shown is explicitly labeled
simulated, never live — see `PROJECT_CONTRACT.md` section 9.

## Running with Docker (recommended)

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:3000 (redirects to `/login` until you sign in)
- Backend root: http://localhost:8000
- Backend health: http://localhost:8000/api/v1/system/health
- Market status: http://localhost:8000/api/v1/market/status
- Backend interactive API docs: http://localhost:8000/docs

Seed a demo account (see the backend steps below), then visit
http://localhost:3000/market (Market Intelligence) and
http://localhost:3000/price-action (Price Action) and
http://localhost:3000/strategies (Strategies),
http://localhost:3000/scanner (Scanner), http://localhost:3000/signals
(Signals), http://localhost:3000/positions (Paper Trading),
http://localhost:3000/trades, http://localhost:3000/analytics,
http://localhost:3000/risk and http://localhost:3000/system after logging in. Simulated warm-up history is
generated at startup, so regimes are available within seconds.

To stop:

```bash
docker compose down
```

## Running locally without Docker

**Backend**

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
export DATABASE_URL=postgresql+asyncpg://gainzalgo:gainzalgo@localhost:5432/gainzalgo
export REDIS_URL=redis://localhost:6379/0
alembic upgrade head
python -m app.seed   # optional: creates the demo user
uvicorn app.main:app --reload
```

The market data engine starts automatically with the API — no extra
step needed, and no `ZERODHA_*` variables are required.

**Frontend**

```bash
cd frontend
npm install
export NEXT_PUBLIC_API_BASE_URL=http://localhost:8000
npm run dev
```

## How the market data engine works (Phase 3 scope)

- `MockMarketDataProvider` is the only provider with a real
  implementation. `PaperMarketDataProvider` (what the future paper
  trading engine will depend on) currently delegates to it entirely.
  `BrokerMarketDataProvider` is an abstract interface only — no
  concrete, connectable broker implementation exists yet, by design.
- Ticks flow through `TickValidator` (rejecting stale, duplicate,
  invalid-price, missing-value, future-timestamp, and out-of-order
  data) before ever reaching `CandleBuilder`.
- Candles are only considered "closed" once a tick from the *next*
  period confirms it — the same way real exchange candles work.
- Everything (ticks, candles, connected clients) lives in memory for
  this phase; nothing is persisted to `market_ticks`/`candles` tables
  yet (that's Phase 4).
- The whole pipeline is a single background `asyncio.Task` started/
  stopped via `app.main`'s lifespan — check
  `GET /api/v1/market/status` for live tick accept/reject counts.

## How market intelligence works (Phase 4 scope)

- Engines in `backend/app/market_intelligence/` are pure functions over
  a list of candles. Each returns structured output plus plain-English
  `reasons`; with too little history they raise `InsufficientDataError`
  rather than guess, and the API lists the engine under `unavailable`.
- `backend/app/regime/` scores every regime from weighted, configurable
  conditions (`RegimeConfig`). The best score wins if it clears a
  minimum, otherwise the result is `UNKNOWN`; `HIGH_VOLATILITY` takes
  precedence when its own score is high enough. Confidence is lowered
  when the runner-up is close. `GET /api/v1/market/intelligence/config`
  shows every weight and threshold in use.
- `MarketIntelligenceService` only analyses **closed** candles, so
  readings never repaint mid-candle. Analysis runs in a worker thread
  (about 60 ms for all 51 symbols).
- Regime accuracy is checked against the simulator's own known
  behaviours in tests. That shows the detector recognises the patterns
  it is designed for; it says nothing about predicting real markets.

## How price action works (Phase 5 scope)

- `backend/app/price_action/` holds one module per concern:
  `candle_patterns.py` (runs every detector), `changing_guard.py`,
  `narrow_range.py`, `wide_range.py`, `tails.py`, `swing_structure.py`,
  `retracement.py`, `support_resistance.py`, `multi_timeframe.py`, plus
  `config.py` with every threshold and weight.
- Detectors use only candles up to the one they evaluate. Context
  (prior trend, nearby support/resistance, volume) adjusts confidence:
  for example a bottoming tail after a decline into support scores
  higher than one mid-range. Historical chart markers are evaluated
  without today's levels, so they never benefit from hindsight.
- "ATR" means ATR(14) at the latest candle everywhere, so distances and
  thresholds are comparable across engines.
- Day levels use IST calendar days. The simulated feed trades around the
  clock, so a simulated "day" is a full IST date, not the 09:15-15:30
  NSE session.
- Warm-up history for 5m/15m/30m/60m is aggregated from one simulated 5m
  path per symbol, so the timeframes describe the same prices (startup
  takes about 2 seconds).
- "Entry" is the brief's name for the 5m role: it reports whether the
  latest candle, momentum and volume agree with the higher-timeframe
  bias. It never produces an order, price, stop or size.

## How strategies work (Phase 6 scope)

- The router maps the regime to the strategies allowed to run (and
  their directions). Defaults: STRONG_BULLISH / STRONG_BEARISH -> trend
  continuation (with the trend only), BREAKOUT -> breakout momentum,
  RANGE -> mean reversion and breakout momentum, HIGH_VOLATILITY and
  UNKNOWN -> nothing. The router does not rank strategies.
- Each strategy has required checks (all must pass) and weighted checks
  (confidence). Ineligible results still return every check, the
  blockers and the furthest stage of the strategy's concept reached.
- Every threshold is a hypothesis in `app/strategies/config.py`, exposed
  at `GET /api/v1/strategies/config`, to be validated by backtesting.
- On the simulated feed most strategies are waiting most of the time,
  as they should be. Simulated ranges are narrow relative to ATR, so
  mean reversion rarely finds enough room to VWAP; its tests show it
  fires when the geometry works.

## How signals and the scanner work (Phase 7 scope)

- After each strategy refresh: eligible candidates -> scored signals ->
  ranking -> storage -> scanner rows, all on the same snapshot.
- Scoring weights are the brief's initial model (15/15/15/10/10/10/10/5/5)
  and live in configuration. **They add up to 95, not 100**; they are kept
  as given and every score is scaled to 100 over the components that
  apply to the strategy. They are not assumed to be optimal.
- A stored signal keeps the values it had when its setup first appeared;
  later scans only update when it was last seen, its latest score and
  rank. When the setup disappears it is marked EXPIRED.
- On the simulated feed, eligible candidates have mostly scored 50-75,
  so READY (85+) signals are rare. Two simulator traits pull scores down:
  simulated volume is random (the volume factor rarely confirms), and the
  simulated NIFTY 50 index is its own series rather than an aggregate of
  the constituents, so market alignment can disagree with a broadly
  rising set of stocks. Thresholds were not lowered to compensate.

## How paper trading works (Phase 8 scope)

- **Entry.** A stored ACTIVE signal (from the Signals page, the API, or
  automatic mode, which takes the best-ranked READY signal once per scan)
  becomes a trade intent. The RiskManager evaluates it against the live
  price; if approved, a paper MARKET order is placed for the sized
  quantity. Its client order id comes from the signal id, so the same
  signal can never be sent twice.
- **Position.** As the entry fills, an SL-M stop and a LIMIT target are
  placed for the filled quantity and kept in step with partial fills.
  Whichever fills, the other is reduced or cancelled. An unfilled entry
  is cancelled after `PAPER_ENTRY_TIMEOUT_SECONDS`.
- **Exit.** Stop loss, target, square-off at 15:15 IST, manual exit, or an
  emergency stop that explicitly asks to close. The trade records average
  entry and exit, gross P&L, charges, net P&L and the R multiple, and
  feeds today's risk counters.
- **Emergency stop** disables new trading, turns automatic mode off and
  cancels pending entry orders. It does **not** close an open position
  unless you ask (the dashboard asks separately) or set
  `PAPER_EMERGENCY_CLOSE_POSITIONS=true`; the position keeps its stop
  and target.
- **Market hours.** By default entries are allowed 09:15-15:00 IST,
  Monday to Friday; exchange holidays are not built in (set
  `TRADING_HOLIDAYS`). To exercise the simulated feed at other times set
  `TRADING_SESSION_MODE=SIMULATED_24H`.
- **Restarts.** Capital and today's counters are rebuilt from stored
  trades. A trade that was open when the process stopped cannot be
  resumed (paper orders live in memory) and is marked INTERRUPTED.
- READY (85+) signals are rare on the simulated feed (see above), so
  automatic mode may wait a long time for one. For a demonstration you can
  lower `SIGNAL_EXECUTABLE_THRESHOLD`; that changes which signals count as
  executable and says nothing about their quality.

## How the dashboard works (Phase 9 scope)

- **Data.** Every panel reads the backend through TanStack Query; there is
  no hardcoded or sample data in the frontend. The market data itself is
  the simulated feed (that is what demo mode is), labelled "Simulated".
  When the backend is unreachable panels say so instead of keeping
  invented values.
- **Realtime.** One WebSocket per tab (`SocketBridge`) streams prices,
  intelligence and regime changes into a Zustand store; paper trading
  events arriving on the same socket refresh the trading, analytics and
  system queries immediately. Polling remains as a fallback.
- **Today's P&L** is realized net P&L today plus the open trade's net P&L
  at the live price — the same figure the Active Position panel shows.
- **Health levels** are explained per component on the System page and
  in `docs/api.md`. HEALTHY means a component is running as designed; it
  says nothing about trading results.
- **End-to-end tests** expect the stack to be running: `make up` (or the
  backend plus `npm run build && npm start`), then `make test-e2e`. Set
  `E2E_BASE_URL` / `E2E_API_URL` for other hosts. Each run registers a
  throwaway account with a random password.

## Environment variables

See [`.env.example`](./.env.example) for the full list. New in Phases 4
and 5:

- `MARKET_DATA_WARMUP_CANDLES` — simulated closed candles per symbol and
  timeframe generated at startup (default `250`).
- `MARKET_DATA_REGIME_ROTATION_SECONDS` — how often each simulated
  symbol may switch behaviour (default `900`; `0` disables).
- `INTELLIGENCE_TIMEFRAME` — timeframe analysed (default `5m`).
- `INTELLIGENCE_REFRESH_SECONDS` — re-analysis interval (default `5.0`).
- `PRICE_ACTION_CONTEXT_TIMEFRAME` / `PRICE_ACTION_SETUP_TIMEFRAME` /
  `PRICE_ACTION_ENTRY_TIMEFRAME` — multi-timeframe roles (default
  `60m` / `15m` / `5m`; must be in descending order).
- `PRICE_ACTION_REFRESH_SECONDS` — price action re-analysis interval
  (default `15.0`).

- `SIGNAL_EXECUTABLE_THRESHOLD` (default `85`), `SIGNAL_STORE_MIN_SCORE`
  (default `70`), `SCANNER_TOP_N` (default `10`) and optional
  `SIGNAL_SCORE_WEIGHTS` (JSON; unlisted components keep their defaults).
- Migrations: run `alembic upgrade head` (Docker does this on start) to
  create the `signals`, `paper_orders` and `paper_trades` tables.
- Phase 8: `RISK_*` limits, `TRADING_SESSION_MODE`,
  `TRADING_LAST_ENTRY_TIME`, `TRADING_SQUARE_OFF_TIME`,
  `TRADING_HOLIDAYS`, and the paper broker assumptions `PAPER_SLIPPAGE_BPS`,
  `PAPER_EXECUTION_DELAY_SECONDS`, `PAPER_MAX_FILL_FRACTION`,
  `PAPER_ENTRY_TIMEOUT_SECONDS`, `PAPER_EMERGENCY_CLOSE_POSITIONS` and
  `PAPER_COSTS` — see `.env.example`.

`MARKET_DATA_TICK_INTERVAL_SECONDS` and everything from Phase 2
(`TRADING_MODE`, `JWT_SECRET`, etc.) still apply unchanged.

## Tests and checks

```bash
make test-backend    # pytest — 482 tests, all passing with a real Postgres;
                      # auth/settings tests skip cleanly (not fail) without one
make test-frontend   # tsc --noEmit + eslint + vitest (68 unit + integration tests)
make test-e2e        # Playwright against a running stack (5 end-to-end tests)
make lint             # ruff + black (backend), eslint (frontend)
```

Or directly:

```bash
cd backend && python -m pytest -q
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```

Test coverage added in Phase 9 (16 backend tests, 32 frontend tests,
5 end-to-end): performance analytics (summary figures, undefined profit
factor, equity and drawdown in closing order, streaks, IST days, groups),
component health classification, and the new endpoints with Postgres
(components, events, performance, trading events over the WebSocket).
Frontend integration tests render the dashboard, shell and pages with
real providers against API responses captured from the backend and a
mock WebSocket: every command-center panel, scanner selection, live
position P&L and the position bar, error states without invented data,
live NIFTY over the socket, query refresh on trading events, reconnect,
notifications, analytics (and its empty state), all eight system
components, the complete trade table and the risk meters. Playwright
logs in to the running stack, opens all twelve sections (failing on any
console error), checks the live WebSocket feed, the system page,
switching automatic trading on and off, and notifications.

Test coverage added in Phase 8 (122 backend tests, 5 frontend):
position sizing (formula, lots, ticks, every cap), every rejection
reason on its own and all failures reported together, day rollover and
session rules; the paper broker's order types, slippage, delay, partial
fills, modify / cancel, duplicates, validation, charges and accounting;
the state machine; and the engine end to end — long and short TP and SL,
gaps through the stop, a stop already crossed, one active trade,
duplicate signals at the risk manager and the broker, partial entry and
exit fills, entry expiry, the daily loss limit (block and shrink),
consecutive losses, max trades, manual exit, square-off, market closed,
stale data, non-PAPER mode, and emergency stop with and without closing.
With Postgres: storage, events, restart recovery, auto mode, and the
`/trading` API with auth and audit logging.

Test coverage added in Phase 7 (43 backend tests, 5 frontend): default
weights and validation, every category boundary, scoring arithmetic,
not-applicable handling, errors counted as zero, direction symmetry,
mean-reversion inversions, quantity candidates, strategy versioning,
every ranking tie-break, top-candidate filtering, scanner rows, the
signal lifecycle in Postgres (create, keep, expire, reappear, the
one-active-signal index), the scan service with and without a
database, and the scanner / signal API.

Test coverage added in Phase 6 (66 backend tests, 4 frontend): an
eligible baseline per strategy and side with exact candidates, every
required check failing on its own, stop/target geometry limits,
configurable thresholds and opening ranges, error isolation, every
default route and custom routing, the service, the API, and an import
guard that fails if strategy code references broker, order or
execution modules.

Test coverage added in Phase 5 (94 backend tests, 11 frontend): every
detector with positive, negative and context cases plus a no-look-ahead
check (31); zig-zag swings, every retracement zone boundary, day levels
across the IST midnight boundary, touch episodes, confluence, breakout
levels and every Level Strength Score component (40); multi-timeframe
bias, alignment, conflicts, every setup state, entry confirmation, the
service and API, including a check that no response carries order,
quantity, stop or target fields (23); and the frontend price action
helpers (vitest).

## Project structure

```
gainzalgo-pro-max/
  PROJECT_CONTRACT.md
  docs/development-roadmap.md
  docs/api.md
  docker-compose.yml
  Makefile
  .env.example
  backend/
    app/
      core/            config, logging, security, constants
      database/        base, session, models/ (9 models)
      market_data/     provider interface, mock/paper/broker providers,
                        validation, candle builder, NIFTY 50 universe,
                        MarketDataService (Phase 3)
      market_intelligence/  engines, analyzer, factor scores,
                        MarketIntelligenceService (Phase 4)
      regime/          Market Regime Detector + RegimeConfig (Phase 4)
      price_action/    pattern detectors, swings, retracement,
                        support/resistance, multi-timeframe,
                        PriceActionService (Phase 5)
      strategies/      BaseStrategy, trend continuation, breakout
                        momentum, mean reversion, StrategyRouter,
                        StrategyService (Phase 6)
      signals/         scoring, signal, ranking engines, storage,
                        SignalService (Phase 7)
      scanner/         Nifty50Scanner (Phase 7)
      risk/            RiskLimits, position sizing, RiskManager (Phase 8, new)
      execution/       BrokerAdapter, PaperBrokerAdapter, CostModel (Phase 8, new)
      trading/         state machine, PaperTradingEngine, storage,
                        TradingService (Phase 8)
      analytics/       performance analytics over paper trades (Phase 9, new)
      websocket/       ConnectionManager (Phase 3)
      schemas/         Pydantic request/response models
      services/        auth_service, settings_service, system_health
      api/v1/          auth, settings, system, market, intelligence,
                        price_action, strategies, signals (scanner),
                        trading, market_ws routes
      seed.py          demo user seed script
    alembic/           migrations
  frontend/
    app/
      login/           public login/register page
      (dashboard)/     protected route group (dashboard, settings, market)
    components/auth/   AuthProvider (session state/hydration)
    components/market/ regime badge/explainer/table, factor list,
                        breadth, symbol detail (Phase 4)
    components/price-action/ chart, pattern grid, retracement gauge,
                        levels table, multi-timeframe panel (Phase 5)
    components/strategies/ strategy result card (Phase 6)
    components/signals/ signal detail, score and reason breakdowns (Phase 7)
    components/trading/ lifecycle strip, risk checks, trade figures,
                        execute-signal button, trade table/detail (Phase 8)
    components/dashboard/ command center, market brain, live scanner,
                        signal panel, active position + bar, risk,
                        health (Phase 9, new)
    components/analytics/ Recharts equity, drawdown, P&L (Phase 9, new)
    components/realtime/ SocketBridge: the one /ws/market connection
    components/ui/      shadcn/ui components
    lib/stores/         Zustand market and UI stores (Phase 9, new)
    lib/queries.ts      TanStack Query hooks (Phase 9, new)
    test/               fixtures, render helpers, integration tests
    e2e/                Playwright end-to-end tests
    lib/               market-client, use-market-socket,
                        intelligence-format, price-action-format,
                        strategy-format, signal-format,
                        trading-client, trading-format
                        (+ vitest tests)
    middleware.ts       route-level auth redirect
  infrastructure/       Dockerfiles + backend entrypoint (runs migrations)
  scripts/               (reserved for future setup scripts)
```

## What's next

See `docs/development-roadmap.md`. Next: the remaining trading-domain
data models, the rest of the WebSocket event set and REST API, and the
backtesting engine, built one phase at a time. A real broker adapter comes
last and only behind the live-trading safety conditions in
`PROJECT_CONTRACT.md` section 9.
