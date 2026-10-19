# Backend deployment

Development: `docker compose up --build` (auto-reload, open API, placeholder secrets).

Production: `docker-compose.prod.yml` — backend (no reload), Postgres and
Redis (not published), and Caddy terminating TLS with an automatic
certificate. Nothing was deployed by this upgrade; choose and pay for a host
yourself.

```
cp .env.example .env.production     # on the server only; never commit it
# set APP_ENV=production, long random APP_SECRET and JWT_SECRET (≥32 chars),
# POSTGRES_USER / POSTGRES_PASSWORD, KITE_* if used, KITE_REDIRECT_URL=https://<domain>/api/v1/broker/zerodha/callback
DOMAIN=api.example.com docker compose -f docker-compose.prod.yml --env-file .env.production up -d --build
```

- Health: `GET /api/v1/system/health` (used by the container health check).
- With `APP_ENV=production` or `staging` the backend **refuses to start** with
  placeholder secrets, and every data endpoint requires a signed-in user
  (WebSocket: first-message token, never in the URL).
- Trading stays PAPER. Live orders additionally need
  `EXECUTION_MODE=ZERODHA`, `LIVE_TRADING_ENABLED=true`, a daily Kite login,
  an admin arming live trading with typed confirmation + password, every
  pre-trade check passing, and a static IP registered with Zerodha.
- Self-service sign-up is **closed** in production/staging (`ALLOW_REGISTRATION`).
  Create accounts on the server; the password is read from the terminal:
  `docker compose -f docker-compose.prod.yml exec backend python -m app.scripts.create_user --email you@example.com --role ADMIN`
- Database: `alembic upgrade head` runs on start. This upgrade added no schema
  migration (new fields live in existing JSON payloads).
