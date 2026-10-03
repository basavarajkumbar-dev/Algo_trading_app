# Real market data: Zerodha Kite Connect (Phase 13)

Phase 13 connects the platform to **real NSE market data** from Zerodha Kite
Connect while keeping **execution on the paper broker**. Market data and
execution are separate systems: logging in to Zerodha streams real prices;
it never enables real orders.

> **Not demonstrated against a live Kite account.** No Kite credentials were
> available while building this phase. Every component was built to the
> published Kite Connect v3 protocol and tested against protocol-accurate fakes
> (a REST server and a binary WebSocket server). Before relying on it, run the
> smoke test below with your own credentials during market hours.

## Modes

| Setting | Values | Default |
|---|---|---|
| `MARKET_DATA_MODE` | `MOCK` (simulated) · `ZERODHA` (real) | `ZERODHA` when `KITE_API_KEY` + `KITE_API_SECRET` are set, else `MOCK` |
| `EXECUTION_MODE` | `PAPER` · `ZERODHA` | `PAPER` |
| `LIVE_TRADING_ENABLED` | `true` / `false` | `false` |

Effective execution is `ZERODHA` only when `EXECUTION_MODE=ZERODHA` **and**
`LIVE_TRADING_ENABLED=true`, and this build still has no live order path:
orders always go to the paper broker, and the shared Kite REST client refuses
order endpoints unless live execution is effective. Credentials never change
the execution mode.

