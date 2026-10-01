"""Seed script: creates the demo user and its default settings rows.

Usage:
    cd backend && python -m app.seed

The demo user's credentials are deliberately not secret — this is a
seed for local/demo use only, never for a production credential. Never
reuse this account or password outside local development.

Safe to run more than once: if the demo user already exists, this
script leaves it untouched rather than erroring or duplicating rows.
"""

from __future__ import annotations

import asyncio

from app.core.logging import configure_logging, get_logger
from app.database.session import async_session_factory
from app.services.auth_service import EmailAlreadyRegisteredError, register_user

DEMO_EMAIL = "demo@example.com"
DEMO_PASSWORD = "demo-paper-trading-123"
DEMO_FULL_NAME = "Demo User"

logger = get_logger(__name__)


async def seed_demo_user() -> None:
    async with async_session_factory() as db:
        try:
            await register_user(
                db, email=DEMO_EMAIL, password=DEMO_PASSWORD, full_name=DEMO_FULL_NAME
            )
        except EmailAlreadyRegisteredError:
            logger.info("Demo user already exists (%s) — nothing to do.", DEMO_EMAIL)
            return

    logger.info("Created demo user %s with default PAPER-mode settings.", DEMO_EMAIL)


def main() -> None:
    configure_logging()
    asyncio.run(seed_demo_user())
    print(
        "\nDemo login (local/demo use only — never reuse this password anywhere real):\n"
        f"  email:    {DEMO_EMAIL}\n"
        f"  password: {DEMO_PASSWORD}\n"
    )


if __name__ == "__main__":
    main()
