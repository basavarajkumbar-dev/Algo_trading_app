# GAINZALGO PRO MAX — API Reference

Covers the API surface that exists through Phase 2. Interactive,
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

Then, as the simulated market moves:

- `{"type": "market_tick", "symbol": "...", "price": ..., "volume": ..., "timestamp": "..."}`
- `{"type": "candle_closed", "symbol": "...", "timeframe": "...", "timestamp": "...", "open": ..., "high": ..., "low": ..., "close": ..., "volume": ...}`

The endpoint is broadcast-only in this phase — there is no per-client
symbol filtering yet, and any inbound client message is ignored (it is
still read, so the server notices disconnects promptly).
