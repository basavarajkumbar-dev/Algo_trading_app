"""User-entered Zerodha credentials: setup, encryption at rest, the official
login flow with the user's own key, session expiry, update, removal, and
protection against other users and unauthenticated callers.

Kite is the fake server (no network, no orders). Needs Postgres."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from starlette.requests import Request

from app.api.v1 import broker_credentials as cred_api
from app.core.config import get_settings
from app.database.models.broker_credential import BrokerCredential
from app.database.session import async_session_factory
from app.main import app
from app.security.credential_vault import CredentialVault, VaultError, generate_key
from app.tests import fake_kite
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, REQUEST_TOKEN, FakeKite
from app.tests.test_broker_api import _user

# Kite API secrets are letters and digits; the fake checks this one
USER_SECRET = "s3cr3tApiSecretValue42"
OTHER_SECRET = "an0therSecretValue9876"
KEY = generate_key()

ENV_CLEAR = (
    "KITE_API_KEY",
    "KITE_API_SECRET",
    "KITE_ACCESS_TOKEN",
    "ZERODHA_API_KEY",
    "ZERODHA_API_SECRET",
    "ZERODHA_ACCESS_TOKEN",
    "CREDENTIALS_ENCRYPTION_KEY",
    "CREDENTIALS_ENCRYPTION_KEY_FILE",
)


async def _wipe() -> None:
    async with async_session_factory() as db:
        await db.execute(delete(BrokerCredential))
        await db.commit()


def _client(monkeypatch: pytest.MonkeyPatch, env: dict[str, str]) -> Iterator[TestClient]:
    for k in ENV_CLEAR:
        monkeypatch.delenv(k, raising=False)
    base = {
        "MARKET_DATA_MODE": "MOCK",
        "FRONTEND_URL": "http://frontend.test",
        "KITE_REDIRECT_URL": "https://api.example.test/api/v1/broker/zerodha/callback",
    }
    for k, v in {**base, **env}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(fake_kite, "API_SECRET", USER_SECRET)
    get_settings.cache_clear()
    import app.main as main_module

    monkeypatch.setattr(main_module, "settings", get_settings())
    cred_api.WRITE_LIMIT.reset()
    cred_api.ACTION_LIMIT.reset()
    with TestClient(app) as c:
        c.portal.call(_wipe)
        yield c
        c.portal.call(_wipe)
    get_settings.cache_clear()


@pytest.fixture
def c(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, {"CREDENTIALS_ENCRYPTION_KEY": KEY})


@pytest.fixture
def no_vault(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    yield from _client(monkeypatch, {})


def _fake_kite() -> FakeKite:
    fake = FakeKite(token=None)
    fake.login_time = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S")
    runtime = app.state.zerodha
    runtime.client._http = httpx.AsyncClient(
        base_url="https://api.kite.trade",
        transport=fake.transport(),
        headers={"X-Kite-Version": "3"},
    )
    return fake


URL = "/api/v1/broker/zerodha/credentials"
FORBIDDEN_TEXT = (USER_SECRET, OTHER_SECRET, ACCESS_TOKEN, REQUEST_TOKEN)


def _clean(*texts: str) -> None:
    for text in texts:
        for secret in FORBIDDEN_TEXT:
            assert secret not in text


def _save(c: TestClient, h: dict[str, str], key: str = API_KEY, secret: str = USER_SECRET) -> Any:
    return c.put(URL, headers=h, json={"api_key": key, "api_secret": secret})


def _state(url: str) -> str:
    return parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]


def _callback(c: TestClient, state: str, status: str = "success") -> httpx.Response:
    return c.get(
        "/api/v1/broker/zerodha/callback",
        params={"request_token": REQUEST_TOKEN, "status": status, "state": state},
        follow_redirects=False,
    )


def _connect(c: TestClient, h: dict[str, str]) -> str:
    r = c.post(f"{URL}/connect?client=mobile", headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["status"]["status"] == "AWAITING_LOGIN"
    return str(r.json()["login_url"])


async def _rows() -> list[BrokerCredential]:
    async with async_session_factory() as db:
        return list((await db.execute(select(BrokerCredential))).scalars())


# --- tests -----------------------------------------------------------------------------


def test_fresh_install_starts_empty_and_paper_trading_works(c: TestClient) -> None:
    assert c.get(URL).status_code == 401  # never without a signed-in user
    h = _user(c)
    st = c.get(URL, headers=h).json()
    assert st["status"] == "NOT_CONFIGURED" and st["status_label"] == "Not Configured"
    assert st["configured"] is False and st["api_key_hint"] is None and st["api_secret"] is None
    assert st["storage"] == "ENCRYPTED" and st["paper_trading_available"] is True
    assert c.get("/api/v1/trading/status").status_code == 200  # paper without Zerodha
    assert c.get("/api/v1/broker/zerodha/status").json()["configured"] is False


def test_validation_messages_never_echo_the_values(c: TestClient) -> None:
    h = _user(c)
    cases = [
        ({"api_key": "", "api_secret": USER_SECRET}, "API key"),
        ({"api_key": API_KEY, "api_secret": ""}, "API secret"),
        ({"api_key": "bad key!", "api_secret": USER_SECRET}, "letters or digits"),
        ({"api_key": API_KEY, "api_secret": USER_SECRET + "-x"}, "letters or digits"),
        ({"api_key": USER_SECRET, "api_secret": USER_SECRET}, "different"),
        ({"api_secret": USER_SECRET}, "API key"),
    ]
    for body, phrase in cases:
        cred_api.WRITE_LIMIT.reset()  # invalid attempts count against the limit too
        r = c.put(URL, headers=h, json=body)
        assert r.status_code == 422 and phrase in r.json()["detail"], r.text
        _clean(r.text)
    cred_api.WRITE_LIMIT.reset()
    bad_json = c.put(URL, headers={**h, "content-type": "application/json"}, content=USER_SECRET)
    assert bad_json.status_code == 400
    _clean(bad_json.text)
    assert c.portal.call(_rows) == []  # nothing was stored


def test_save_encrypts_at_rest_and_shows_only_a_mask(c: TestClient) -> None:
    h = _user(c)
    r = _save(c, h)
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["status"] == "CREDENTIALS_SAVED" and st["status_label"] == "Credentials Saved"
    assert st["api_key_hint"] == "••••" + API_KEY[-4:] and st["api_secret"] == "Configured"
    assert API_KEY not in r.text
    _clean(r.text, c.get(URL, headers=h).text)
    rows = c.portal.call(_rows)
    assert len(rows) == 1
    row = rows[0]
    for column in (row.api_key_encrypted, row.api_secret_encrypted):
        assert column.startswith("v1:") and USER_SECRET not in column and API_KEY not in column
    vault = CredentialVault(KEY)
    ctx = CredentialVault.context(str(row.user_id), "ZERODHA", "api_secret")
    assert vault.decrypt(row.api_secret_encrypted, context=ctx) == USER_SECRET
    with pytest.raises(VaultError):  # bound to its owner: useless on another row
        vault.decrypt(
            row.api_secret_encrypted,
            context=CredentialVault.context(str(uuid.uuid4()), "ZERODHA", "api_secret"),
        )
    with pytest.raises(VaultError):  # and useless without the server's key
        CredentialVault(generate_key()).decrypt(row.api_secret_encrypted, context=ctx)
    assert app.state.zerodha.configured and app.state.zerodha.owner_user_id == row.user_id
    assert st["market_data"] == "SIMULATED" and "MARKET_DATA_MODE" in st["market_data_note"]


def test_full_official_flow_test_disconnect_and_logs(
    c: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    fake = _fake_kite()
    h = _user(c)
    assert _save(c, h).status_code == 200
    t = c.post(f"{URL}/test", headers=h).json()
    assert t["ok"] and t["verified"] is False and "connect to Zerodha" in t["message"]
    url = _connect(c, h)
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://kite.zerodha.com/connect/login") and q["api_key"] == [API_KEY]
    assert USER_SECRET not in url  # the secret never leaves the backend
    r = _callback(c, _state(url))
    assert r.headers["location"] == "gainzalgo://zerodha?result=connected"
    st = c.get(URL, headers=h).json()
    assert st["status"] == "CONNECTED" and st["connected"] is True
    assert st["user_id"] and st["user_id"] != "AB1234"  # masked
    rows = c.portal.call(_rows)
    assert rows[0].access_token_encrypted and ACCESS_TOKEN not in rows[0].access_token_encrypted
    assert rows[0].last_connected_at is not None
    t = c.post(f"{URL}/test", headers=h).json()
    assert t["ok"] and t["verified"] is True
    # credentials are not asked for again; a new login only re-runs Zerodha's
    out = c.post(f"{URL}/disconnect", headers=h).json()
    assert out["status"] == "NOT_CONNECTED" and out["configured"] is True
    assert any(m == "DELETE" and p == "/session/token" for m, p, *_ in fake.requests)
    assert c.portal.call(_rows)[0].access_token_encrypted is None
    _clean(caplog.text, r.text, r.headers["location"])
    assert API_KEY not in caplog.text
    assert fake.posts() == []  # no order was ever sent


def test_denied_login_and_wrong_secret_fail_honestly(c: TestClient) -> None:
    _fake_kite()
    h = _user(c)
    _save(c, h)
    r = _callback(c, _state(_connect(c, h)), status="cancelled")
    assert "result=error" in r.headers["location"]
    st = c.get(URL, headers=h).json()
    assert st["status"] == "CONNECTION_FAILED" and not st["connected"]
    assert "cancelled" in st["detail"] or "not authorised" in st["detail"]
    # a secret that does not belong to the key: Zerodha refuses the exchange
    _save(c, h, secret=OTHER_SECRET)
    r = _callback(c, _state(_connect(c, h)))
    assert "result=error" in r.headers["location"]
    st = c.get(URL, headers=h).json()
    assert st["status"] == "CONNECTION_FAILED" and "API secret" in st["detail"]
    assert c.get("/api/v1/broker/zerodha/status").json()["connected"] is False
    _clean(r.text, st["detail"])


def test_session_expiry_needs_reconnect_and_live_stays_off(c: TestClient) -> None:
    _fake_kite()
    h = _user(c)
    _save(c, h)
    _callback(c, _state(_connect(c, h)))
    assert c.get(URL, headers=h).json()["status"] == "CONNECTED"
    c.portal.call(app.state.zerodha.auth.mark_expired, "the Kite session expired (06:00 IST)")
    st = c.get(URL, headers=h).json()
    assert st["status"] == "SESSION_EXPIRED" and st["status_label"] == "Session Expired"
    assert st["configured"] is True  # credentials kept: only the login is repeated
    assert c.portal.call(_rows)[0].access_token_encrypted is None
    broker = c.get("/api/v1/broker/status").json()
    assert (
        broker["live_trading_possible"] is False and broker["zerodha"]["live_orders_armed"] is False
    )
    # reconnect: same saved credentials, new official login
    r = _callback(c, _state(_connect(c, h)))
    assert r.headers["location"].endswith("result=connected")
    assert c.get(URL, headers=h).json()["status"] == "CONNECTED"


def test_saved_session_survives_a_restart_until_it_expires(c: TestClient) -> None:
    from app.brokers.zerodha.credentials import BrokerCredentialService
    from app.brokers.zerodha.runtime import build_zerodha_runtime

    fake = _fake_kite()
    h = _user(c)
    _save(c, h)
    _callback(c, _state(_connect(c, h)))

    async def restart() -> tuple[bool, str | None]:
        runtime = build_zerodha_runtime(get_settings(), transport=fake.transport())
        runtime.client._http = httpx.AsyncClient(  # type: ignore[union-attr]
            base_url="https://api.kite.trade",
            transport=fake.transport(),
            headers={"X-Kite-Version": "3"},
        )
        svc = BrokerCredentialService(runtime, async_session_factory, CredentialVault(KEY))
        loaded = await svc.load_active()
        state = await svc.restore_session()
        await runtime.stop()
        return loaded, state.value if state else None

    assert c.portal.call(restart) == (True, "AUTHENTICATED")


def test_update_replaces_and_remove_deletes_everything(c: TestClient) -> None:
    fake = _fake_kite()
    h = _user(c)
    _save(c, h)
    _callback(c, _state(_connect(c, h)))
    r = _save(c, h, key="newkey9876", secret=OTHER_SECRET)  # update
    st = r.json()
    assert st["status"] == "CREDENTIALS_SAVED" and st["api_key_hint"] == "••••9876"
    assert any(m == "DELETE" and p == "/session/token" for m, p, *_ in fake.requests)
    assert c.portal.call(_rows)[0].access_token_encrypted is None
    assert app.state.zerodha.auth.api_key == "newkey9876"
    gone = c.delete(URL, headers=h)
    assert gone.status_code == 200 and gone.json()["status"] == "NOT_CONFIGURED"
    assert c.portal.call(_rows) == []
    assert not app.state.zerodha.configured and app.state.zerodha.owner_user_id is None
    assert c.get("/api/v1/broker/zerodha/status").json()["configured"] is False
    assert c.delete(URL, headers=h).status_code == 404
    assert c.post(f"{URL}/connect", headers=h).status_code == 409


def test_users_cannot_read_or_change_each_others_credentials(c: TestClient) -> None:
    _fake_kite()
    a, b = _user(c), _user(c)
    _save(c, a)
    st_b = c.get(URL, headers=b).json()
    assert st_b["status"] == "NOT_CONFIGURED" and st_b["api_key_hint"] is None
    assert c.delete(URL, headers=b).status_code == 404  # B has nothing; A untouched
    assert c.get(URL, headers=a).json()["status"] == "CREDENTIALS_SAVED"
    _callback(c, _state(_connect(c, a)))
    assert c.get(URL, headers=a).json()["status"] == "CONNECTED"
    # B saves their own: stored, but A's live session is not hijacked
    r = _save(c, b, key="bkey1234567", secret=OTHER_SECRET)
    assert r.json()["status"] == "CREDENTIALS_SAVED" and not r.json()["active_on_server"]
    assert c.post(f"{URL}/connect", headers=b).status_code == 409
    assert c.post(f"{URL}/disconnect", headers=b).json()["status"] == "CREDENTIALS_SAVED"
    assert c.get(URL, headers=a).json()["status"] == "CONNECTED"
    viewer = _user(c, "VIEWER")
    assert _save(c, viewer).status_code == 403  # read-only role
    assert c.post(f"{URL}/connect", headers=viewer).status_code == 403
    assert c.put(URL, json={"api_key": API_KEY, "api_secret": USER_SECRET}).status_code == 401


def test_without_an_encryption_key_nothing_is_saved(no_vault: TestClient) -> None:
    h = _user(no_vault)
    st = no_vault.get(URL, headers=h).json()
    assert st["storage"] == "UNAVAILABLE" and "CREDENTIALS_ENCRYPTION_KEY" in st["storage_problem"]
    r = _save(no_vault, h)
    assert r.status_code == 503 and "Nothing was saved" in r.json()["detail"]
    assert no_vault.portal.call(_rows) == []
    assert no_vault.get("/api/v1/trading/status").status_code == 200


def test_credential_changes_are_rate_limited(c: TestClient) -> None:
    h = _user(c)
    codes = [_save(c, h).status_code for _ in range(6)]
    assert codes[:5] == [200] * 5 and codes[5] == 429


def test_https_is_required_in_production(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    def request(scheme: str) -> Request:
        return Request(
            {
                "type": "http",
                "scheme": scheme,
                "path": URL,
                "headers": [],
                "server": ("api.example.test", 443),
                "query_string": b"",
            }
        )

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with pytest.raises(HTTPException) as err:
            cred_api.require_https(request("http"))
        assert err.value.status_code == 403
        cred_api.require_https(request("https"))  # accepted
    finally:
        monkeypatch.delenv("APP_ENV")
        get_settings.cache_clear()
    cred_api.require_https(request("http"))  # development: local http allowed


def test_vault_rotation_and_bad_keys() -> None:
    from app.security.credential_vault import VaultUnavailable

    old, new = generate_key(), generate_key()
    ctx = CredentialVault.context("u", "ZERODHA", "api_secret")
    sealed = CredentialVault(old).encrypt("value", context=ctx)
    rotated = CredentialVault(new, [old])
    assert rotated.decrypt(sealed, context=ctx) == "value" and rotated.needs_rotation(sealed)
    assert "value" not in repr(rotated) and new not in repr(rotated)
    for bad in ("not-base64!!", "c2hvcnQ="):
        with pytest.raises(VaultUnavailable):
            CredentialVault(bad)


def test_log_redaction_masks_tokens_in_urls_and_headers() -> None:
    from app.core.logging import redact

    line = (
        "HTTP Request: DELETE https://api.kite.trade/session/token?api_key=k1&access_token=abc123 "
        '"GET /api/v1/broker/zerodha/callback?request_token=rq9&state=s HTTP/1.1" '
        "Authorization: token key1:tok999 checksum=deadbeef"
    )
    out = redact(line)
    for secret in ("abc123", "rq9", "tok999", "deadbeef"):
        assert secret not in out
    assert "api_key=k1" in out and "state=s" in out
