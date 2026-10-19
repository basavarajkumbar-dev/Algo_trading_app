"""User-entered Zerodha Kite Connect credentials.

All endpoints need a signed-in user and act on that user's own record only
(there is no user id in any path or body). Managing credentials needs the
TRADER or ADMIN role; in production / staging they are accepted over HTTPS
only. Mutations are rate limited per user. No response, error or log line
contains the API secret, the API key (only a masked hint) or any token.

GET    /broker/zerodha/credentials             status (never the secret)
PUT    /broker/zerodha/credentials             save or replace {api_key, api_secret}
DELETE /broker/zerodha/credentials             remove (ends the Kite session)
POST   /broker/zerodha/credentials/test        verify what can be verified now
POST   /broker/zerodha/credentials/connect     official Kite login URL (?client=mobile)
POST   /broker/zerodha/credentials/disconnect  end the Kite session, keep credentials
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.deps import get_current_user, require_role
from app.api.v1.broker import _mobile_states, _state_of, market_data_label
from app.brokers.zerodha.credentials import BrokerCredentialService, CredentialError
from app.core.config import get_settings
from app.core.constants import UserRole
from app.core.rate_limit import SlidingWindowLimiter
from app.database.models.user import User

router = APIRouter(prefix="/broker/zerodha/credentials", tags=["broker credentials"])
_manager = require_role(UserRole.TRADER, UserRole.ADMIN)

# per user: saves / removals, and other actions (test, connect, disconnect)
WRITE_LIMIT = SlidingWindowLimiter(limit=5, window_seconds=600)
ACTION_LIMIT = SlidingWindowLimiter(limit=20, window_seconds=600)

MAX_BODY_BYTES = 2048

_BODY_SCHEMA = {
    "requestBody": {
        "required": True,
        "content": {
            "application/json": {
                "schema": {
                    "type": "object",
                    "required": ["api_key", "api_secret"],
                    "properties": {
                        "api_key": {"type": "string", "minLength": 6, "maxLength": 64},
                        "api_secret": {
                            "type": "string",
                            "minLength": 12,
                            "maxLength": 128,
                            "format": "password",
                            "writeOnly": True,
                        },
                    },
                }
            }
        },
    }
}


def _service(request: Request) -> BrokerCredentialService:
    svc: BrokerCredentialService | None = getattr(request.app.state, "broker_credentials", None)
    if svc is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "Broker credential service is not running."
        )
    return svc


def require_https(request: Request) -> None:
    """Credentials travel over HTTPS only. Behind the TLS proxy uvicorn runs
    with --proxy-headers, so the scheme reflects X-Forwarded-Proto."""

    if get_settings().app_env.lower() in ("production", "staging") and (
        request.url.scheme != "https"
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Broker credentials are accepted over HTTPS only."
        )


def _limit(limiter: SlidingWindowLimiter, user: User) -> None:
    wait = limiter.check(user.id)
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many credential requests. Try again in a few minutes.",
            headers={"Retry-After": str(int(wait) + 1)},
        )


def _http(exc: CredentialError) -> HTTPException:
    return HTTPException(exc.status_code, exc.message)


def _with_market(request: Request, body: dict[str, Any]) -> dict[str, Any]:
    """Add the market-data source, and say when it changes only on restart."""

    label = market_data_label(request)
    note = None
    if label == "SIMULATED" and body.get("configured"):
        if get_settings().market_data_mode is None:
            note = (
                "Market data is still simulated: real Zerodha data starts after the "
                "backend restarts with these credentials. Paper trading continues meanwhile."
            )
        else:
            note = "The server is set to simulated market data (MARKET_DATA_MODE)."
    return {**body, "market_data": label, "market_data_note": note}


@router.get("")
async def get_status(request: Request, user: User = Depends(get_current_user)) -> dict[str, Any]:
    return _with_market(request, await _service(request).status(user.id))


@router.put("", dependencies=[Depends(require_https)], openapi_extra=_BODY_SCHEMA)
async def save(request: Request, user: User = Depends(_manager)) -> dict[str, Any]:
    _limit(WRITE_LIMIT, user)
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "Request too large.")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        # never echo the body back: it may hold the secret
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send JSON with api_key and api_secret.")
    if not isinstance(body, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Send JSON with api_key and api_secret.")
    try:
        result = await _service(request).save(user.id, body.get("api_key"), body.get("api_secret"))
        return _with_market(request, result)
    except CredentialError as exc:
        raise _http(exc) from None


@router.delete("", dependencies=[Depends(require_https)])
async def remove(request: Request, user: User = Depends(_manager)) -> dict[str, Any]:
    _limit(WRITE_LIMIT, user)
    try:
        return _with_market(request, await _service(request).remove(user.id))
    except CredentialError as exc:
        raise _http(exc) from None


@router.post("/test", dependencies=[Depends(require_https)])
async def test(request: Request, user: User = Depends(_manager)) -> dict[str, Any]:
    _limit(ACTION_LIMIT, user)
    try:
        result = await _service(request).test(user.id)
        return {**result, "status": _with_market(request, result["status"])}
    except CredentialError as exc:
        raise _http(exc) from None


@router.post("/connect", dependencies=[Depends(require_https)])
async def connect(
    request: Request,
    client: str = Query(default="mobile", pattern="^(web|mobile)$"),
    user: User = Depends(_manager),
) -> dict[str, Any]:
    """Start the official Kite login for this user's saved credentials. The
    browser goes to Zerodha, then to the backend callback (token exchange with
    the checksum, server-side), then back to the app."""

    _limit(ACTION_LIMIT, user)
    svc = _service(request)
    try:
        url = await svc.login_url(user.id)
    except CredentialError as exc:
        raise _http(exc) from None
    if client == "mobile":
        state = _state_of(url)
        if state:
            _mobile_states(request).add(state)
    return {"login_url": url, "status": _with_market(request, await svc.status(user.id))}


@router.post("/disconnect", dependencies=[Depends(require_https)])
async def disconnect(request: Request, user: User = Depends(_manager)) -> dict[str, Any]:
    _limit(ACTION_LIMIT, user)
    try:
        return _with_market(request, await _service(request).disconnect(user.id))
    except CredentialError as exc:
        raise _http(exc) from None