Other settings: `KITE_ACCESS_TOKEN` (optional, today's token), `KITE_REDIRECT_URL`,
`FRONTEND_URL`, `DATA_STALE_THRESHOLD_SECONDS` (10), `DEFAULT_TIMEFRAME` (5m),
`CANDLE_TIMEFRAMES`, `KITE_TICK_MODE` (quote), `INSTRUMENT_EXCHANGES` (NSE, NFO),
`TICK_MAX_MOVE_PERCENT` (20), `PERSIST_CANDLES` (true), `PERSIST_RAW_TICKS` (false).
The Phase 11 `ZERODHA_*` names remain accepted as aliases.

## Pipeline

```
Kite WebSocket (binary)          Kite REST
  KiteTickerClient                 KiteClient (httpx, rate-limited)
  SubscriptionManager              instruments · ltp · historical · profile
        │                                │
        ▼                                ▼
  TickProcessor ──► NormalizedTick ──► Redis latest state, raw-tick store (opt.)
        │
        ▼ Tick
  MarketDataService ── CandleBuilder (1/3/5/15/30/60m, 09:15-anchored)
        │                    └─► closed candles ─► Postgres market_candles, Redis
        ▼
  intelligence → regime → price action → strategy → signal → risk → PAPER broker
```

Strategies, signals and risk never import a broker module (enforced by
`test_broker_architecture.py`), and the market-data layer does not import the
broker package either: the Zerodha provider plugs into it.

## Authentication (daily)

1. An admin clicks **Settings → Zerodha → Log in** (`GET /broker/zerodha/login`,
   ADMIN). The backend returns the Kite login URL with a one-time `state`
   (10-minute TTL) in `redirect_params`.
2. The browser logs in at Zerodha, which redirects to `KITE_REDIRECT_URL`
   (`/api/v1/broker/zerodha/callback`) with `request_token` and the `state`.
3. The backend checks the state (single use), exchanges the request token for
   an access token (SHA-256 checksum with the API secret), and redirects the
   browser to `FRONTEND_URL/settings?zerodha=connected` — no token in the URL.
4. The access token lives **only in backend memory**. It is never written to
   the database, Redis, logs or any response. It expires at 06:00 IST; a 403
   from Kite or the expiry marks the session `TOKEN_EXPIRED` and the feed stops.

States: `NOT_CONFIGURED`, `LOGIN_REQUIRED`, `AUTHENTICATING`, `AUTHENTICATED`,
`TOKEN_EXPIRED`, `AUTH_ERROR`, `LOGGED_OUT`.

## Instrument master

`broker_instruments` (one row per instrument token; indexes on token,
exchange + tradingsymbol, name, expiry, instrument type) and
`instrument_sync_runs` (every download: counts, status, error). The dump is
validated row by row (invalid rows counted, duplicate tokens rejected),
upserted without duplicates, and instruments that disappear are deactivated.
It is downloaded on login when missing or older than today's 08:30 IST dump.

`InstrumentResolver`: `find_by_symbol`, `find_by_token`, `resolve_nse_equity`,
`resolve_index` (`NIFTY50` → `NIFTY 50`, `BANKNIFTY` → `NIFTY BANK`),
`resolve_nfo_instrument`, `resolve_future`, `resolve_option`. The NIFTY 50
watchlist is resolved through it — no instrument token is hardcoded; symbols
missing from the master are reported, not guessed.

## WebSocket

`KiteTickerClient` implements the documented protocol on asyncio `websockets`
(`wss://ws.kite.trade?api_key=…&access_token=…`, JSON subscribe / unsubscribe /
mode, binary frames of LTP 8 / index 28-32 / quote 44 / full 184-byte packets,
1-byte heartbeats). Why not the official SDK's `KiteTicker`: it runs Twisted in
a thread and Twisted's reactor cannot be restarted, so the daily
logout/login cycle would need a process restart. The parser follows the
SDK's layout (cross-checked against pykiteconnect 5.2.2) and returns UTC-aware
timestamps. REST uses our async httpx client for the same reason (the SDK is
synchronous); its endpoints and error mapping follow the official docs.

* Modes: stocks in QUOTE (no timestamp: the receive time is used), indices in
  FULL (32 bytes, with the exchange timestamp used for latency).
* Reconnect: exponential backoff (1, 2, 4 … 60 s, at most 50 attempts), after
  a network failure, a server close, or a heartbeat timeout (10 s without data).
  An auth failure (HTTP 401/403 on the handshake) stops reconnecting and
  re-validates the session.
* After every (re)connection: subscriptions restored → instruments verified →
  latest prices verified via REST LTP → **LIVE**.
* `SubscriptionManager` never subscribes a token twice and enforces the
  3000-instrument per-connection limit.

## Ticks, candles, staleness

`TickProcessor` rejects (and counts) invalid tokens, unknown instruments,
missing fields, non-positive or non-finite prices, negative quantities, bad /
future / stale timestamps, out-of-order and duplicate ticks, and implausible
jumps (> 20 %). Rejected ticks never reach candles or strategies, and the
socket keeps running. Cumulative volume becomes per-tick volume.

Candles (real data): buckets anchored to 09:15 IST, the last 60-minute candle
clipped at 15:30 (as Kite's own), closed only when their period has ended —
by a later tick or by a once-a-second finalizer (+2 s grace) — never reopened
by a late tick, and nothing after 15:30 is counted. After a mid-session login
the in-progress higher-timeframe candles are completed from 1-minute history,
so the first 60m candle covers the whole hour. No simulated candles exist in
ZERODHA mode. Multi-timeframe defaults stay 60m context / 15m setup / 5m entry.

**Stale-data protection.** With no tick for `DATA_STALE_THRESHOLD_SECONDS`
(or the feed not ready), data is `STALE` / `DISCONNECTED`: signals are marked
non-executable (`blocked_reason = DATA_STALE`) and the risk engine rejects
new paper orders with `DATA_STALE`. Real data also uses that threshold as the
maximum price age in risk checks.

## Historical data and backtests

`HistoricalDataProvider` (`get_historical_candles`, `get_intraday_candles`,
`get_daily_candles`, `get_trading_days`, `get_recent_candles`) with
`ZerodhaHistoricalDataProvider` (chunks by the per-interval day limit:
minute 30, 3/5/10minute 90, 15/30minute 180, 60minute 365, day 2000; 3
requests/s; retries 429s; drops in-progress candles; trading days from the
NIFTY 50 index's daily candles) and `MockHistoricalDataProvider` (the
synthetic series, labelled simulated).

Backtests accept `data_source.kind = "zerodha"`: the worker downloads 5-minute
candles for the symbols and the benchmark into `BACKTEST_CACHE_DIR/zerodha/…`
(CSV, reused) and runs on them. Needs a login; ranges must end before today.
The cache records the range requested from Kite (`coverage.json`): a weekday
inside it with no NIFTY 50 candles was an exchange holiday, so a range that
starts or ends on a holiday (e.g. 2 Oct, Gandhi Jayanti) runs normally.
The synthetic and CSV sources are unchanged.

## Storage

* Postgres: `market_candles` (closed candles, idempotent), `market_ticks`
  (only with `PERSIST_RAW_TICKS=true`), the instrument master, and
  `system_events` for `MARKET_DATA_STALE` / `WEBSOCKET_*` transitions.
* Redis (latest state only, TTLs, coalesced writes):
  `market:tick:{token}`, `market:ltp:{token}`, `market:candle:{symbol}:{tf}`
  (last 300), `market:status`, `market:subscription`. In SIMULATED mode only
  `market:status` (with `simulated: true`) is written, so these keys never
  hold simulated prices.

## Orders, reconciliation, health

* `BrokerAdapter` gains `get_trades`, `get_order_trades`, `get_margins`
  (paper, mock, Zerodha). `OrderRequest`/`Order` carry `client_order_id`,
  `strategy_signal_id`, `correlation_id`, `broker_order_id`.
* An uncertain order is reconciled, never re-sent; `OrderManager.retry_lost`
  re-sends only an order reconciliation has confirmed LOST, after one more
  reconcile, with a new client id linked by the correlation id.
* `OrderReconciliationService` (`POST /broker/reconciliation`) compares local
  vs broker orders, trades and positions: missing / unknown orders, quantity,
  status, average-price, trade and position mismatches (read-only).
* `GET /system/broker-health`: `HEALTHY` / `DEGRADED` / `DISCONNECTED` /
  `AUTH_REQUIRED` / `ERROR` with each check (database, Redis, auth, REST,
  instrument master, WebSocket, freshness).
* `GET /system/market-data`: provider, mode, WebSocket, REST, last tick,
  latency, subscriptions, stale, candle status.

## Dashboard

Every page's header shows **DATA SOURCE: ZERODHA LIVE / ZERODHA · STALE /
ZERODHA · DISCONNECTED / SIMULATED**. The Command Center and System pages show
the *Market Data & Broker* panel (broker, auth, market data, execution — never
LIVE unless live execution is enabled — last tick, latency, subscriptions,
WebSocket, REST, instrument master and 5m/15m/60m candle dots). Settings has
the Zerodha login/logout card.

## Manual setup and verification (with your account)

1. Create an app at developers.kite.trade; set its redirect URL to
   `http://localhost:8000/api/v1/broker/zerodha/callback` (or your host).
2. In `.env` (never committed, never in the frontend):
   `KITE_API_KEY=…`, `KITE_API_SECRET=…`,
   `KITE_REDIRECT_URL=http://localhost:8000/api/v1/broker/zerodha/callback`,
   `FRONTEND_URL=http://localhost:3000`. Keep `EXECUTION_MODE=PAPER`,
   `LIVE_TRADING_ENABLED=false`.
3. Start the stack, log in as an ADMIN, open **Settings → Zerodha → Log in**.
4. Check `GET /api/v1/system/market-data` (`status: LIVE` during market
   hours) and the DATA SOURCE badge.
5. Smoke test (read-only, never places an order):
   `KITE_ACCESS_TOKEN=<today's token> python -m app.scripts.test_zerodha_connection`
   — configuration, authentication, account, instrument master, resolution,
   REST quotes, historical, WebSocket ticks, safety. Outside market hours step 8
   reports no ticks (Kite sends none).

## Known limits

* Live Kite connectivity is unverified here (no credentials; outbound access to
  Kite is not available in the build environment).
* One WebSocket connection (3000 instruments) — enough for NIFTY 50 + indices.
* Holidays come from data (no candles), not from an exchange calendar file.
* The Zerodha backtest universe is the chosen symbols (survivorship bias possible).
* Docker images could not be built in the build environment (Docker Hub is not
  reachable from it); `docker compose config` validates.
