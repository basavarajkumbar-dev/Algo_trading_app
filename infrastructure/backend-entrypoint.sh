#!/bin/sh
# GAINZALGO PRO MAX — backend container entrypoint.
#
# Runs Alembic migrations before starting the API so `docker compose up
# --build` always leaves the database schema up to date, without
# requiring a separate manual `make migrate` step for a fresh checkout.
# Migrations are idempotent (alembic upgrade head is a no-op if already
# current), so this is safe to run on every container start, including
# restarts.
set -e

echo "Waiting for the database to accept connections..."
python -c "
import asyncio
import sys
from app.core.config import get_settings
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text

async def wait():
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    for attempt in range(30):
        try:
            async with engine.connect() as conn:
                await conn.execute(text('SELECT 1'))
            await engine.dispose()
            return
        except Exception:
            await asyncio.sleep(1)
    print('Database did not become reachable in time.', file=sys.stderr)
    sys.exit(1)

asyncio.run(wait())
"

echo "Running database migrations..."
alembic upgrade head

echo "Starting API server..."
# Auto-reload only in development; production runs without it behind a
# TLS-terminating proxy (see docker-compose.prod.yml).
if [ "${UVICORN_RELOAD:-true}" = "true" ]; then
  exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
fi
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips="${FORWARDED_ALLOW_IPS:-127.0.0.1}" --workers 1
