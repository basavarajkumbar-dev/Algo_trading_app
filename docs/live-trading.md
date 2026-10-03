# Live trading safety layer (Phase 14)

This phase adds the safeguards for sending **real orders** to Zerodha. It is
about refusing unsafe orders, not about profitability: nothing here predicts
or guarantees a result.

> **Status.** Built and tested against a protocol-accurate fake of the Kite REST
> API (orders, fills, cancellations, lost responses). It has **not** placed an
> order on a real Zerodha account. Read "Before the first live order" below.

**Default: PAPER.** Live orders need *all* of the following at once:

1. **Configuration** on the backend: `EXECUTION_MODE=ZERODHA` **and**
   `LIVE_TRADING_ENABLED=true`. Kite credentials alone never enable it.
2. **An explicit user action**: an ADMIN arms live trading on the **Live** page
   by typing `ARM LIVE TRADING` and entering their password. Arming is held in
   memory, lasts until 15:30 IST that day, and ends on a disarm, the kill
   switch, a lost Kite session, an unhealthy Kite auth, or any restart.
   Nothing re-arms automatically.
3. **Every pre-trade check passing** (below), including the risk engine.
4. **The execution gate** in the OrderManager (armed, broker connected and
   reconciled, health HEALTHY, NSE session open, no kill switch).

## Pipeline

```
READY signal ──► RiskManager (live limits, mode LIVE only when authorised)
             ──► PreTradeValidator (20 checks; any failure = no order)
             ──► LiveOrderManager (entry → SL-M stop + LIMIT target → exit)
             ──► OrderManager (execution gate, idempotency, reconciliation)
             ──► ZerodhaBrokerAdapter ──► Kite
```

Signals reach live trading from the **Live** page (pre-flight, then *Place LIVE
order*, ADMIN) or from **live auto trading** (ADMIN toggle, only while armed:
the top READY candidate of each scan, one attempt per scan). Paper trading and
APPROVAL mode are unchanged and keep using the paper broker.

## Pre-flight checks (PreTradeValidator)

| Check | Passes when |
|---|---|
| live_configured | `EXECUTION_MODE=ZERODHA` and `LIVE_TRADING_ENABLED=true` |
| armed | an admin armed live trading this session |
| broker_connected | Zerodha CONNECTED and reconciled after any reconnect |
| market_open | Mon–Fri, 09:15 IST to the last entry time, not a configured holiday |
| market_data_healthy | real Zerodha data, feed LIVE, this symbol fresh |
| websocket_healthy | the Kite ticker is connected |
| database_healthy / redis_healthy | in the latest health report (≤ 20 s old) |
| broker_health | the broker health monitor reports HEALTHY |
| risk_engine_healthy | running for today, no daily-loss / trade-count / loss-streak block |
| no_emergency_stop | kill switch and paper emergency stop both off |
| no_unresolved_trade | no live trade left open by a previous run |
| risk_limits_valid | risk ≤ 2 %/trade, daily loss ≤ 5 %, live caps applied, 1 position, no leverage, real session |
| risk_approved | the RiskManager approved the trade (all of its checks) |
| account_balance | Zerodha available cash ≥ quantity × entry |
| no_duplicate_position | no open position at the broker for any symbol (max 1), no working order for the symbol |
| no_stale_signal | signal active, confirmed within `LIVE_SIGNAL_MAX_AGE_SECONDS`, not already traded |
| valid_quantity | > 0, ≤ `LIVE_MAX_ORDER_QUANTITY`, value ≤ `LIVE_MAX_POSITION_VALUE` |
| valid_stop | right side of entry, ≥ 1 tick, ≤ `LIVE_MAX_STOP_PERCENT` away |
| valid_target | right side of entry, risk/reward ≥ `LIVE_MIN_RISK_REWARD` |

Every evaluation, passed or failed, is written to `audit_logs`
(`LIVE_PRETRADE_CHECK`) with the failed checks.

## Order lifecycle (LiveOrderManager)

* **Entry**: LIMIT at the signal's entry price (rounded to the instrument's
  tick), product MIS, exchange NSE. Unfilled after `LIVE_ENTRY_TIMEOUT_SECONDS`
  (60): the remainder is cancelled; nothing filled → trade CANCELLED.
* **Protection**: as soon as any quantity fills, a **SL-M stop** and a
  **LIMIT target** are placed for the filled quantity and resized as more fills.
* **One-cancels-other**: when one exit leg fills, the other is cancelled.
* **Square-off** (15:15 IST by default), **manual exit** or a **kill switch
  that asks for it**: both legs are cancelled, then a MARKET exit is sent for
  the open quantity.
* Exits are **reduce-only**: they may be sent under the kill switch, after a
  disarm or outside the entry window, but never for more than is open.
* **One active trade** (`max_active_positions = 1`; other values are refused).

