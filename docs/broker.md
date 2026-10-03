# Broker layer and Zerodha adapter (Phase 11)

> **PAPER mode.** Orders execute on the simulated paper broker. Zerodha,
> when configured, is connected **read-only**: nothing in this build
> places, modifies or cancels a real order, and live trading is never
> enabled automatically.

## Architecture

```
STRATEGY -> SIGNAL -> RISK -> ORDER MANAGER -> BROKER INTERFACE -> ZERODHA ADAPTER
                                  |                 (BrokerAdapter)
                                  |                   |- PaperBrokerAdapter   simulated fills (paper trading)
                                  |                   |- MockBrokerAdapter    scriptable, fault injection (tests)
                                  |                   '- ZerodhaBrokerAdapter Kite Connect v3
                            execution gate
```

* `app/execution/broker.py` — the `BrokerAdapter` interface:
  `connect`, `disconnect`, `health_check`, `get_profile`, `get_balance`,
  `get_positions`, `get_orders`, `get_order_status`, `place_order`,
  `modify_order`, `cancel_order`, `get_instruments`, plus the error
  contract (`BrokerError`, `BrokerUnavailableError`, `BrokerAuthError`,
  `BrokerRateLimitError`, `BrokerNotConfiguredError`,
  `LiveOrdersDisabledError`).
* `app/execution/order_manager.py` — `OrderManager` and `ExecutionGate`.
* `app/brokers/mock.py`, `app/brokers/zerodha/` (`client.py`,
  `mapping.py`, `adapter.py`), `app/brokers/service.py`.
* Strategy, signal, scanner, regime, intelligence, price-action and risk
  code never import an adapter, the Kite client or an HTTP library; only
  `app/brokers/zerodha` uses HTTP. Both rules are enforced by
  `app/tests/test_broker_architecture.py`.
* The paper trading engine keeps driving its `PaperBrokerAdapter`
  directly (it also feeds it prices); the paper adapter implements the
  same interface and works behind the `OrderManager` too (tested).

## What was verified (Zerodha documentation, October 2026)

From the official Kite Connect v3 documentation and Zerodha's notice of
25 March 2026 on SEBI's retail algo rules:

| Item | Value used |
|---|---|
| API root | `https://api.kite.trade` (`ZERODHA_API_BASE_URL`) |
| Headers | `X-Kite-Version: 3`, `Authorization: token <api_key>:<access_token>` |
| Login | `https://kite.zerodha.com/connect/login?v=3&api_key=…` → redirect with `request_token` → `POST /session/token` with `checksum = sha256(api_key + request_token + api_secret)` |
| Token expiry | access token expires at **06:00 IST the next day** |
| Requests | form-encoded; JSON `{status, data}`; errors `{status: "error", message, error_type}` |
| Orders | `POST /orders/:variety`, `PUT /orders/:variety/:order_id`, `DELETE /orders/:variety/:order_id`, `GET /orders`, `GET /orders/:order_id` (history) |
| Order params | `tradingsymbol, exchange, transaction_type, order_type (MARKET/LIMIT/SL/SL-M), quantity, product (MIS/CNC/NRML/MTF), validity, price, trigger_price, tag (≤ 20 alphanumeric), market_protection` |
| Market protection | **required** for MARKET and SL-M orders since 1 April 2026; `0` is rejected; `-1` = Zerodha's automatic protection |
| Static IP | since **1 April 2026** order requests are accepted only from a static IP registered at developers.kite.trade (one per account; read APIs work from any IP) |
| Rate limits | 10 orders/second per account (more needs an exchange-registered strategy; excess → HTTP 429), 400 orders/minute, 5,000/day, 25 modifications per order; 10 requests/second for other endpoints, 1/second for quotes |
| Other | `GET /portfolio/positions` (`net`, `day`), `GET /user/margins/equity`, `GET /user/profile`, `GET /instruments[/:exchange]` (daily gzipped CSV) |

