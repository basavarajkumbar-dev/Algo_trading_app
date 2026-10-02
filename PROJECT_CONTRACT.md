# GAINZALGO PRO MAX — Project Contract

Status: **Phase 0 — Foundation & Governance**
Last updated: 2026-09-16

This document is the binding reference for how GAINZALGO PRO MAX is designed,
built, and shipped. Every later phase must comply with this contract. If a
future decision conflicts with this document, this document wins unless it is
explicitly amended (see §6 Git Workflow).

---

## 1. Project Objective

GAINZALGO PRO MAX is a modular, explainable, risk-controlled algorithmic
intraday trading platform, initially scoped to the Indian equity market
(NIFTY 50 universe) with a Zerodha broker adapter.

The platform must support three trading modes:

| Mode | Description |
|---|---|
| `PAPER` | Fully simulated capital, orders, and fills. **Default mode.** |
| `APPROVAL` | System proposes trades; a human approves or rejects before execution. |
| `LIVE` | Real orders sent to a connected broker. Requires explicit multi-factor enablement (see §9). |

**The system must always start in `PAPER` mode.** No code path may cause an
automatic or silent transition from `PAPER`/`APPROVAL` to `LIVE`.

### What this project is
- A modular, testable, explainable signal-generation and risk-managed
  execution framework.
- A platform where every strategy is treated as a **testable hypothesis**,
  validated through backtesting and out-of-sample evaluation before it is
  trusted with capital (paper or live).
- A system whose default and safest configuration must be fully runnable
  with **zero external credentials**, using mock/simulated market data.

### What this project explicitly is NOT
- Not a "guaranteed profit" or "guaranteed win rate" system.
- Not a black box — every signal must produce a human-readable explanation.
- Not a collection of microservices from day one (see §2).
- Not a system that treats a single candlestick pattern, indicator, or
  fixed lookback as ground truth. All such signals are *factors*, not
  standalone triggers.

---

## 2. Architecture Principles

1. **Modular monolith first.** The backend is one deployable FastAPI
   application internally organized into clearly bounded modules
   (market_data, regime, strategies, signals, risk, execution, etc.).
   Microservice extraction is a possible *future* step once module
   boundaries have proven stable — it is explicitly out of scope for the
   phases defined in this contract.
2. **Layered flow, one direction.** API routes are thin. Business logic
   lives in services. Data access lives in repositories.
   ```
   API route → Service → Domain logic → Repository → Database
   ```
   Routes must not contain business logic. Domain logic must not import
   FastAPI or HTTP concerns.
3. **Strategy engine never talks to a broker.** The only path to broker
   execution is:
   ```
   Strategy → Signal → Risk Engine → Order Manager → Broker Interface → Adapter
   ```
4. **Everything broker/market-data related is behind an interface.**
   `MarketDataProvider` and `BrokerAdapter` are abstract. Mock and Paper
   implementations must be complete and are the default. A missing or
   invalid broker credential must never crash the app — it must fall back
   to Mock/Paper and surface a clear system-health warning.
5. **Fail closed, not open.** Any critical subsystem failure (stale data,
   DB down, Redis down, broker disconnected, risk engine unavailable)
   blocks new order creation. Existing positions are handled per the
   configured fail-safe policy — never silently ignored.
6. **One active trade at a time by default.** This is a configurable
   limit, but its default value is 1, enforced by an explicit state
   machine, not by convention.
7. **Configuration over hardcoding.** Risk parameters, strategy weights,
   thresholds, and trading hours live in the database/settings layer and
   environment variables — never as hardcoded magic numbers in domain
   logic.
8. **Explainability is a first-class output.** Every `TradingSignal` and
   every regime/structure/strategy decision must carry a `reasons: str[]`
   (or equivalent) field usable directly by the frontend "Why Selected?"
   panel.

---

## 3. Technology Stack