## Never a blind retry

* Client order ids derive from a correlation id (`L` + the signal id): the same
  signal can never produce a second entry; the OrderManager returns the order
  it already has, and the adapter's idempotency tag (`GZ…`) is checked again.
* Each order carries `client_order_id`, `strategy_signal_id`,
  `correlation_id` and, once known, `broker_order_id`.
* If Kite may have received an order but the response was lost, the order is
  marked *outcome unknown* and **reconciled, never re-sent**; it is adopted by
  its tag when it appears in the order book.

## Kill switch

Engage it from the Live page (TRADER or ADMIN), or with the paper emergency
stop (which now also engages it):

* blocks every new entry and disarms live trading;
* cancels a pending live entry;
* keeps an open position's stop and target **and does not close the position**,
  unless `LIVE_CLOSE_POSITIONS_ON_KILL=true` or you tick *Also close an open
  live position*;
* stops paper trading too.

It stays engaged until an ADMIN resets it; a reset does not re-arm.

## BrokerHealthMonitor and reconciliation

Every 5 s the monitor takes the broker health report (database, Redis, Kite
auth, REST, instrument master, WebSocket, data freshness). Only `HEALTHY`
lets the gate send a new entry; a report older than 20 s counts as failed.
While armed, every health change is audited, and `AUTH_REQUIRED` disarms.
The OrderManager reconciles the live trade's orders every `LIVE_POLL_SECONDS`;
`POST /broker/reconciliation` compares local and broker orders, trades and
positions.

## Restart recovery

A live trade that was not finished when the backend stopped is found at startup
(`live_trades`). Arming and new live entries stay blocked until an ADMIN checks
positions and orders in Kite and acknowledges it on the Live page (audited).
The app does not try to re-adopt such orders automatically.

## Audit

Every live action is stored in `audit_logs` with who, what and when:
`LIVE_ARMED`, `LIVE_ARM_REFUSED`, `LIVE_DISARMED`, `LIVE_AUTO_TRADING`,
`LIVE_PRETRADE_CHECK`, `LIVE_ORDER_PLACED`, `LIVE_ORDER_MODIFIED`,
`LIVE_ORDER_CANCELLED`, `LIVE_ENTRY_FILL`, `LIVE_EXIT_FILL`,
`LIVE_TRADE_CLOSED`, `LIVE_EXIT_REQUESTED`, `LIVE_KILL_SWITCH`,
`LIVE_KILL_SWITCH_RESET`, `LIVE_HEALTH_CHANGED`, `LIVE_RECOVERY_ACKNOWLEDGED`.
`GET /api/v1/live/audit` lists them; the Live page shows the trail.

## API

`GET /live/status` · `POST /live/arm` (ADMIN) · `POST /live/disarm` ·
`POST /live/preflight` (no order) · `POST /live/execute` (ADMIN) ·
`POST /live/auto` (ADMIN) · `POST /live/exit` · `POST /live/kill-switch` ·
`POST /live/kill-switch/reset` (ADMIN) · `POST /live/recovery/acknowledge`
(ADMIN) · `GET /live/audit`.

## Settings

`LIVE_MAX_ACTIVE_POSITIONS` (1), `LIVE_MAX_POSITION_VALUE` (25000),
`LIVE_MAX_ORDER_QUANTITY` (5000), `LIVE_MAX_STOP_PERCENT` (3),
`LIVE_MIN_RISK_REWARD` (1.0), `LIVE_SIGNAL_MAX_AGE_SECONDS` (120),
`LIVE_ENTRY_TIMEOUT_SECONDS` (60), `LIVE_CLOSE_POSITIONS_ON_KILL` (false),
`LIVE_POLL_SECONDS` (2). Live also uses the risk settings (risk per trade,
daily loss limit, trades per day, loss streak, last entry and square-off
times), always on the real NSE session, with no leverage.

## Before the first live order

1. Zerodha requires a **static IP** registered for your app for order APIs
   (since 1 April 2026); orders from other IPs are rejected.
2. Run on real data for a few sessions with live **disarmed**, using
   *Run pre-flight* to see which checks pass.
3. Start with a small `LIVE_MAX_POSITION_VALUE` and low `RISK_PER_TRADE`.
4. Try the kill switch once while disarmed, and know how to close positions
   directly in Kite.
5. Keep watching: live auto trading still needs a person able to intervene.

## Known limits

* Never run against a real account in this build environment; the order flow
  is verified only against the fake Kite server.
* Entries are LIMIT orders; a fast market may leave them unfilled (cancelled at
  the timeout) rather than chasing the price.
* MIS margin/leverage is not used: the balance check is conservative (full
  position value against available cash).
* APPROVAL mode still executes on the paper broker; approved trades are not
  routed to live in this phase.
* Charges and taxes are not deducted from the live gross P&L shown.
