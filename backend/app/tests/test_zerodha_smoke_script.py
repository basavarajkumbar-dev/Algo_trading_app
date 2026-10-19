"""The smoke script against the fake Kite REST + WebSocket servers: all nine
steps run, and no order endpoint is touched."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

import app.scripts.test_zerodha_connection as smoke
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.ticker import encode_quote
from app.core.config import get_settings
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, API_SECRET, FakeKite
from app.tests.fake_kite_ws import FakeKiteWS

IST = ZoneInfo("Asia/Kolkata")


async def test_smoke_script_passes_on_fakes_and_sends_no_orders(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakeKite()
    # an NSE dump big enough to look like the real one, NIFTY 50 included
    rows = [
        "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,"
        "tick_size,lot_size,instrument_type,segment,exchange",
        "256265,1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE",
    ]
    for i, inst in enumerate(smoke.NIFTY_50_UNIVERSE):
        rows.append(
            f"{1000 + i * 256 + 1},{i},{inst.symbol},{inst.name.replace(',', '')},0,,0,0.05,1,EQ,NSE,NSE"
        )
    for i in range(1200):
        rows.append(f"{900000 + i * 256 + 1},{i},X{i},X,0,,0,0.05,1,EQ,NSE,NSE")
    fake.instruments_csv = "\n".join(rows) + "\n"
    for i, inst in enumerate(smoke.NIFTY_50_UNIVERSE):
        fake.ltp[f"NSE:{inst.symbol}"] = (1000 + i * 256 + 1, inst.reference_price)
    fake.ltp["NSE:NIFTY 50"] = (256265, 25000.0)
    # the script asks for the last 7 days of wall-clock time: put the candle
    # on the latest completed weekday (was a fixed date that aged out)
    day = datetime.now(IST).date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    fake.candles[(256265, "5minute")] = [
        [f"{day.isoformat()}T09:15:00+0530", 25000, 25010, 24990, 25005, 0]
    ]
    ws = FakeKiteWS(api_key=API_KEY, token=ACCESS_TOKEN)
    await ws.start()
    monkeypatch.setenv("KITE_API_KEY", API_KEY)
    monkeypatch.setenv("KITE_API_SECRET", API_SECRET)
    monkeypatch.setenv("KITE_ACCESS_TOKEN", ACCESS_TOKEN)
    monkeypatch.setenv("KITE_WS_URL", ws.url)
    get_settings.cache_clear()

    real = KiteClient

    def client(**kw: Any) -> KiteClient:
        return real(**{**kw, "transport": fake.transport()})

    monkeypatch.setattr(smoke, "KiteClient", client)

    async def stream() -> None:
        await ws.connected.wait()
        for _ in range(20):
            await asyncio.sleep(0.05)
            await ws.push([encode_quote(1001, 3020.0, volume=10)])

    pusher = asyncio.create_task(stream())
    try:
        code = await smoke.run(seconds=1.0, sync_db=False)
    finally:
        pusher.cancel()
        await ws.stop()
        get_settings.cache_clear()
    out = capsys.readouterr().out
    assert code == 0, out
    assert out.count("[PASS]") == 9 and "ALL PASSED" in out
    assert fake.posts() == [] and not any(p.startswith("/orders") for _, p, _, _ in fake.requests)
    for secret in (API_SECRET, ACCESS_TOKEN):
        assert secret not in out
