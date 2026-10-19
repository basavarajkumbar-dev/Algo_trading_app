"""Create a user (or change an existing user's role) from the server shell.

    docker compose -f docker-compose.prod.yml exec backend \\
        python -m app.scripts.create_user --email you@example.com --role ADMIN

The password is read from the terminal (never from the command line, so it
does not end up in shell history or process lists) or from stdin with
--password-stdin. It is never printed or logged.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import sys

from app.core.constants import UserRole
from app.database.session import async_session_factory, engine
from app.services.auth_service import get_user_by_email, register_user


async def _run(email: str, role: UserRole, password: str | None, full_name: str | None) -> str:
    async with async_session_factory() as db:
        user = await get_user_by_email(db, email)
        if user is None:
            if not password:
                raise SystemExit("A password is required for a new user.")
            if len(password) < 8:
                raise SystemExit("The password must be at least 8 characters.")
            user = await register_user(db, email=email, password=password, full_name=full_name)
            action = "created"
        else:
            action = "updated"
        user.role = role
        await db.commit()
    await engine.dispose()
    return f"{action} {email} with role {role.value}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--email", required=True)
    parser.add_argument("--role", default="TRADER", choices=[r.value for r in UserRole])
    parser.add_argument("--full-name", default=None)
    parser.add_argument("--password-stdin", action="store_true")
    args = parser.parse_args(argv)
    if args.password_stdin:
        password: str | None = sys.stdin.readline().rstrip("\n")
    else:
        password = getpass.getpass("Password (leave empty to keep an existing user's): ") or None
    print(
        asyncio.run(_run(args.email.strip().lower(), UserRole(args.role), password, args.full_name))
    )


if __name__ == "__main__":
    main()