Sources:
[Kite Connect v3 docs](https://kite.trade/docs/connect/v3/),
[Orders](https://kite.trade/docs/connect/v3/orders/),
[User / login](https://kite.trade/docs/connect/v3/user/),
[Exceptions and rate limits](https://kite.trade/docs/connect/v3/exceptions/),
[Portfolio](https://kite.trade/docs/connect/v3/portfolio/),
[Market quotes / instruments](https://kite.trade/docs/connect/v3/market-quotes/),
[Zerodha: preparing to comply with SEBI's retail algo rules](https://kite.trade/forum/discussion/15912/preparing-to-comply-with-sebis-retail-algo-rules-static-ip-ratelimits-order-types).
Re-check them before any live use: rules and limits change.

## Configuration

Environment variables only; nothing is stored in the database or code.

| Variable | Default | Meaning |
|---|---|---|
| `ZERODHA_API_KEY` | empty | Kite Connect app key |
| `ZERODHA_API_SECRET` | empty | app secret (a `SecretStr`: never printed) |
| `ZERODHA_ACCESS_TOKEN` | empty | optional; otherwise obtained by the login flow and kept in memory |
| `ZERODHA_API_BASE_URL` | `https://api.kite.trade` | |
| `ZERODHA_LOGIN_URL` | `https://kite.zerodha.com/connect/login` | |
| `ZERODHA_MARKET_PROTECTION` | `-1` | `-1` automatic, or 0 < x ≤ 100 %; `0` is refused at startup |
| `ZERODHA_ORDERS_PER_SECOND` | `8` | client-side cap (≤ 10) |
| `ZERODHA_TIMEOUT_SECONDS` | `10` | |
| `BROKER_HEALTH_INTERVAL_SECONDS` | `30` | health check / reconnect cadence |
| `BROKER_RECONCILE_INTERVAL_SECONDS` | `10` | order-book reconciliation cadence while connected |

**Missing credentials → PAPER.** Without key and secret the adapter is
`NOT_CONFIGURED`, the live-broker health component is HEALTHY ("no live
broker needed"), and everything else runs unchanged.

**Never commit** the API key, API secret, access token or a password.
`.env` is git-ignored; `.env.example` keeps the Zerodha values empty (a
test checks this). Secrets never appear in `repr`, logs, exceptions or
API responses (tested).

## Connection states

`NOT_CONFIGURED` → no credentials. `AUTH_REQUIRED` → no token, token past
06:00 IST, or Kite answered 403 / `TokenException` (log in again).
`CONNECTING`, `CONNECTED` (verified with `GET /user/profile`), `DEGRADED`
(a recent call failed), `DISCONNECTED` (3 consecutive failures, or
disconnected on request). Live orders are disarmed on any drop.

The `BrokerService` re-checks health every 30 s, reconnects after a
failure (not after a requested disconnect), and reconciles orders every
10 s while connected.

## Order status mapping

| Kite status | Status |
|---|---|
| `PUT ORDER REQ RECEIVED`, `VALIDATION PENDING`, `OPEN PENDING`, `AMO REQ RECEIVED` | `SUBMITTED` |
| `OPEN` | `OPEN`, or `PARTIALLY_FILLED` once `filled_quantity > 0` |
| `TRIGGER PENDING` | `TRIGGER_PENDING` |
| `MODIFY VALIDATION PENDING`, `MODIFY PENDING`, `MODIFIED`, `CANCEL PENDING` | the working status it had |
| `COMPLETE` | `FILLED` |
| `CANCELLED` | `CANCELLED` (filled quantity kept: partly filled, then cancelled) |
| `REJECTED` | `REJECTED` with `status_message` as the reason |
| anything else | previous status kept; raw text stored in `broker_status` |

## Order safety

* **Execution gate (fail closed).** A live order needs every condition of
  `PROJECT_CONTRACT.md` §9: `TRADING_MODE=LIVE` **and**
  `LIVE_MODE_ENABLED=true`, an explicit user arming action, risk-engine
  approval of that order, a CONNECTED and reconciled broker, overall
  system health HEALTHY, an open session, and no emergency stop. The
  adapter is armed only for the duration of one gated request. **This
  build has no arming action**, so live orders cannot happen.
* **Duplicate protection.** Every order carries a `client_order_id`.
  The order manager returns the already-tracked order for a repeated id
  (no second request), serialises concurrent submissions of one id, and
  refuses an identical working order (same symbol, side, quantity, type
  and purpose) within 5 s. The adapter sends a deterministic tag
  (`"GZ" + 18 hex of sha256(client_order_id)`) as the Kite `tag` and
  rejects an id it has seen.
* **Network failure.** Reads are retried (2×) on connection errors and
  502/503/504. Writes are never retried. If a placement may have reached
  Kite (timeout, reset, 5xx), the order is `SUBMITTED` with
  `needs_reconciliation`; it is never re-sent. Reconciliation finds it by
  tag and adopts it (`ORDER_RECOVERED`), or after 3 readable order books
  and 30 s marks it failed (`ORDER_LOST`), re-adopting it if it appears
  later (`ORDER_REAPPEARED`). A connection that could not be opened means
  the order was not sent (`REJECTED: not sent`).
* **Failed orders.** Input, order, margin and holding exceptions, 429s and
  exchange rejections become `REJECTED` with the broker's reason and an
  `ORDER_REJECTED` event; nothing is retried automatically.
* **Partial fills.** Reconciliation emits `ORDER_PARTIAL_FILL` /
  `ORDER_FILLED` with the filled-quantity delta and average price.
* **Disconnect.** New orders are blocked while the broker is not
  CONNECTED; after a reconnect a successful reconciliation is required
  before orders are accepted (`resync_pending`).
* **External orders.** Orders in the Zerodha order book that this app did
  not place are reported once (`EXTERNAL_ORDER`) and never managed.
* **Emergency stop** also cancels working live-broker orders when live
  trading is configured (positions are never closed by it).
* Order modifications are capped at Kite's 25 per order.

All events are stored as system events (`BROKER_*`) and pushed over the
WebSocket, so they appear in the System page's event log.

## Logging in (read-only use)

1. Register a Kite Connect app at developers.kite.trade with this app's
   redirect URL, and set `ZERODHA_API_KEY` / `ZERODHA_API_SECRET`.
2. As an ADMIN: `GET /api/v1/broker/zerodha/login-url`, open it, log in.
3. Take the `request_token` from the redirect and
   `POST /api/v1/broker/zerodha/session {"request_token": "…"}`. The
   access token is kept in memory only and expires at 06:00 IST.
4. `GET /api/v1/broker/status` and the System page show the connection;
   `/broker/zerodha/{profile,balance,positions,orders}` are read-only.

For any future live use, the order endpoints also need a static IP
registered with Zerodha.

## Tests

`test_zerodha_adapter.py` (against `fake_kite.py`, a fake Kite server on
`httpx.MockTransport`, no network), `test_order_manager.py` (mock, paper
and Zerodha behind the order manager and gate),
`test_broker_conformance.py` (all three adapters, same interface),
`test_broker_architecture.py`, `test_broker_api.py` (Postgres).

## Not in this phase

* No arming action, no live-order endpoint, no automatic switch to LIVE.
* No Kite WebSocket order updates or postbacks (reconciliation polls the
  order book), no live market data from Kite, no per-fill trade records
  (`GET /orders/:id/trades`), no cover / iceberg / AMO orders.
* The paper engine is not routed to Zerodha.