| Layer | Technology |
|---|---|
| Frontend | Next.js (App Router) + TypeScript (strict) + Tailwind CSS + shadcn/ui |
| Frontend data | TanStack Query (server state) + Zustand (client/UI/websocket state) |
| Frontend charts | Recharts |
| Backend | Python + FastAPI + Pydantic v2 |
| ORM / migrations | SQLAlchemy 2.x (async) + Alembic |
| Database | PostgreSQL |
| Cache / real-time state | Redis |
| Background tasks | ARQ (Redis-backed async task queue) — chosen over Celery for a
  lighter dependency footprint that fits a single-broker, single-region
  deployment; chosen over raw FastAPI background tasks because trading
  loops need persistence, retries, and visibility outside the request
  cycle. This decision is revisited if/when true multi-worker scaling is
  needed. |
| Realtime transport | Native FastAPI/Starlette WebSockets |
| Containerization | Docker + Docker Compose |
| Backend lint/format/type-check | ruff, black, mypy (best-effort on external stubs) |
| Frontend lint/format | eslint, prettier, TypeScript strict mode |
| Backend tests | pytest + pytest-asyncio |
| Frontend tests | Vitest + React Testing Library (component/unit), Playwright reserved for later E2E phases |

---

## 4. Repository Structure

Top-level layout (created/expanded incrementally, phase by phase):

```
gainzalgo-pro-max/
  README.md
  PROJECT_CONTRACT.md
  docker-compose.yml
  Makefile
  .env.example
  .gitignore
  docs/
    development-roadmap.md
    architecture.md            (added when Phase 2+ introduces the backend skeleton)
    api.md                     (added when the API surface exists)
    trading-engine.md          (added when the trading engine exists)
  frontend/
    (Next.js app — scaffolded in its dedicated phase)
  backend/
    app/
      main.py
      core/            config, security, logging, constants
      api/v1/
      database/        base, session, models/
      schemas/
      services/
      repositories/
      market_data/
      market_intelligence/
      regime/
      multi_timeframe/
      price_action/
      strategies/
      signals/
      risk/
      execution/
      paper_trading/
      brokers/
      positions/
      orders/
      analytics/
      backtesting/
      websocket/
      notifications/
      workers/
      utils/
      tests/
  infrastructure/
    (Dockerfiles, deployment/config assets)
  scripts/
    (setup, seed, dev-utility scripts)
```

No phase may introduce a top-level directory not listed here without
updating this contract first.

---

## 5. Coding Standards

**Python (backend)**
- Format with `black`, lint with `ruff`, type-check with `mypy` on a
  best-effort basis (external/broker SDK stubs may be incomplete — that is
  acceptable and should be isolated at the adapter boundary).
- All public functions and Pydantic models are typed. No bare `Any` in
  domain logic.
- No file should mix unrelated responsibilities. If a module file exceeds
  roughly 300–400 lines, it should be split.
- Business logic never lives inside an API route handler.
- Never hardcode secrets, API keys, or credentials. Always read from
  environment/config.

**TypeScript (frontend)**
- Strict mode on. `any` is disallowed except at explicit, commented
  third-party boundary points.
- Components are function components with typed props (`interface Props`).
- Shared types live in a dedicated `types/` directory and mirror backend
  Pydantic schemas where they cross the API boundary.
- No business/trading logic duplicated in the frontend — the frontend
  displays and requests; the backend decides.

**General**
- No single massive file — neither a giant Python module nor a giant React
  component. Favor composition.
- Every module that computes a trading-relevant decision (regime, signal,
  score, risk check) returns structured, explainable output — not just a
  boolean or a raw number.

---

## 6. Git Workflow

- `main` is always in a runnable state for whatever phase has been
  completed. Do not commit code that fails to build/import.
- Work proceeds **phase by phase**, matching `docs/development-roadmap.md`.
  A phase is not started until the previous phase's Definition of Done is
  met.
