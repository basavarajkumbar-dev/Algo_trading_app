# GAINZALGO PRO MAX — API Reference

Covers the API surface that exists through Phase 9. Interactive,
always-current documentation is also available at `/docs` (Swagger UI)
and `/redoc` on the running backend. This file is a narrative
companion, not a replacement — update it in the same phase that changes
the API surface, per `PROJECT_CONTRACT.md`'s Definition of Done.

Base URL: `/api/v1`

All request/response bodies are JSON unless noted otherwise. All
timestamps are ISO 8601 UTC.

---

## System

### `GET /system/health`

No authentication required. Reports whether the API, PostgreSQL, and
Redis are reachable — nothing more. Does not indicate anything about
market data, strategies, or trading subsystems, none of which exist
yet.

```json
{
  "status": "HEALTHY",
  "app_name": "GAINZALGO PRO MAX",
  "app_env": "development",
  "demo_mode": true,
  "trading_mode": "PAPER",
  "live_trading_active": false,
  "components": [
    { "name": "database", "status": "CONNECTED", "detail": null },
    { "name": "redis", "status": "CONNECTED", "detail": null }
  ],
  "notes": [
    "Market data is simulated (mock provider) — no live market feed is connected in this phase.",
    "No broker is connected and no real orders can be placed."
  ]
}
```

`status` is one of `HEALTHY`, `WARNING`, `CRITICAL`, `OFFLINE`.
`components[].status` is one of `CONNECTED`, `PENDING`, `DISCONNECTED`,
`NOT_CONFIGURED`.

---

## Auth

None of these endpoints require authentication except `/auth/me`.

### `POST /auth/register`

Creates a user, plus a default `TradingSettings` row (`trading_mode:
PAPER`) and a default `RiskConfig` row, in the same transaction. Returns
tokens immediately (no separate login step needed after registering).

Request:
```json
{ "email": "you@example.com", "password": "at-least-8-chars", "full_name": "Optional Name" }
```

Response `201`: a `TokenResponse` (see below).

Errors: `409` if the email is already registered. `422` for validation
failures (email format — checked syntactically only, no DNS/MX lookup;
password under 8 characters).

### `POST /auth/login`

Request:
```json
{ "email": "you@example.com", "password": "your-password" }
```

Response `200`: a `TokenResponse`. Error `401` for any wrong
email/password combination (the message does not reveal which field
was wrong).

### `POST /auth/refresh`

Rotates the refresh token: the one submitted is revoked, and a new
access/refresh pair is issued. Reusing an already-rotated refresh token
fails.

Request:
```json
{ "refresh_token": "..." }
```

Response `200`: a `TokenResponse`. Error `401` if the token is invalid,
expired, or already revoked/rotated.

### `POST /auth/logout`

Revokes the given refresh token's session. Idempotent — logging out
with an unknown or already-revoked token still returns `204`.

Request:
```json
{ "refresh_token": "..." }
```

Response: `204 No Content`.

### `GET /auth/me`

Requires `Authorization: Bearer <access_token>`.

Response `200`:
```json
{ "id": "uuid", "email": "you@example.com", "full_name": "Optional Name", "role": "TRADER", "is_active": true }
```

Error `401` if the header is missing, malformed, or the token is
invalid/expired.

### `TokenResponse` shape

