"""Print a new CREDENTIALS_ENCRYPTION_KEY (32 random bytes, base64).

    python -m app.scripts.generate_credentials_key > /run/secrets/credentials_key

Keep it outside the database and outside Git. Losing it makes the saved broker
credentials unreadable (users then re-enter them); leaking it together with a
database copy exposes them.
"""

from app.security.credential_vault import generate_key

if __name__ == "__main__":
    print(generate_key())
