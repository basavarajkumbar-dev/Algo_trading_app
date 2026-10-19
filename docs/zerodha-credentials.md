# Zerodha credentials entered in the app

Each user enters **their own** Kite Connect API key and API secret in the
Android app: *Settings → Broker connections → Zerodha Kite Connect*. Nothing
is predefined: no key, secret, access token or request token exists in the
source code, the APK, `.env.example` or Git, and a fresh install shows empty
fields.

## What happens to the credentials

| Step | Where | How |
|---|---|---|
| Typed | phone | Kept in the screen's memory only; the secret field is masked (Show/Hide toggle); cleared as soon as the server confirms the save. Never written to phone storage, never logged. |
| Sent | phone → backend | Once, `PUT /api/v1/broker/zerodha/credentials`, signed-in user only, **HTTPS only** (the app refuses a non-HTTPS backend outside development builds; the backend refuses plain HTTP in production/staging). |
| Checked | backend | Format only (letters/digits). Zerodha itself checks the API key at login and the API secret during the token exchange — there is no official call that validates a secret on its own, so the app says so instead of pretending. |
| Stored | backend database | AES-256-GCM, a fresh nonce per value, bound to the user and the field (a value copied to another user's row cannot be decrypted). The key is `CREDENTIALS_ENCRYPTION_KEY` (or a Docker-secret file), **not** in the database. Without a key, saving is refused — there is no plaintext fallback. |
| Shown | app | Only a masked key hint (`••••1234`) and "Configured". The secret is never returned by any endpoint. |
| Used | backend | The secret only computes the official checksum `sha256(api_key + request_token + api_secret)` for the token exchange. |

Logs: no credential is logged by the app code; in addition every log line
from the HTTP client and the web server is passed through a redaction filter
that masks `access_token=`, `request_token=`, `api_secret=`, `checksum=` and
`Authorization: token …` (Kite's logout call carries the token in its URL).

## Connecting (official Kite Connect flow)

1. **Connect to Zerodha** → the backend returns Zerodha's login URL for your
   API key with a one-time `state` (valid 10 minutes).
2. You log in on Zerodha's own page (system browser) and authorise the app.
3. Zerodha redirects to the backend callback (`KITE_REDIRECT_URL`, register it
   at developers.kite.trade) with the request token.
4. The backend exchanges it (`POST /session/token` with the checksum).
5. The access token is encrypted into your record and the app is reopened.
6. The status shown is what the backend verified — never "Connected" because a
   button was pressed.

## Status values (from the backend)

| Status | Meaning |
|---|---|
| Not Configured | No credentials saved for your account. |
| Credentials Saved | Saved; never connected with them yet. |
| Not Connected | Disconnected (credentials kept). |
| Awaiting Zerodha Login | A login was started and has not come back yet. |
| Connected | Zerodha accepted the session (checked by the backend). |
| Session Expired | Zerodha sessions end daily (~06:00 IST). Credentials are kept: tap **Reconnect to Zerodha**. |
| Connection Failed | Login cancelled/denied, or Zerodha rejected the token exchange (e.g. the secret does not belong to the key). The reason is shown. |

You never re-enter the key and secret each day — only the Zerodha login is
repeated. **Update Credentials** replaces them (the old session is ended at
Zerodha); **Remove Credentials** deletes the key, secret and session token and
logs the session out at Zerodha.

## Rules on the server

- Endpoints (`/api/v1/broker/zerodha/credentials`, `/test`, `/connect`,
  `/disconnect`): signed-in users only, each acts on **its own** record (no
  user id in any request); managing needs the TRADER or ADMIN role; changes are
  rate-limited (5 saves/removals and 20 other actions per 10 minutes per user);
  error messages never echo a submitted value.
- One Zerodha account drives the server at a time. Another user's saved
  credentials cannot take over while that account is connected (409).
- If the server sets `KITE_API_KEY`/`KITE_API_SECRET` itself (operator
  override, not recommended) the in-app credentials are locked and the app
  says so.
- Paper trading never needs credentials. Live orders additionally need
  `EXECUTION_MODE=ZERODHA`, `LIVE_TRADING_ENABLED=true`, an admin's arming and
  every pre-trade check — saving credentials or connecting never enables them.
- Market data: credentials present at start-up select real Zerodha data;
  credentials saved for the first time while the server runs allow the login
  immediately, and real market data starts after the next backend restart
  (the app shows this). The data source never switches mid-session.

## Server setup

```
python -m app.scripts.generate_credentials_key > secrets/credentials_key   # once
# docker-compose.prod.yml passes it as CREDENTIALS_ENCRYPTION_KEY_FILE
```

Back the key up separately from the database. Losing it makes saved
credentials unreadable (users re-enter them; nothing else is affected).
Rotation: set the new key, list the old one in
`CREDENTIALS_ENCRYPTION_PREVIOUS_KEYS`; values are re-encrypted when saved.

Tests: `backend/app/tests/test_broker_credentials.py` (fake Kite server, no
network, no orders), `mobile/__tests__/credentials.test.tsx`.