- Commit granularity: one logical unit of work per commit (e.g. "Phase 1:
  Docker + Postgres + Redis skeleton"), with a message referencing the
  phase.
- Amendments to this contract or the roadmap are made via an explicit
  commit that modifies `PROJECT_CONTRACT.md` / `docs/development-roadmap.md`
  directly — never silently contradicted by code.
- No direct commits that introduce live-trading defaults, hardcoded
  secrets, or a skipped safety check are permitted, regardless of phase
  pressure.

---

## 7. Testing Requirements

- Every phase that introduces trading-relevant logic (regime detection,
  scoring, risk, order/position lifecycle) must ship with tests in the
  same phase — tests are not deferred to a later "testing phase" for core
  safety logic.
- Minimum required test coverage areas before any paper-trading execution
  path is considered done:
  - Risk limit rejection (daily loss, max trades, consecutive losses,
    position size)
  - Duplicate order prevention
  - One-active-trade-at-a-time enforcement
  - Stop loss and target trigger logic
  - Full paper trade lifecycle (signal → order → position → exit → P&L)
  - Emergency stop behavior
  - System-unhealthy blocks new trades
- Backend: `pytest`, async-aware, run via `make test-backend` (introduced
  when the backend skeleton exists).
- Frontend: component and state-management tests via Vitest, run via
  `make test-frontend` (introduced when the frontend skeleton exists).

---

## 8. Security Requirements

- Passwords hashed with a modern, salted algorithm (e.g. bcrypt/argon2
  via `passlib`), never stored or logged in plaintext.
- Authentication via JWT access tokens with refresh-token support;
  protected routes verified via dependency injection.
- Role-based access control: `ADMIN`, `TRADER`, `VIEWER` with distinct
  permissions (defined fully in the auth phase).
- CORS explicitly configured, not wildcard-open in any environment other
  than local development.
- All external input validated via Pydantic schemas; no raw dict access
  to request bodies in route handlers.
- Rate limiting applied to authentication and other sensitive endpoints
  once those endpoints exist.
- Secrets only via environment variables, documented in `.env.example`
  with placeholder values — never committed with real values.
- Audit log for sensitive actions: trading mode changes, strategy changes,
  risk changes, signal approval/rejection, order placement, position
  closure, emergency stop. Audit entries record who, what, when, before,
  and after.
- Logs must never contain secrets, tokens, or full credentials.

---

## 9. Trading Safety Requirements

These rules are non-negotiable and apply to every future phase:

1. **Default mode is `PAPER`.** This is enforced in code (a typed default,
   not just documentation) and covered by a test once the mode enum
   exists.
2. **No automatic PAPER/APPROVAL → LIVE transition.** Enabling `LIVE`
   requires all of the following simultaneously:
   - An explicit environment flag (e.g. `LIVE_MODE_ENABLED=true`)
   - Explicit user configuration/action (not a default)
   - Risk engine approval
   - An active, healthy broker connection
   - Overall system health at `HEALTHY`
   - An active trading session window
3. **Fail closed.** Stale market data, broker disconnect, DB/Redis
   failure, strategy crash, or risk-engine unavailability blocks all new
   order creation. The failure is logged and surfaces as a system-health
   warning — it is never swallowed silently.
4. **Explicit trade/order/position state machines.** No implicit states
   inferred from combinations of booleans.
5. **Explainability is mandatory**, not optional, for every signal that
   could lead to an order.
6. **No overstated claims.** No user-facing copy, log message, or default
   configuration may claim guaranteed profit, a guaranteed win rate, or a
   specific accuracy percentage as fact. Backtest/paper results are always
   presented as historical/simulated performance, not a promise.
7. **Strategies are hypotheses.** A strategy version is only eligible for
   `APPROVAL`/`LIVE` consideration after backtesting and out-of-sample
   evaluation exist for it — enforced procedurally in the strategy
   versioning phase.
8. **Emergency Stop** must always be reachable from the UI once it exists,
   must disable new trading and cancel pending orders where safe, and must
   never auto-close existing positions unless explicitly configured or
   requested by the user.

---

## 10. Development Phases

See `docs/development-roadmap.md` for the authoritative, ordered phase
list with entry/exit criteria. Summary:

- **Phase 0** — Governance & contract (this phase).
- **Phase 1** — Repository & tooling skeleton: Docker Compose, `.env.example`,
  Makefile, empty-but-wired backend/frontend scaffolds that boot.
- **Phase 2** — Database & authentication: SQLAlchemy/Alembic
  foundation, the core domain models needed for a full auth flow
  (users, sessions, trading accounts, instruments, strategy/risk
  config, audit/system events, trading settings), JWT auth endpoints,
  a settings foundation, and matching frontend login/protected-route
  support. (Combines what were originally separate "Phase 2/3/4" steps
  — see `docs/development-roadmap.md`'s Phase 2 numbering note.)
- **Phase 4** — Remaining core domain data models & migrations
  (candles, market regimes, signals, orders, positions, trades, etc. —
  the tables not already needed for Phase 2's auth flow).
- **Phase 5** — Market data layer: `MarketDataProvider` interface, full
  Mock/Paper/Broker-interface providers, candle builder, tick
  validation, and a WebSocket broadcast foundation. (Delivered as
  "Phase 3" per direct instruction, ahead of this list's original
  sequence — see `docs/development-roadmap.md`'s Phase 5 numbering
  note.)
- **Phase 6** — Market intelligence: regime detector, multi-timeframe
  engine, market structure, VWAP, moving averages, retracement,
  support/resistance. (Delivered as "Phase 4 — Market Intelligence +
  Regime Engine" and "Phase 5 — Price Action Intelligence Engine" per
  direct instruction — see the roadmap's Phase 6 note.)
- **Phase 7** — Price action, volume, momentum, relative strength
  engines. (Volume, momentum and relative strength delivered in "Phase
  4"; COG, NR, NRB, WRB, BT and TT detectors in "Phase 5".)
- **Phase 8** — Strategy engine: base strategy, concrete strategies,
  strategy router. (Delivered as "Phase 6 — Adaptive Strategy Engine";
  strategies return candidates only, signal generation is Phase 9.)
- **Phase 9** — Signal engine, confidence scoring, candidate ranking,
  stock scanner. (Delivered as "Phase 7 — Signal Engine + Scoring +
  NIFTY 50 Scanner"; the risk check is Phase 10.)
- **Phase 10** — Risk engine.
- **Phase 11** — Order management, paper trading engine, position/exit
  management, trade state machine.
- **Phase 12** — WebSocket real-time layer + REST API completion.
- **Phase 13** — Frontend foundation + dashboard command center.
- **Phase 14** — Remaining frontend pages (scanner, signals, positions,
  trades, analytics, risk, settings, system health).
- **Phase 15** — Backtesting engine + strategy versioning.
- **Phase 16** — Broker adapter architecture + Zerodha adapter structure.
- **Phase 17** — Hardening: full test suite pass, security review,
  documentation pass, final integration validation.

Phases may be split further as work proceeds, but their order and scope
boundaries should not be silently reshuffled — amend the roadmap instead.

---

## 11. Definition of Done — General Template

Every phase, regardless of number, is only complete when **all** of the
following hold:

1. All files described in that phase's scope exist and are committed.
2. The application still boots/imports cleanly (`docker compose up
   --build` succeeds once Docker exists; backend imports cleanly once it
   exists).
3. No hardcoded secrets were introduced.
4. Any trading-relevant logic introduced in the phase has accompanying
   tests, and those tests pass.
5. No safety rule in §9 was weakened or bypassed.
6. Documentation affected by the phase (`README.md`, `docs/architecture.md`,
   `docs/api.md`, `docs/trading-engine.md`, this contract, or the roadmap)
   is updated in the same phase — not deferred.
7. The phase's own exit criteria, as listed in
   `docs/development-roadmap.md`, are explicitly checked off in the phase
   completion report.
8. The next phase has not been started or partially implemented.

Phase-specific Definition of Done items are listed per-phase in
`docs/development-roadmap.md`.
