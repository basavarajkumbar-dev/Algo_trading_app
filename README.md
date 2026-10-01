# GAINZALGO PRO MAX

Modular, explainable, risk-controlled algorithmic intraday trading
platform for the Indian equity market (NIFTY 50 universe), with a
Zerodha broker adapter architecture.

**Status: Phase 4 — Market Intelligence + Regime Engine complete.**

See:

- [`PROJECT_CONTRACT.md`](./PROJECT_CONTRACT.md) — rules, architecture
  principles, tech stack, and safety requirements.
- [`docs/development-roadmap.md`](./docs/development-roadmap.md) —
  phase-by-phase build plan and this phase's full Definition of Done.
- [`docs/api.md`](./docs/api.md) — full endpoint reference, including
  the market intelligence endpoints and WebSocket events added in this
  phase.

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
- **Market intelligence + regime engine** (Phase 4, new): VWAP, moving
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

**Still no strategies, signals, or orders of any kind exist.** The
engines only read and describe prices; nothing in this codebase can
place a trade. A regime describes recent behaviour and is not a
forecast: no regime guarantees future price movement. Every price shown is explicitly labeled
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
http://localhost:3000/market after logging in to see Market
Intelligence on the simulated feed. Simulated warm-up history is
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

## Environment variables

See [`.env.example`](./.env.example) for the full list. New in this
phase:

- `MARKET_DATA_WARMUP_CANDLES` — simulated closed candles per symbol and
  timeframe generated at startup (default `250`).
- `MARKET_DATA_REGIME_ROTATION_SECONDS` — how often each simulated
  symbol may switch behaviour (default `900`; `0` disables).
- `INTELLIGENCE_TIMEFRAME` — timeframe analysed (default `5m`).
- `INTELLIGENCE_REFRESH_SECONDS` — re-analysis interval (default `5.0`).

`MARKET_DATA_TICK_INTERVAL_SECONDS` and everything from Phase 2
(`TRADING_MODE`, `JWT_SECRET`, etc.) still apply unchanged.

## Tests and checks

```bash
make test-backend    # pytest — 141 tests, all passing with a real Postgres;
                      # auth/settings tests skip cleanly (not fail) without one
make test-frontend   # tsc --noEmit + eslint + vitest
make lint             # ruff + black (backend), eslint (frontend)
```

Or directly:

```bash
cd backend && python -m pytest -q
cd frontend && npm run typecheck && npm run lint && npm test && npm run build
```

Test coverage added this phase: every engine against synthetic candle
fixtures (39 tests, including two real bugs they caught), the regime
detector with one or more cases per regime plus `UNKNOWN` and
insufficient data (18), the intelligence service, REST endpoints and
WebSocket events (11 plus API/WS tests), and the frontend formatting
helpers (11 vitest tests).

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
                        MarketIntelligenceService (Phase 4, new)
      regime/          Market Regime Detector + RegimeConfig (Phase 4, new)
      websocket/       ConnectionManager (Phase 3)
      schemas/         Pydantic request/response models
      services/        auth_service, settings_service, system_health
      api/v1/          auth, settings, system, market, intelligence,
                        market_ws routes
      seed.py          demo user seed script
    alembic/           migrations
  frontend/
    app/
      login/           public login/register page
      (dashboard)/     protected route group (dashboard, settings, market)
    components/auth/   AuthProvider (session state/hydration)
    components/market/ regime badge/explainer/table, factor list,
                        breadth, symbol detail (Phase 4, new)
    lib/               market-client, use-market-socket,
                        intelligence-format (+ vitest tests)
    middleware.ts       route-level auth redirect
  infrastructure/       Dockerfiles + backend entrypoint (runs migrations)
  scripts/               (reserved for future setup scripts)
```

## What's next

See `docs/development-roadmap.md`. Still open from the intelligence
work: the multi-timeframe engine, retracement and support/resistance
engines, and price action detectors. After that come the remaining
trading-domain data models, strategies, signals, risk, and the paper
trading engine, built one phase at a time.
