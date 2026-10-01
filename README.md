# GAINZALGO PRO MAX

Modular, explainable, risk-controlled algorithmic intraday trading
platform for the Indian equity market (NIFTY 50 universe), with a
Zerodha broker adapter architecture.

**Status: Phase 3 — Market Data Engine complete.**

See:

- [`PROJECT_CONTRACT.md`](./PROJECT_CONTRACT.md) — rules, architecture
  principles, tech stack, and safety requirements.
- [`docs/development-roadmap.md`](./docs/development-roadmap.md) —
  phase-by-phase build plan and this phase's full Definition of Done.
- [`docs/api.md`](./docs/api.md) — full endpoint reference, including
  the market data and WebSocket surface added in this phase.

## What exists right now

- **Auth & settings** (Phase 2): JWT register/login/refresh/logout,
  per-user trading settings, all backed by real PostgreSQL models.
- **Market data engine** (Phase 3, new): a fully simulated NIFTY 50
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
- **Frontend `/market` page**: NIFTY 50 index, a live watchlist table,
  market/provider status, and WebSocket connection status with
  automatic reconnect.

**Still no strategies, signals, or orders of any kind exist.** The
market data engine only ever reads/derives prices — nothing in this
codebase can place a trade. Every price shown is explicitly labeled
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

Seed a demo account (see Phase 2 section below), then visit
http://localhost:3000/market after logging in to see the live
simulated feed.

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

## Environment variables

See [`.env.example`](./.env.example) for the full list. Notably new in
this phase:

- `MARKET_DATA_TICK_INTERVAL_SECONDS` — how often the mock provider
  generates a batch of ticks (default `1.0`).

Everything from Phase 2 (`TRADING_MODE`, `JWT_SECRET`, etc.) still
applies unchanged.

## Tests and checks

```bash
make test-backend    # pytest — 66 tests total, all passing with a real Postgres;
                      # auth/settings tests skip cleanly (not fail) without one
make test-frontend   # tsc --noEmit + eslint
make lint             # ruff + black (backend), eslint (frontend)
```

Or directly:

```bash
cd backend && python -m pytest -q
cd frontend && npm run typecheck && npm run lint && npm run build
```

Backend test coverage added this phase: tick validation (every
rejection reason independently tested), candle aggregation across all
six timeframes (including two real bugs the tests caught and fixed —
see `docs/development-roadmap.md`), the mock provider, the
`MarketDataService` pipeline, the WebSocket connection manager and live
endpoint, and the REST market endpoints.

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
                        MarketDataService (Phase 3, new)
      websocket/       ConnectionManager (Phase 3, new)
      schemas/         Pydantic request/response models
      services/        auth_service, settings_service, system_health
      api/v1/          auth, settings, system, market, market_ws routes
      seed.py          demo user seed script
    alembic/           migrations
  frontend/
    app/
      login/           public login/register page
      (dashboard)/     protected route group (dashboard, settings, market)
    components/auth/   AuthProvider (session state/hydration)
    lib/               market-client, use-market-socket (Phase 3, new)
    middleware.ts       route-level auth redirect
  infrastructure/       Dockerfiles + backend entrypoint (runs migrations)
  scripts/               (reserved for future setup scripts)
```

## What's next

See `docs/development-roadmap.md`: the remaining trading-domain data
models (Phase 4), market intelligence and regime detection (Phase 6),
strategies, signals, risk, and the paper trading engine — built one
phase at a time.