Returned by `/auth/register`, `/auth/login`, and `/auth/refresh`:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "user": { "id": "uuid", "email": "...", "full_name": "...", "role": "TRADER", "is_active": true }
}
```

Access tokens expire in 30 minutes; refresh tokens in 7 days (both
configurable via `Settings`, not hardcoded — see `app/core/config.py`).

---

## Settings

All settings endpoints require `Authorization: Bearer <access_token>`
and operate on the caller's own settings row — there is no way to read
or modify another user's settings in this phase.

### `GET /settings/me`

Response `200`:
```json
{
  "id": "uuid",
  "user_id": "uuid",
  "trading_mode": "PAPER",
  "paper_capital": 100000.0,
  "auto_trading_enabled": false,
  "signal_score_threshold": 85.0,
  "trading_start_time": "09:15:00",
  "trading_end_time": "15:15:00",
  "emergency_stop": false
}
```

### `PATCH /settings/me`

Request (all fields optional; only send what you want to change):
```json
{ "paper_capital": 250000, "signal_score_threshold": 80, "trading_start_time": "09:20:00", "trading_end_time": "15:00:00" }
```

**Deliberately not editable through this endpoint:** `trading_mode` and
`auto_trading_enabled`. Changing trading mode is a safety-critical
action (`PROJECT_CONTRACT.md` section 9) that will get its own
explicit, audited endpoint once a real trading engine exists to make it
meaningful — it is not a field on a generic settings form.

Response `200`: the updated `TradingSettingsResponse` (same shape as
`GET`).

---

## Roles

`ADMIN`, `TRADER`, `VIEWER` exist on every user (default: `TRADER`).
`backend/app/api/deps.py` provides `require_role(*roles)` for future
routes to depend on, but no route in this phase is role-gated — there
is nothing sensitive enough yet to need it.

---

## What's not here yet

Scanner, signals, positions, orders, trades, analytics, risk,
strategies, system start/pause/stop/emergency, and backtesting
endpoints described in `PROJECT_CONTRACT.md` do not exist yet. See
`docs/development-roadmap.md` for when each arrives.

---

## Market data

Read-only, no authentication required. All prices, volumes, and
candles are simulated — see the `notes` field on every response below
and `PROJECT_CONTRACT.md` section 9.

### `GET /market/status`

```json
{
  "provider_status": "CONNECTED",
  "is_streaming": true,
  "watchlist": ["NIFTY50", "RELIANCE", "TCS", "..."],
  "connected_websocket_clients": 0,
  "ticks_received": 42,
  "ticks_accepted": 42,
  "ticks_rejected": 0,
  "rejected_by_issue": {},
  "started_at": "2026-09-20T02:06:12.914085Z",
  "notes": [
    "Market data is simulated (mock provider) — not a live feed.",
    "No broker is connected and no real orders can be placed."
  ]
}
```

### `GET /market/instruments`

Returns the NIFTY 50 universe (50 constituents) plus the synthetic
`NIFTY50` composite index — 51 entries total.

```json
[{ "symbol": "RELIANCE", "name": "Reliance Industries Ltd.", "exchange": "NSE", "sector": "Oil Gas & Consumable Fuels", "is_active": true }]
```

### `GET /market/snapshot/{symbol}`

```json
{ "symbol": "RELIANCE", "price": 1226.07, "change": -0.33, "change_percent": -0.03, "volume": 444, "timestamp": "2026-09-20T02:06:18.917149Z" }
```

`404` if the symbol isn't on the watchlist or hasn't ticked yet since
startup.

### `GET /market/candles?symbol=&timeframe=&limit=`

`timeframe` is one of `1m`, `3m`, `5m`, `15m`, `30m`, `60m` (default
`5m`). `limit` defaults to 100, max 500.

```json
[{ "symbol": "RELIANCE", "timeframe": "1m", "timestamp": "2026-09-20T02:06:00Z", "open": 1226.07, "high": 1226.07, "low": 1226.07, "close": 1226.07, "volume": 444, "is_closed": false }]
```

The last entry in the list may be the in-progress (not yet closed)
candle for the current bucket.

### `WS /ws/market`

No authentication in this phase. On connect, the server immediately
sends:

1. `{"type": "connected", "message": "...", "watchlist": [...]}`
2. One `{"type": "market_snapshot", ...}` per watchlist symbol that has
   ticked at least once.
3. The latest `{"type": "market_intelligence", ...}` picture, once the
   first analysis has run.

Then, as the simulated market moves:

- `{"type": "market_tick", "symbol": "...", "price": ..., "volume": ..., "timestamp": "..."}`
- `{"type": "candle_closed", "symbol": "...", "timeframe": "...", "timestamp": "...", "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}`
- `{"type": "market_intelligence", "snapshot": {...}, "symbols": [...]}`:
  same shape as `GET /market/intelligence` without `status`, sent on
  every refresh (default every 5 s).
- `{"type": "market_regime", "symbol": "...", "regime": "...", "previous_regime": "...", "changed": true, "confidence": 72.5, "reasons": [...], "timestamp": "..."}`:
  sent for any symbol whose regime changed, and for the benchmark
  (`NIFTY50`) on every refresh with `changed` set accordingly.

The endpoint is broadcast-only in this phase — there is no per-client
symbol filtering yet, and any inbound client message is ignored (it is
still read, so the server notices disconnects promptly).

---

## Market intelligence

Read-only, no authentication required. Everything below is computed
from **closed** candles of the simulated feed (default timeframe `5m`),
so a value never repaints mid-candle. A regime describes how price has
been behaving; every regime result carries this note:

> A regime describes recent price behaviour. It is not a forecast and
> does not guarantee future price movement.

All endpoints return `503` if the intelligence service is not running.

### `GET /market/intelligence`

```json
{
  "snapshot": {
    "benchmark_symbol": "NIFTY50",
    "timeframe": "5m",
    "market_direction": "DOWN",
    "market_regime": "STRONG_BEARISH",
    "confidence": 75.0,
    "volatility": "NORMAL",
    "trend_strength": 68.0,
    "momentum": 64.0,
    "breadth": { "symbols_counted": 50, "advancing": 28, "declining": 22, "unchanged": 0, "above_vwap": 31, "below_vwap": 19, "percent_above_vwap": 62.0, "advance_decline_ratio": 1.27, "bullish_regimes": 17, "bearish_regimes": 15 },
    "regime_distribution": { "STRONG_BULLISH": 17, "STRONG_BEARISH": 15, "RANGE": 11, "BREAKOUT": 4, "HIGH_VOLATILITY": 2, "UNKNOWN": 1 },
    "factors": [{ "key": "trend", "label": "Trend", "score": 68.0, "scale": "intensity", "interpretation": "Downtrend", "detail": "..." }],
    "regime": { "...": "RegimeResult, see below" },
    "timestamp": "2026-10-01T15:11:44Z",
    "note": "..."
  },
  "symbols": [
    { "symbol": "NIFTY50", "name": "NIFTY 50", "price": 25008.59, "change_percent": -2.54, "regime": "STRONG_BEARISH", "confidence": 75.0, "top_reason": "Lower-high, lower-low structure", "trend_quality_score": 68.0, "trend_direction": "DOWN", "momentum_score": 64.0, "relative_strength_score": null, "relative_strength_rank": null, "price_vs_vwap": "BELOW", "volatility_level": "NORMAL", "relative_volume": 1.43, "data_as_of": "2026-10-01T15:05:00Z" }
  ],
  "status": { "is_running": true, "timeframe": "5m", "refresh_seconds": 5.0, "refresh_count": 2, "last_refresh_at": "...", "last_compute_ms": 60.1, "last_error": null, "symbols_analyzed": 51 }
}
```

`symbols` lists the benchmark first, then constituents. Factor `scale`
is `directional` (50 = neutral, above = bullish) or `intensity` (0-100
strength with no direction, e.g. volatility or trend quality).

### `GET /market/intelligence/{symbol}`

Full engine output for one symbol: `regime` (a `RegimeResult`),
`factors`, `relative_strength_rank`, and `analysis` with one block per
engine (`vwap`, `moving_averages`, `volatility`, `momentum`, `volume`,
`structure` including labelled swings, `range`, `relative_strength`,
`trend_quality`). Engines that lacked enough history are `null` and
listed in `analysis.unavailable` with the reason. `404` for a symbol
not on the watchlist.

### `GET /market/regime?symbol=`

Just the `RegimeResult`; `symbol` defaults to the benchmark.

```json
{
  "symbol": "NIFTY50",
  "timeframe": "5m",
  "regime": "STRONG_BEARISH",
  "confidence": 75.0,
  "reasons": ["Lower-high, lower-low structure", "Price below VWAP (-1.29%)", "Negative EMA slope (-0.024%/candle)"],
  "counter_reasons": ["Momentum not bearish (score 64)"],
  "features": { "price_vs_vwap": "BELOW", "ema_fast_slope_percent": -0.024, "atr_ratio": 0.87, "...": "..." },
  "scores": { "STRONG_BULLISH": 20.0, "STRONG_BEARISH": 75.0, "BREAKOUT": 0.0, "RANGE": 35.0, "HIGH_VOLATILITY": 15.0 },
  "candles_analyzed": 250,
  "data_as_of": "2026-10-01T15:05:00Z",
  "timestamp": "2026-10-01T15:11:44Z",
  "note": "A regime describes recent price behaviour. It is not a forecast and does not guarantee future price movement."
}
```

Regimes: `STRONG_BULLISH`, `STRONG_BEARISH`, `RANGE`, `BREAKOUT`,
`HIGH_VOLATILITY`, `UNKNOWN`. `HIGH_VOLATILITY` wins outright when its
score reaches the override threshold; when no regime reaches the
minimum score the result is `UNKNOWN`. Confidence is the winning score,
reduced when the runner-up is close behind.

### `GET /market/intelligence/config`

Every weight and threshold in use: `benchmark_symbol`, `analysis`
(engine lookbacks/periods) and `regime` (`RegimeConfig` condition
weights and thresholds). Useful for auditing why a regime was chosen.

---

## Price action

Read-only, no authentication required. Computed by `PriceActionService`
every `PRICE_ACTION_REFRESH_SECONDS` (default 15 s) from **closed**
candles of the simulated feed. Patterns are contextual features, not
trade signals, and levels are reference zones, not guaranteed reversal
points. No endpoint returns orders, quantities, stops or targets.

All endpoints return `503` if the service is not running.

### `GET /market/price-action`

One summary row per watched symbol plus service status.

```json
{
  "symbols": [
    {
      "symbol": "BAJAJFINSV",
      "name": "Bajaj Finserv Ltd.",
      "price": 1852.5,
      "data_as_of": "2026-10-01T15:45:00Z",
      "patterns": [{ "pattern": "WRB", "direction": "BULLISH", "confidence": 95.0, "strength": 62.0 }],
      "recent_pattern_count": 21,
      "retracement_direction": "UP",
      "retracement_percentage": 3.0,
      "retracement_zone": "SHALLOW",
      "nearest_support": { "label": "Breakout level", "price": 1852.44, "strength": 62.0, "distance_atr": -0.01 },
      "nearest_resistance": { "label": "Day high", "price": 1853.33, "strength": 62.0, "distance_atr": 0.2 },
      "overall_bias": "BULLISH",
      "bias_score": 64.2,
      "alignment_score": 100.0,
      "setup_state": "WITH_TREND",
      "entry_confirmations": 2
    }
  ],
  "status": { "is_running": true, "context_timeframe": "60m", "setup_timeframe": "15m", "entry_timeframe": "5m", "refresh_seconds": 15.0, "refresh_count": 3, "last_refresh_at": "...", "last_compute_ms": 870.4, "last_error": null, "symbols_analyzed": 51 },
  "note": "Price action patterns are contextual features, not trade signals. ..."
}
```

### `GET /market/price-action/{symbol}`

`{ "symbol", "name", "analysis", "candles" }` where `candles` are the
latest 150 closed entry-timeframe candles the analysis used, and
`analysis` contains:

- `patterns`: every detector (COG, NR, NRB, WRB, BT, TT) on the latest
  closed candle, detected or not:

  ```json
  {
    "pattern": "BT",
    "name": "Bottoming Tail",
    "detected": true,
    "direction": "BULLISH",
    "confidence": 75.0,
    "strength": 81.4,
    "context": {
      "prior_trend": "DOWN",
      "prior_move_atr": -3.2,
      "atr": 1.12,
      "range_vs_average": 1.6,
      "relative_volume": 1.1,
      "location": "AT_SUPPORT",
      "nearest_level": { "label": "Prev day low", "price": 98.95, "distance_atr": 0.0 },
      "levels_considered": true
    },
    "reasons": ["Lower tail 88% of the range: lower prices rejected", "Formed at support (Prev day low 98.95)", "Follows a decline (-3.2 ATR)"],
    "metrics": { "tail_ratio": 0.875, "close_position": 0.97 },
    "candle_index": 299,
    "timestamp": "..."
  }
  ```

  `location` is `AT_SUPPORT`, `AT_RESISTANCE`, `THROUGH_LEVEL` (the
  candle's body crossed the level, e.g. a breakout candle),
  `BETWEEN_LEVELS` or `NO_LEVELS`.
- `recent_patterns`: patterns detected on the last 60 candles (for chart
  markers), evaluated without today's levels to avoid look-ahead.
- `swing_structure`: significant ATR zig-zag pivots and legs, plus the
  Phase 4 HH/HL/LH/LL structure.
- `retracement`: `detected`, `direction` (of the impulse), `swing_high`,
  `swing_low`, `impulse_size_atr`, `retracement_percentage` (now),
  `max_retracement_percentage` (deepest since the impulse extreme),
  `zone` (`SHALLOW`, `HEALTHY_PULLBACK`, `DEEP_PULLBACK`, `WEAKENING`,
  `MAJOR_WEAKNESS`, `INVALIDATION_CONTEXT`), `quality`, `levels`
  (40/50/60/80/100% prices), `strength`, `confidence`, `reasons`.
- `support_resistance`: `levels` (highest first), each with `label`,
  `type`, `price`, `role` (`SUPPORT`/`RESISTANCE`), `distance_atr`,
  `strength` (Level Strength Score 0-100), `touch_count`, `timeframe`
  (`1d` for day levels), `recency` (candles since the last touch),
  `confluence` and `components` (score points by component); plus
  `nearest_support`/`nearest_resistance` and
  `strongest_support`/`strongest_resistance`.
- `multi_timeframe`: `overall_bias`, `bias_score` (-100..+100),
  `alignment_score` (0-100), `context_analysis`, `setup_analysis`,
  `entry_analysis`, `conflicts`, `reasons`. Each timeframe analysis has
  its bias, regime, structure, momentum and role-specific detail:
  `key_levels` (context), `retracement` + `setup_state` + nearest levels
  (setup), detected `patterns` + `confirmation` (entry: candle pattern,
  momentum and volume agreeing with the higher-timeframe bias;
  descriptive only).

`404` for a symbol not on the watchlist.

### `GET /market/price-action/config`

Every threshold and weight in use (`patterns`, `swings`, `retracement`,
`levels` including Level Strength Score weights, `multi_timeframe`).

---

## Strategies

Read-only, no authentication required. Strategy **candidates** computed
from simulated data after each price action refresh. Candidates are
hypotheses, not orders: they are not sized, not risk-checked and never
sent to a broker. No endpoint returns order, quantity or broker fields.

All endpoints return `503` if the service is not running.

### `GET /strategies`

One row per watched symbol: `regime`, `regime_confidence`,
`eligible_strategies` (what the router allows), `candidates` (eligible
results: `strategy`, `direction`, `entry_candidate`, `stop_candidate`,
`target_candidate`, `reward_risk`, `confidence`) and `stages` (how far
each routed strategy's concept got), plus service `status`.

### `GET /strategies/{symbol}?include_unrouted=false`

`evaluation` holds the router `decision` (regime, eligible strategies,
one entry per strategy with the reason it was or was not routed and the
allowed directions) and one `StrategyResult` per routed strategy:

```json
{
  "strategy": "TREND_CONTINUATION",
  "eligible": true,
  "direction": "LONG",
  "entry_candidate": 102.4,
  "stop_candidate": 99.8,
  "target_candidate": 110.0,
  "risk_per_unit": 2.6,
  "reward_per_unit": 7.6,
  "reward_risk": 2.92,
  "entry_basis": "above the confirmation candle's high",
  "stop_basis": "below the pullback low",
  "target_basis": "Swing high (structure)",
  "confidence": 100.0,
  "setup_score": 100.0,
  "stage": "ENTRY_CANDIDATE",
  "checks": [{ "key": "pullback", "label": "Pullback", "passed": true, "required": true, "weight": 0.0, "detail": "pulled back 50% (healthy range 30-65%), now 40%" }],
  "reasons": ["Trend: up from regime", "..."],
  "blockers": [],
  "features": { "pullback_extreme": 100.0, "impulse_size_atr": 20.0, "...": "..." },
  "note": "Strategy candidates are hypotheses, not orders. ..."
}
```

When not eligible, the candidates are `null`, `confidence` is 0 and
`blockers` says why; `setup_score` still shows how much of the setup is
present. `include_unrouted=true` adds `unrouted`: every enabled
strategy evaluated regardless of routing (research view).

### `GET /strategies/route?regime=RANGE&confidence=70`

The router decision for any regime and confidence.

### `GET /strategies/config`

Router mapping, minimum regime confidence, and every strategy's
thresholds, weights and candidate geometry.

---

## Scanner and signals

Read-only, no authentication required. Computed from simulated data
after every strategy refresh. Signals are scored strategy candidates,
not orders; scores and ranks describe how much of each strategy's
weighted evidence is present and are not proof of profitability.

### `GET /scanner`

`{ "scan": ScanResult, "status": SignalServiceStatus }`

- `scan.market_context`: NIFTY 50 regime, multi-timeframe bias and
  direction, price and change.
- `scan.rows`: one per constituent (50), ranked rows first. Each has
  `rank`, `symbol`, `price`, `change_percent`, `status` (`READY`,
  `WATCH`, `IGNORE`, `NO_SETUP`, `NO_ROUTE`, `NO_DATA`), the best
  signal's `signal_id` / `signal_direction` / `signal_strategy` /
  `score` / `category`, and the analysis: `regime`, 60m / 15m / 5m
  directions and biases, `alignment_score`, `setup_state`, structure,
  `price_vs_vwap`, `trend_quality`, retracement, `nearest_support` /
  `nearest_resistance`, `patterns`, `relative_volume`,
  `momentum_score`, `relative_strength` (+ rank) and routed
  `strategies` with their furthest `strategy_stages`.
- `scan.candidates`: every scored candidate as a ranked
  `TradeCandidate` (rank, symbol, direction, strategy, score, category,
  status, entry, stop_loss, target, risk_reward, quantity_candidate and
  the ranking metrics); `scan.top_candidates`: the first
  `SCANNER_TOP_N` that are WATCH or better.
- `scan.signals`: the full `TradingSignal` for each candidate in this
  scan (with `id` when stored), so details are available even when a
  signal is not stored.
- `status`: scan count and time, persistence state, executable threshold.

### `GET /signals`

Stored signals, newest first. Query: `status` (`ACTIVE` / `EXPIRED`),
`category`, `symbol`, `strategy`, `executable`, `limit` (max 200),
`offset`. Returns `{ items: StoredSignal[], total, limit, offset, note }`
where `StoredSignal` = `{ signal, generated_at, last_seen_at,
expired_at, last_score, last_rank, times_seen }`.

### `GET /signals/{id}`

One `StoredSignal`. The `signal` holds every field of the signal object:

```json
{
  "id": "4edbeb3f-...",
  "symbol": "ITC",
  "direction": "BUY",
  "strategy": "TREND_CONTINUATION",
  "strategy_version": "1.0.0+567cf21b",
  "entry": 262.83,
  "stop_loss": 261.73,
  "target": 265.02,
  "risk_reward": 2.0,
  "quantity_candidate": 190,
  "quantity_note": "190 shares: risk 1% of 100,000 at 1.10/share, capped at 50,000 position value ... Candidate only",
  "score": 58.0,
  "category": "IGNORE",
  "executable": false,
  "confidence": 70.0,
  "market_regime": "STRONG_BULLISH",
  "breakdown": {
    "total": 58.0,
    "category": "IGNORE",
    "executable": false,
    "executable_threshold": 85.0,
    "components": [{ "key": "relative_strength", "label": "Relative strength", "weight": 15.0, "applicable": true, "effective_weight": 15.8, "score": 0.94, "points": 14.9, "passed": true, "detail": "RS score 78, rank 23/50" }]
  },
  "reasons": ["✗ Market alignment: ...", "✓ Relative strength: RS score 78, rank 23/50 (14.9/15.8)", "...", "Final score 58 / 100 (IGNORE)"],
  "strategy_checks": [{ "key": "pullback", "label": "Pullback", "passed": true, "required": true, "weight": 0, "detail": "pulled back 48% ..." }],
  "candle_time": "2026-10-02T00:50:00Z",
  "timestamp": "2026-10-02T00:56:18Z",
  "status": "ACTIVE"
}
```

`404` for an unknown id, `422` for a malformed one, `503` if signal
storage is unavailable.

### `GET /signals/config`

Scoring weights, category bands, executable threshold, quantity
defaults, ranking settings and the storage score.


---

## Paper trading and risk (Phase 8)

PAPER only. Every order goes to the simulated `PaperBrokerAdapter`; no
broker is connected and no real order can be placed. If `TRADING_MODE`
is anything other than `PAPER`, every risk check fails with
`TRADING_MODE_NOT_PAPER`. Automatic trading is off at every start.

Read endpoints need no authentication. Action endpoints need a
`TRADER` or `ADMIN` bearer token (401 without one, 403 for `VIEWER`), and
each action is written to the audit log.

### `GET /trading/status`

`TradingStatus`: `mode` (`PAPER`), `state` (`IDLE`, `SCANNING`,
`SIGNAL_FOUND`, `RISK_CHECK`, `ORDER_PENDING`, `POSITION_ACTIVE`, `EXIT`,
`CLOSED`) and `state_since`, `auto_trading`, `emergency_stop` /
`emergency_reason`, `active_trade` (a `PaperTrade` or null),
`last_decision` (the last `RiskDecision`), `balance`, `positions`,
`working_orders`, `risk` (`RiskSnapshot`), `system_healthy` /
`health_detail`, `persistence_ok` / `persistence_error`,
`restored_trades_today`, `interrupted_trades`, `broker` (the paper
broker assumptions), `entry_timeout_seconds`, `recent_transitions`, and
(Phase 9) the session: `market_open`, `entries_allowed`, `market_detail`,
`session_mode` (`EXCHANGE` / `SIMULATED_24H`).

### `GET /trading/risk`

`{ limits: RiskLimits, snapshot: RiskSnapshot, last_decision, note }`.
`snapshot`: today's `state` (trading day, day-start capital, trades,
realized net P&L, consecutive losses, wins, losses), `capital`,
`available_capital`, `open_exposure`, `daily_loss_limit_amount`,
`daily_loss_remaining`, `risk_per_trade_amount`, `trades_remaining`,
`consecutive_losses_remaining`, `entries_blocked`, `blocked_reasons`.

A `RiskDecision` is `{ approved, intent, size, checks, rejection_reasons,
evaluated_at, note }`. `size` (`PositionSize`): `quantity`,
`raw_quantity`, `risk_amount`, `risk_per_unit`, `actual_risk`,
`position_value`, tick-rounded `entry` and `stop_loss`, and the
`constraints` that reduced the quantity. `checks` lists every check with
`passed`, `detail` and, when failed, its `reason`:

| reason | when |
|---|---|
| `TRADING_MODE_NOT_PAPER` | mode is not PAPER |
| `EMERGENCY_STOP` | emergency stop active |
| `SYSTEM_UNHEALTHY` | market data feed, scanner or trade storage unhealthy |
| `MARKET_CLOSED` | outside the session, weekend, configured holiday, or after the last entry time |
| `DATA_STALE` | no price / price older than `RISK_MAX_DATA_AGE_SECONDS` / signal expired or not confirmed within `RISK_MAX_SIGNAL_AGE_SECONDS` |
| `ACTIVE_POSITION_EXISTS` | a trade is already between signal and exit |
| `DUPLICATE_ORDER` | the signal was already sent today, or an order for the symbol is open |
| `DAILY_LOSS_LIMIT` | today's realized loss reached `MAX_DAILY_LOSS` % |
| `MAX_TRADES_REACHED` | `MAX_TRADES_PER_DAY` entries taken |
| `MAX_CONSECUTIVE_LOSSES` | `RISK_MAX_CONSECUTIVE_LOSSES` losses in a row |
| `SIGNAL_NOT_EXECUTABLE` | score below `SIGNAL_EXECUTABLE_THRESHOLD` |
| `SHORT_NOT_ALLOWED` | short side with `RISK_ALLOW_SHORT=false` |
| `INVALID_STOP` | stop not on the loss side of entry and price by a tick |
| `INVALID_TARGET` | target not on the profit side of price |
| `PRICE_MOVED_AWAY` | price moved more than `RISK_MAX_ENTRY_DEVIATION_R` x stop distance against the entry |
| `INVALID_QUANTITY` | sized quantity below one lot |
| `INSUFFICIENT_CAPITAL` | position value / margin multiplier + estimated charges exceeds available capital |
| `MAX_EXPOSURE` | open exposure + this trade exceeds `RISK_MAX_EXPOSURE` |

### `GET /trading/positions`

`{ positions: Position[], balance: Balance, note }`. A `Position` has
signed `quantity`, `average_price`, `last_price`, `realized_pnl`,
`unrealized_pnl`, `charges`. `Balance`: `starting_capital`, `cash`,
`used_margin`, `available`, `realized_pnl` (gross), `unrealized_pnl`,
`charges_paid`, `equity`.

### `GET /trading/orders?trade_id=&limit=&offset=`

Stored paper orders, newest first: `{ items: [{ order, trade_id, purpose }],
total, limit, offset, note }`. `purpose` is `ENTRY`, `STOP_LOSS`,
`TARGET` or `EXIT`. An `Order` has `order_type` (`MARKET`, `LIMIT`,
`SL_M`), `quantity`, `price`, `trigger_price`, `status` (`OPEN`,
`TRIGGER_PENDING`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`,
`REJECTED`), `filled_quantity`, `average_price`, `fills` (each with
`price`, `market_price`, `slippage`, `charges`, `timestamp`), `charges`
(brokerage, STT, exchange, SEBI, stamp duty, GST) and
`rejection_reason`.

### `GET /trading/trades?status=&limit=&offset=` and `GET /trading/trades/{id}`

Stored paper trades, newest first. A `PaperTrade`: `symbol`, `side`
(`LONG`/`SHORT`), `strategy`, `score`, `status` (`PENDING`, `OPEN`,
`EXITING`, `CLOSED`, `CANCELLED`, `REJECTED`, `INTERRUPTED`),
`planned_entry`, `stop_loss`, `target`, `quantity` (approved),
`filled_quantity`, `entry_price` / `exit_price` (averages),
`initial_risk`, `gross_pnl`, `charges`, `net_pnl`, `r_multiple`,
`exit_reason` (`STOP_LOSS`, `TARGET`, `TIME_EXIT`, `MANUAL`, `EMERGENCY`,
`ENTRY_EXPIRED`, `ENTRY_REJECTED`), timestamps, `order_ids` by purpose,
and the `risk_summary` / `risk_checks` that approved it.

### `POST /trading/signals/{signal_id}/execute` (TRADER / ADMIN)

Runs a stored signal through the risk check and, if approved, places a
PAPER market entry. Returns `{ decision: RiskDecision, status:
TradingStatus }` with 200 whether approved or not — a rejection is a
normal result listing every failed check. 404 if the signal does not
exist.

### `POST /trading/auto` (TRADER / ADMIN)

Body `{ "enabled": true|false }`. With automatic trading on, the
best-ranked READY signal not yet tried today is executed after each
scan, one trade at a time. 409 while the emergency stop is active.

### `POST /trading/exit` (TRADER / ADMIN)

Closes the active trade at market (or cancels an unfilled entry). 409
when there is no active trade or an exit is already working.

### `POST /trading/emergency-stop` (TRADER / ADMIN)

Body (optional) `{ "reason": "...", "close_position": false }`. Disables
new trading, turns automatic trading off and cancels pending entry
orders. An open position is **not** closed unless `close_position` is
true (or `PAPER_EMERGENCY_CLOSE_POSITIONS=true` and the field is
omitted); it keeps its protective stop and target.

### `POST /trading/emergency-stop/reset` (TRADER / ADMIN)

Allows new trading again; automatic trading stays off. 409 while an
exit order is working.


---

## Dashboard support (Phase 9)

Read-only, no authentication required.

### `GET /analytics/performance`

Computed from stored CLOSED paper trades with a fill (up to 5,000), all
P&L net of charges:

- `summary`: `closed_trades`, `wins`, `losses`, `breakeven`, `win_rate`
  (%), `net_pnl`, `gross_profit`, `gross_loss`, `profit_factor` (gross
  profit / |gross loss|; `null` when there are no losing trades),
  `charges`, `average_win`, `average_loss`, `expectancy` (net per trade),
  `average_r`, `best_trade`, `worst_trade`, `max_drawdown` (rupees, <= 0),
  `max_drawdown_percent`, `longest_win_streak`, `longest_loss_streak`,
  `starting_capital` (`PAPER_CAPITAL`), `ending_equity`, `return_percent`.
- `equity_curve`: per trade in closing order — `trade_id`, `symbol`,
  `strategy`, `closed_at`, `net_pnl`, `r_multiple`, `cumulative_pnl`,
  `equity`, `drawdown`, `drawdown_percent` (below the running peak).
- `daily`: per IST trading day — `date`, `net_pnl`, `trades`, `wins`,
  `losses`.
- `by_strategy`, `by_exit_reason`: `{ key, trades, wins, losses,
  win_rate, net_pnl, gross_profit, gross_loss, profit_factor, average_r }`.
- `other_trades`: counts of trades not in the statistics by status
  (open, cancelled, rejected, interrupted).

Paper results on simulated data; not a forecast and not evidence of
profitability.

### `GET /system/components`

`{ status, components, demo_mode, trading_mode, checked_at, note }`.
`status` is the worst component level. Each component is `{ key, name,
status: HEALTHY | WARNING | CRITICAL, detail, metrics }`:

| key | HEALTHY | WARNING | CRITICAL |
|---|---|---|---|
| `market_data` | feed running, provider connected, tick < 10 s old | tick 10–60 s old, no tick yet, or simulated feed with `DEMO_MODE=false` | not running, provider not connected, tick > 60 s old |
| `backend` | answering | — | (unreachable: the request fails) |
| `database` | PostgreSQL reachable | — | unreachable |
| `redis` | reachable | unreachable (not on the trading path yet) | — |
| `websocket` | `/ws/market` broadcasting (`connected_clients`) | — | market data feed not running |
| `strategy_engine` | intelligence, price action, strategies and signals refreshing on time | a service errored or is > 3 refresh intervals late | a service is not running |
| `risk_engine` | evaluating orders | emergency stop active, or a daily risk limit reached | trading service not running |
| `paper_broker` | simulating fills, trades stored | system unhealthy for new entries | loop not running, or trades cannot be stored |

### `GET /system/events?limit=30&severity=`

Recent system events, newest first: `{ items: [{ id, event_type,
severity, message, metadata, created_at }], total }`. Paper trading
events are prefixed `PAPER_` (`PAPER_ORDER_PLACED`,
`PAPER_POSITION_OPENED`, `PAPER_TRADE_CLOSED`, `PAPER_RISK_REJECTED`,
`PAPER_EMERGENCY_STOP`, ...). `severity` filters to `INFO`, `WARNING` or
`CRITICAL`.

### WebSocket: `trading_event`

Every paper trading event is also pushed on `WS /ws/market` after it is
stored (example shape):

```json
{
  "type": "trading_event",
  "event_type": "TRADE_CLOSED",
  "severity": "INFO",
  "message": "LONG 40 RELIANCE: TARGET, net P&L ₹212.40 (+0.85R)",
  "at": "2026-10-02T05:46:11.204+00:00",
  "metadata": { "trade_id": "…", "net_pnl": 212.4, "exit_reason": "TARGET" }
}
```

The dashboard refreshes its trading, analytics and system queries when
one arrives.

## Backtesting (Phase 10)

Reads need no authentication; creating, cancelling and deleting runs
need the TRADER or ADMIN role. Runs execute one at a time in the
background on the server. Methodology and assumptions:
[`docs/backtesting.md`](./backtesting.md). Every response that carries
results also carries `note`: results describe past (or synthetic) data
under the stated assumptions and do not predict or guarantee future
performance.

### `GET /backtests/options`

Everything the form needs: `strategies` (`ALL`, `TREND_CONTINUATION`,
`BREAKOUT_MOMENTUM`, `MEAN_REVERSION`, each with `label`, `parameters`
— `{ key, label, scope: strategy | execution, kind: float | int | choice,
default, minimum, maximum, step, choices, description }` — and
`defaults`), `versions` (the current default version of each choice plus
versions stored by earlier runs, with the strategy parameters that
produce them), `data_sources` (`synthetic` always; `csv` when
`BACKTEST_DATA_DIR` is set, with its date range, symbols and whether the
universe is point-in-time), `timeframes` (`5m`, `15m` entry timeframes),
`modes`, `limits` (`max_symbols` 20, `max_days` 250,
`max_grid_combinations` 27), default `risk`, `costs` and `validation`,
`assumptions` and `note`.

### `POST /backtests` (TRADER / ADMIN) → 202

```json
{
  "name": "TC out-of-sample",
  "mode": "OUT_OF_SAMPLE",
  "strategy": "TREND_CONTINUATION",
  "version": "1.0.0+567cf21b",
  "symbols": ["RELIANCE", "HDFCBANK", "INFY"],
  "start_date": "2026-06-01",
  "end_date": "2026-08-28",
  "timeframe": "5m",
  "parameters": { "min_score": 65 },
  "grid": { "min_score": [55, 65, 75], "trailing_mode": ["NONE", "BREAKEVEN"] },
  "capital": 100000,
  "risk": { "risk_per_trade_percent": 1, "daily_loss_limit_percent": 3, "max_trades_per_day": 5 },
  "costs": { "slippage_bps": 5 },
  "validation": { "oos_fraction": 0.3, "min_trades_for_selection": 8, "null_control": false },
  "data_source": { "kind": "synthetic", "seed": 42 }
}
```

Only `symbols`, `start_date` and `end_date` are required. `version`, if
given, must be one the parameters actually produce. Checked before the
run is queued (422 with a reason): dates inside the data's range, at
least 3 trading days (6 for out-of-sample; train + test for
walk-forward), known symbols, parameters inside their ranges, a grid of
at most 27 combinations, and a grid for walk-forward. Returns the run
summary with `status: QUEUED` and `estimated_seconds`.

Modes: `HISTORICAL` (your parameters over the range; a grid only yields
a sensitivity table), `OUT_OF_SAMPLE` (time-ordered split; selection on
the in-sample part; headline = the out-of-sample part), `WALK_FORWARD`
(`wf_train_days` / `wf_test_days`, rolling or `wf_anchored`; headline =
stitched test windows).

### `GET /backtests?limit=50&offset=0`

`{ items: [RunSummary], total, queued, note }`, newest first.
`RunSummary`: `id, name, mode, status (QUEUED | RUNNING | COMPLETED |
FAILED | CANCELLED), progress (0–1), message, error, strategy, timeframe,
start_date, end_date, symbols, capital, data_source { kind, synthetic,
fingerprint, seed }, headline { total_trades, win_rate, profit_factor,
net_pnl, max_drawdown, expectancy_r } | null, flags (count of robustness
flags), created_at, finished_at, duration_ms`.

### `GET /backtests/{id}`

The summary plus `request` (as submitted), `metrics`, `robustness` and
`result`:

- `metrics`: `total_trades, wins, losses, win_rate (%), profit_factor,
  expectancy (₹/trade), expectancy_r, net_pnl, gross_profit, gross_loss,
  charges, return_percent, max_drawdown, max_drawdown_percent,
  average_win, average_loss, payoff_ratio (realised risk/reward),
  average_planned_rr, max_consecutive_wins, max_consecutive_losses,
  sharpe, sortino (null below 20 trading days or without variation),
  sharpe_note, trading_days, trades_per_day, average_holding_minutes,
  exit_reasons, starting_capital, ending_equity`.
- `robustness`: `trades, enough_trades (30+), mean_r, r_std, r_ci95,
  t_stat, mc_net_pnl / mc_max_drawdown ({ p5, p50, p95 } over 1,000
  seeded bootstrap resamples), mc_probability_of_loss (0–1),
  net_pnl_double_costs, net_pnl_extra_slippage, extra_slippage_bps,
  top3_share_of_profit (0–1), net_without_best_trade, by_regime,
  by_strategy, by_exit_reason, flags` (plain-English warnings).
- `result`: `mode, primary_label` (what the headline describes),
  `segments` (`FULL`, `IN_SAMPLE` / `OUT_OF_SAMPLE`, or `TRAIN` / `TEST`
  per window, each with its dates, parameters, objective and metrics),
  `grid` (`varied`, `trades`, `net_pnl`, `profit_factor`,
  `expectancy_r`, `objective`, `smoothed`, `selected`, `window`),
  `combinations_tested, selected_params, selected_params_hash,
  walk_forward { windows, profitable_windows, efficiency,
  parameter_changes, note }, in_sample_metrics, degradation (OOS ÷ IS
  expectancy R), null_control` (metrics on no-edge data, if asked),
  `strategy_versions, data, replay, execution, warnings, assumptions,
  note`.

### `GET /backtests/{id}/trades?limit=500&offset=0`

`{ items, total }` in closing order. Each trade: `id, segment, symbol,
side, strategy, strategy_version, regime, score, decision_time,
opened_at, closed_at, quantity, entry_price, exit_price, stop_loss,
trailing_stop, target, exit_reason (STOP_LOSS | TARGET | TRAILING_STOP |
TIME_EXIT | …), gross_pnl, charges, net_pnl, r_multiple, planned_rr,
reasons`.

### `GET /backtests/{id}/equity`

`{ trades: [...], days: [...] }` — equity after each closed trade (with
`drawdown`, `drawdown_percent`, `net_pnl`) and at the end of each
trading day; points are `{ at, equity, drawdown, drawdown_percent,
net_pnl }`.

### `POST /backtests/{id}/cancel` (TRADER / ADMIN)

Cancels a queued or running run (409 otherwise). A running run stops once
the symbols being replayed finish, or before its next simulation pass.

### `DELETE /backtests/{id}` (TRADER / ADMIN) → 204

Deletes a finished run with its trades and equity (409 while it is
queued or running). Strategy versions and parameter sets stay.

## Broker (Phase 11)

PAPER mode: orders execute on the paper broker. Zerodha, when configured,
is connected read-only; **no endpoint places, modifies or cancels a live
order**. Details: [`docs/broker.md`](./broker.md). No response ever
contains the API secret or access token.

### `GET /broker/status`

`{ execution_broker: "paper", trading_mode, live_trading_possible: false,
live_blockers: [reasons], zerodha: { configured, state (NOT_CONFIGURED |
DISCONNECTED | CONNECTING | CONNECTED | DEGRADED | AUTH_REQUIRED), healthy,
detail, checked_at, latency_ms, last_success_at, last_error,
consecutive_failures, session_expires_at, live_orders_armed,
market_protection, orders_per_second, supervisor_running, paused_by_user,
supervisor_error }, order_manager: { tracked, working, unresolved,
external_orders, resync_pending, last_reconcile_at, last_reconcile_ok,
last_reconcile_error }, note }`. No authentication required.

### `GET /broker/events?limit=50`

Recent order and broker events, newest first: `{ items: [{ kind,
severity, message, at, order_id, client_order_id, broker_order_id,
symbol, status, filled_delta, metadata }] }`. Kinds: `ORDER_SENT`,
`ORDER_BLOCKED`, `ORDER_ACCEPTED`, `ORDER_PARTIAL_FILL`, `ORDER_FILLED`,
`ORDER_REJECTED`, `ORDER_CANCELLED`, `ORDER_MODIFIED`, `ORDER_UNKNOWN`,
`ORDER_RECOVERED`, `ORDER_LOST`, `ORDER_REAPPEARED`, `ORDER_MISSING`,
`EXTERNAL_ORDER`, `CANCEL_FAILED`, `BROKER_DISCONNECTED`,
`BROKER_CONNECTED`. They are also stored as system events with a
`BROKER_` prefix.

### `POST /broker/reconcile` (TRADER / ADMIN)

Reconciles tracked orders with the Zerodha order book now: `{ ok, at,
checked, changed, recovered, lost, external, error }`. 409 when Zerodha
is not configured.

### `POST /broker/connect`, `POST /broker/disconnect` (ADMIN)

(Re)connect and re-check health, or stop using the session (no automatic
reconnect until `connect`). Both return the status above. `connect` is
409 when not configured.

### `GET /broker/zerodha/login-url` (ADMIN)

`{ login_url }` — the Kite login page for the configured API key.

### `POST /broker/zerodha/session` (ADMIN)

`{ "request_token": "…" }` from the login redirect. Exchanges it
(`checksum = sha256(api_key + request_token + api_secret)`), keeps the
access token in memory only, connects, and returns `{ profile, status }`
with a masked user id.

### `GET /broker/zerodha/{profile,balance,positions,orders}` (TRADER / ADMIN)

Read-only account data mapped to the broker-neutral models (`Balance`,
`Position`, `Order`). Errors: 409 not configured or login required, 429
rate limited, 503 unreachable, 502 refused by Zerodha.

The system components (`GET /system/components`) gain `live_broker`:
HEALTHY when not configured (PAPER needs no live broker) or connected;
WARNING when configured but unusable or orders are being reconciled;
CRITICAL only if live trading were enabled.


## Approvals (Phase 12)

APPROVAL mode: `ALGO -> SIGNAL -> SCORE -> RISK CHECK -> PENDING APPROVAL
-> USER REVIEWS -> APPROVE / REJECT -> ORDER`. Approving places a PAPER
order only. Details: [`docs/approval-mode.md`](./approval-mode.md).

### `POST /trading/mode` (TRADER / ADMIN)

`{ "mode": "PAPER" | "APPROVAL" }` → `TradingStatus`. Audited as
`TRADING_MODE_CHANGED`. `LIVE` → 422. Leaving APPROVAL cancels open
requests. `GET /trading/status` reports `mode`.

### `GET /approvals?status=&limit=100&offset=0`

`{ items, total, counts: {STATUS: n}, mode, ttl_seconds, server_time,
note }`, newest first; `status` may repeat. Each item: `id, status
(PENDING | APPROVED | REJECTED | EXPIRED | CANCELLED | EXECUTED | FAILED),
source (ALGO | MANUAL), signal_id, symbol, direction, side, entry,
stop_loss, target, quantity, risk_amount, risk_per_share, position_value,
reward_risk, strategy, strategy_version, score, category, market_regime,
regime_confidence, reasons, risk_checks, signal_timestamp, created_at,
expires_at, requested_by_email, approved_by, approved_by_email,
approved_at, rejected_by, rejected_by_email, rejected_at, expired_at,
executed_at, reason, note, trade_id, execution_reasons,
seconds_remaining`. No authentication required to read.

### `GET /approvals/{id}`

One request (404 if unknown).

### `POST /approvals` (TRADER / ADMIN)

`{ "signal_id": "…" }`: runs the risk check as a preview and creates a
PENDING request (201), or returns the open one for that signal. 409 when
not in APPROVAL mode or the signal is not ACTIVE; 404 unknown signal; 422
`{message, reasons}` when the risk check refuses.

### `POST /approvals/{id}/approve` (TRADER / ADMIN)

Optional `{ "note": "…" }`. Re-runs the risk check (approved quantity as
the cap) and places the paper entry: returns the request as EXECUTED (with
`trade_id`) or FAILED (with `reason`, `execution_reasons`). 409 when it is
not PENDING, has expired (it becomes EXPIRED), its signal expired, the
mode is not APPROVAL, a paper trade is already open (it stays PENDING),
or someone else decided first.

### `POST /approvals/{id}/reject` (TRADER / ADMIN)

`{ "reason": "…" }` (3–500 characters, required) → REJECTED with
`rejected_by`, `rejected_at`, `reason`. 409 when not PENDING.

Every change is written to `audit_logs` (`APPROVAL_*`) and
`system_events`, and pushed as a `trading_event` on `WS /ws/market`.

## Market data & Kite login (Phase 13)

No response contains the API secret, an access token or a request token.

### `GET /broker/zerodha/status`
Safe status: `{broker, configured, connected, auth, user_id (masked), user_name,
login_time, expires_at, last_error, redirect_url_configured, market_data
(CONNECTED | STALE | DISCONNECTED | SIMULATED), execution_mode (PAPER)}`.

### `GET /broker/zerodha/login` (ADMIN)
`{login_url}` — the Kite login URL with a one-time `state` (10 min). `?redirect=true`
answers 307 to it instead.

### `GET /broker/zerodha/callback`
Kite's redirect target (`KITE_REDIRECT_URL`). Validates `state`, exchanges
`request_token` server-side, then 303 to `FRONTEND_URL/settings?zerodha=connected`
or `?zerodha=error&reason=state|login|token_exchange|not_configured`.

### `POST /broker/zerodha/logout` (ADMIN)
Invalidates the Kite session and forgets the token; returns the safe status.

### `GET /broker/reconciliation`, `POST /broker/reconciliation` (TRADER / ADMIN)
Last / new read-only comparison of local and broker orders, trades and positions:
`{result: {ok, at, orders_compared, positions_compared, trades_compared,
mismatches: [{kind, symbol, order_id, broker_order_id, local, broker, detail}]}}`.

### `GET /system/market-data`
`{provider, mode (SIMULATED | ZERODHA_LIVE), simulated, status (LIVE | STALE |
WAITING | DISCONNECTED), websocket, feed_state, rest_api, last_tick,
last_tick_age_seconds, latency_ms, subscribed_instruments, unresolved_symbols,
stale, stale_threshold_seconds, ticks_accepted, ticks_rejected,
rejected_by_issue, warmup_in_progress, last_warmup_at, candles {5m,15m,60m}}`.

### `GET /system/broker-health`
`{status (HEALTHY | DEGRADED | DISCONNECTED | AUTH_REQUIRED | ERROR), configured,
market_data_source, execution_mode, checked_at, checks: [{name, ok, status, detail}]}`.

`GET /market/status` additionally reports `source`, `data_source` and `data_status`;
backtests accept `data_source.kind = "zerodha"`.
