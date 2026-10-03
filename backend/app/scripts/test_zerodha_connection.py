"""Zerodha connection smoke test (Phase 13). READ-ONLY: never places,
modifies or cancels an order.

    python -m app.scripts.test_zerodha_connection [--seconds 15] [--sync-db]

Needs KITE_API_KEY, KITE_API_SECRET and a valid KITE_ACCESS_TOKEN (today's;
get one through Settings > Zerodha > Log in, or the Kite login flow) in the
backend environment. Steps:

  1. configuration      credentials present, modes, live trading disabled
  2. authentication     the access token is accepted (GET /user/profile)
  3. account            equity margins readable (GET /user/margins/equity)
  4. instrument master  NSE dump downloaded and validated (--sync-db stores it)
  5. resolution         NIFTY 50 universe -> instrument tokens (none hardcoded)
  6. REST quotes        LTP for the resolved instruments
  7. historical         last session's 5-minute candles for NIFTY 50
  8. WebSocket          connect, subscribe (quote mode), receive ticks
  9. safety             no order endpoint was called; execution stays PAPER

Exit code 0 when every step passed, 1 otherwise, 2 when not configured.
Secrets are never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from app.brokers.zerodha.historical import ZerodhaHistoricalDataProvider
from app.brokers.zerodha.instruments import InstrumentNotFound, InstrumentResolver
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.mapping import mask
from app.brokers.zerodha.models import parse_instrument_dump
from app.brokers.zerodha.ticker import KiteTickerClient, SubscriptionManager, TickMode
from app.core.config import get_settings
from app.market_data.schemas import Timeframe
from app.market_data.universe import NIFTY_50_UNIVERSE, NIFTY_INDEX_SYMBOL


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[int, str, bool, str]] = []

    def add(self, n: int, name: str, ok: bool, detail: str) -> None:
        self.results.append((n, name, ok, detail))
        print(f"[{'PASS' if ok else 'FAIL'}] {n}. {name}: {detail}", flush=True)

    @property
    def ok(self) -> bool:
        return all(ok for _, _, ok, _ in self.results) and len(self.results) == 9


async def run(seconds: float, sync_db: bool) -> int:
    s = get_settings()
    r = Report()
    secret = s.zerodha_api_secret.get_secret_value() if s.zerodha_api_secret else ""
    token = s.zerodha_access_token.get_secret_value() if s.zerodha_access_token else ""
    if not (s.zerodha_api_key and secret):
        print("Zerodha is not configured (KITE_API_KEY / KITE_API_SECRET). Nothing to test.")
        return 2
    r.add(
        1,
        "configuration",
        bool(token) and s.effective_execution_mode.value == "PAPER" and not s.live_trading_enabled,
        f"api key {mask(s.zerodha_api_key)}, access token {'set' if token else 'MISSING'}, "
        f"market data {s.effective_market_data_mode.value}, execution "
        f"{s.effective_execution_mode.value}, LIVE_TRADING_ENABLED={s.live_trading_enabled}",
    )
    if not token:
        print("Set KITE_ACCESS_TOKEN to today's token (log in first) and run again.")
        return 1

    # orders_allowed=False: this client cannot reach order endpoints at all
    client = KiteClient(
        api_key=s.zerodha_api_key, access_token=token, base_url=s.zerodha_api_base_url
    )
    try:
        try:
            profile = await client.profile()
            r.add(2, "authentication", True, f"user {mask(profile.get('user_id'))}")
        except Exception as exc:  # noqa: BLE001
            r.add(2, "authentication", False, f"{type(exc).__name__}: token rejected or expired")
            return 1

        try:
            m = await client.margins_equity()
            r.add(3, "account", True, f"equity net {m.get('net')}")
        except Exception as exc:  # noqa: BLE001
            r.add(3, "account", False, type(exc).__name__)

        resolver = InstrumentResolver()
        try:
            started = time.perf_counter()
            body = await client.instruments("NSE")
            parsed = parse_instrument_dump(body, exchanges={"NSE"})
            resolver.load(parsed.records)
            detail = (
                f"{len(parsed.records)} valid, {parsed.invalid_rows} invalid rows "
                f"in {time.perf_counter() - started:.1f}s"
            )
            if sync_db:
                from app.brokers.zerodha.instruments import InstrumentRepository, InstrumentService
                from app.database.session import async_session_factory

                repo = InstrumentRepository(async_session_factory)
                svc = InstrumentService(
                    client, repo, resolver, exchanges=list(s.instrument_exchanges)
                )
                res = await svc.sync_instruments()
                detail += f"; DB sync {'ok' if res.ok else 'FAILED: ' + str(res.error)}"
            r.add(4, "instrument master", len(parsed.records) > 1000, detail)
        except Exception as exc:  # noqa: BLE001
            r.add(4, "instrument master", False, f"{type(exc).__name__}: {exc}")

        symbols = [NIFTY_INDEX_SYMBOL, *(i.symbol for i in NIFTY_50_UNIVERSE)]
        tokens: dict[int, str] = {}
        missing: list[str] = []
        for sym in symbols:
            try:
                tokens[resolver.resolve_app_symbol(sym).instrument_token] = sym
            except InstrumentNotFound:
                missing.append(sym)
        r.add(
            5,
            "resolution",
            len(tokens) >= 45,
            f"{len(tokens)}/{len(symbols)} resolved"
            + (f"; not found (index changes?): {', '.join(missing)}" if missing else ""),
        )

        try:
            keys = []
            for t in tokens:
                rec = resolver.find_by_token(t)
                if rec is not None:
                    keys.append(f"{rec.exchange}:{rec.tradingsymbol}")
            ltp = await client.ltp(keys)
            sample = next(iter(ltp.items()), None)
            r.add(
                6,
                "REST quotes",
                len(ltp) >= len(keys) * 0.9,
                f"{len(ltp)} LTPs"
                + (f", e.g. {sample[0]} {sample[1].get('last_price')}" if sample else ""),
            )
        except Exception as exc:  # noqa: BLE001
            r.add(6, "REST quotes", False, f"{type(exc).__name__}: {exc}")

        try:
            hist = ZerodhaHistoricalDataProvider(client, resolver)
            now = datetime.now(UTC)
            candles = await hist.get_historical_candles(
                NIFTY_INDEX_SYMBOL, Timeframe.M5, now - timedelta(days=7), now
            )
            last = candles[-1].timestamp.isoformat() if candles else "none"
            r.add(7, "historical", bool(candles), f"{len(candles)} closed 5m candles, last {last}")
        except Exception as exc:  # noqa: BLE001
            r.add(7, "historical", False, f"{type(exc).__name__}: {exc}")

        ticks: list[dict[str, Any]] = []
        ticker = KiteTickerClient(
            api_key=s.zerodha_api_key,
            token_provider=lambda: token,
            url=s.kite_ws_url,
            reconnect_max_tries=2,
        )
        manager = SubscriptionManager(ticker, default_mode=TickMode.QUOTE)

        async def on_connect() -> None:
            await manager.recover_subscriptions_after_reconnect()

        ticker.on_connect = on_connect
        ticker.on_ticks = lambda batch, at: ticks.extend(batch)
        await manager.subscribe_many(list(tokens))
        await ticker.connect()
        connected = await ticker.wait_connected(15)
        if connected:
            await asyncio.sleep(seconds)
        await ticker.disconnect()
        seen = {t["instrument_token"] for t in ticks}
        r.add(
            8,
            "WebSocket",
            connected and bool(ticks),
            f"connected={connected}, {len(ticks)} ticks for {len(seen)} instruments in "
            f"{seconds:.0f}s, heartbeats {ticker.heartbeats}"
            + ("" if ticks else " (outside market hours Kite sends no ticks)"),
        )

        r.add(
            9,
            "safety",
            not client.orders_allowed and s.effective_execution_mode.value == "PAPER",
            "no order endpoint called; this client cannot place orders; execution PAPER",
        )
    finally:
        await client.aclose()
    print("RESULT:", "ALL PASSED" if r.ok else "SOME STEPS FAILED")
    return 0 if r.ok else 1


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--seconds", type=float, default=15.0, help="how long to stream ticks")
    parser.add_argument("--sync-db", action="store_true", help="also store the instrument master")
    args = parser.parse_args()
    sys.exit(asyncio.run(run(args.seconds, args.sync_db)))


if __name__ == "__main__":
    main()
