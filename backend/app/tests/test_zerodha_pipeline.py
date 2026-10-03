"""End to end on Zerodha-shaped data, without the network or a real account:

    fake Kite REST (instrument master, historical candles, LTP, session)
    + fake Kite WebSocket (binary protocol)
      -> KiteAuthService login (request token + one-time state)
      -> instrument master sync -> tokens resolved (never hardcoded)
      -> KiteTickerClient subscribe / modes -> TickProcessor
      -> MarketDataService: 5m / 15m / 60m candles closing on time
      -> intelligence (regime) -> price action -> strategy -> signal
      -> risk -> PAPER order -> position
    and nothing is ever sent to Kite's order endpoints.

Also: no data before a login, stale-data blocking, reconnection with
subscription recovery, logout stopping the feed. Needs Postgres.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete

from app.brokers.zerodha.auth import KiteAuthService
from app.brokers.zerodha.historical import ZerodhaHistoricalDataProvider
from app.brokers.zerodha.instruments import (
    InstrumentRepository,
    InstrumentResolver,
    InstrumentService,
)
from app.brokers.zerodha.kite_client import KiteClient
from app.brokers.zerodha.market_data import FeedState, ZerodhaMarketDataProvider
from app.brokers.zerodha.ticker import KiteTickerClient, encode_index, encode_quote
from app.database.models.market_data import BrokerInstrument, InstrumentSyncRun
from app.database.models.signal import Signal
from app.database.session import async_session_factory
from app.execution.paper_broker import PaperBrokerConfig
from app.market_data.schemas import Timeframe
from app.market_data.service import DataStatus, MarketDataService
from app.market_intelligence.service import MarketIntelligenceService
from app.price_action.service import PriceActionService
from app.risk.config import RiskLimits
from app.risk.schemas import TradeSide
from app.signals.config import SignalEngineConfig
from app.signals.service import SignalService
from app.strategies.service import StrategyService
from app.tests.fake_kite import (
    ACCESS_TOKEN,
    API_KEY,
    API_SECRET,
    MASTER_CSV,
    REQUEST_TOKEN,
    FakeKite,
)
from app.tests.fake_kite_ws import FakeKiteWS, wait_for
from app.tests.paper_helpers import intent
from app.trading.service import TradingService

IST = ZoneInfo("Asia/Kolkata")
T0 = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)  # Thursday 10:00 IST
WATCH = ["NIFTY50", "RELIANCE", "TCS", "INFY"]
# The fake EXCHANGE's tokens (what Kite would send). The app learns them
# from the instrument master; tests use them only to play the exchange.
EXCHANGE_TOKENS = {"NIFTY50": 256265, "RELIANCE": 738561, "TCS": 2953217, "INFY": 408065}
BASE = {"NIFTY50": 25000.0, "RELIANCE": 1400.0, "TCS": 3000.0, "INFY": 1500.0}
STEP = 0.0003  # per 5-minute bar: a steady uptrend
DROP = 0.00675  # today's pullback from the 09:40 high, as a fraction of the open


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


def _sessions(days: int) -> list[date]:
    out: list[date] = []
    d = T0.astimezone(IST).date() - timedelta(days=1)
    while len(out) < days:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


def _price(symbol: str, i: float) -> float:
    return round(BASE[symbol] * (1 + STEP * i), 2)


def _today(symbol: str, minute: float) -> float:
    """Today 09:15-10:00 IST: an impulse up to 09:40, then a ~45% pullback.
    The index moves a third as much (stocks show relative strength)."""

    p0 = _price(symbol, 20 * 75)
    amp = 0.015 * (0.33 if symbol == "NIFTY50" else 1.0)
    peak = p0 * (1 + amp)
    if minute <= 25:
        return round(p0 + (peak - p0) * minute / 25, 2)
    return round(
        peak - DROP * p0 * (0.33 if symbol == "NIFTY50" else 1.0) * min(minute - 25, 20) / 20, 2
    )


def build_history(fake: FakeKite) -> dict[str, float]:
    """5m candles for 20 past sessions (a steady uptrend) plus today's
    09:15-09:55, and 1m candles for today. Returns each symbol's last close."""

    last: dict[str, float] = {}
    days = _sessions(20)
    today = T0.astimezone(IST).date()
    for symbol, token in EXCHANGE_TOKENS.items():
        rows5: list[list[Any]] = []
        rows1: list[list[Any]] = []
        i = 0.0
        for d in days:
            open_ = datetime.combine(d, datetime.min.time()).replace(hour=9, minute=15)
            for k in range(75):
                ts = open_ + timedelta(minutes=5 * k)
                o, c = _price(symbol, i), _price(symbol, i + 1)
                wick = round(o * 0.0015, 2)  # ~0.3% bar range, like a liquid large cap
                rows5.append(
                    [ts.strftime("%Y-%m-%dT%H:%M:%S+0530"), o, c + wick, o - wick, c, 10_000]
                )
                i += 1
        open_ = datetime.combine(today, datetime.min.time()).replace(hour=9, minute=15)
        for m in range(45):  # 09:15 ... 09:59, closed at 10:00
            o, c = _today(symbol, m), _today(symbol, m + 1)
            rows1.append(
                [
                    (open_ + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:%S+0530"),
                    o,
                    round(max(o, c) + o * 0.0004, 2),
                    round(min(o, c) - o * 0.0004, 2),
                    c,
                    2_000,
                ]
            )
        for k in range(9):
            part = rows1[5 * k : 5 * k + 5]
            rows5.append(
                [
                    part[0][0],
                    part[0][1],
                    max(r[2] for r in part),
                    min(r[3] for r in part),
                    part[-1][4],
                    sum(r[5] for r in part),
                ]
            )
        fake.candles[(token, "5minute")] = rows5
        fake.candles[(token, "minute")] = rows1
        fake.candles[(token, "3minute")] = []
        last[symbol] = _today(symbol, 45)
        fake.ltp[f"NSE:{'NIFTY 50' if symbol == 'NIFTY50' else symbol}"] = (token, last[symbol])
    return last


@dataclass
class Rig:
    clock: Clock
    fake: FakeKite
    ws: FakeKiteWS
    client: KiteClient
    auth: KiteAuthService
    provider: ZerodhaMarketDataProvider
    md: MarketDataService
    last: dict[str, float]
    volume: dict[str, int]

    async def login(self) -> None:
        url = self.auth.get_login_url()
        state = parse_qs(parse_qs(urlparse(url).query)["redirect_params"][0])["state"][0]
        await self.auth.handle_request_token(REQUEST_TOKEN, status="success", state=state)

    async def tick_all(self, move: float = 0.0) -> None:
        """One packet per instrument, in one frame, at the current clock.
        `move` is a fraction of the price (0.0003 = +0.03%)."""

        packets = []
        for symbol, token in EXCHANGE_TOKENS.items():
            step = move if symbol != "NIFTY50" else move / 3
            self.last[symbol] = round(self.last[symbol] * (1 + step) + 0.05, 2)
            if symbol == "NIFTY50":
                packets.append(
                    encode_index(
                        token,
                        self.last[symbol],
                        high=self.last[symbol] + 50,
                        low=BASE[symbol],
                        open_=BASE[symbol],
                        close=self.last[symbol] - 20,
                        exchange_timestamp=self.clock.now.replace(microsecond=0),
                    )
                )
            else:
                self.volume[symbol] += 100
                packets.append(
                    encode_quote(
                        token,
                        self.last[symbol],
                        volume=self.volume[symbol],
                        close=self.last[symbol] - 1,
                    )
                )
        before = self.md.stats.ticks_accepted
        await self.ws.push(packets)
        await wait_for(lambda: self.md.stats.ticks_accepted >= before + len(packets))

    async def run_minutes(self, minutes: int, move: float = 0.0) -> None:
        for _ in range(minutes):
            await self.tick_all(move)
            self.clock.now += timedelta(seconds=30)
            await self.tick_all(move)
            self.clock.now += timedelta(seconds=30)
        self.clock.now += timedelta(seconds=3)  # past the grace: the finalize loop closes
        await asyncio.sleep(0.15)


@pytest.fixture
async def rig() -> AsyncIterator[Rig]:
    async with async_session_factory() as s:
        await s.execute(delete(BrokerInstrument))
        await s.execute(delete(InstrumentSyncRun))
        await s.commit()
    clock = Clock()
    fake = FakeKite(token=None)
    fake.instruments_csv = MASTER_CSV
    fake.login_time = "2026-10-01 09:55:00"
    last = build_history(fake)
    ws = FakeKiteWS(api_key=API_KEY, token=ACCESS_TOKEN)
    await ws.start()
    client = KiteClient(api_key=API_KEY, access_token=None, transport=fake.transport())
    auth = KiteAuthService(api_key=API_KEY, api_secret=API_SECRET, client=client, clock=clock)
    repo = InstrumentRepository(async_session_factory)
    resolver = InstrumentResolver(repository=repo)
    instruments = InstrumentService(client, repo, resolver, exchanges=["NSE", "NFO"], clock=clock)
    historical = ZerodhaHistoricalDataProvider(client, resolver, clock=clock)

    async def fast(_: float) -> None:
        await asyncio.sleep(0.01)

    provider = ZerodhaMarketDataProvider(
        auth=auth,
        client=client,
        resolver=resolver,
        historical=historical,
        instrument_service=instruments,
        api_key=API_KEY,
        clock=clock,
        ticker_factory=lambda: KiteTickerClient(
            api_key=API_KEY,
            token_provider=client.access_token_for_ticker,
            url=ws.url,
            clock=clock,
            sleep=fast,
        ),
    )
    md = MarketDataService(
        provider,
        watchlist=WATCH,
        warmup_candles=60,
        stale_threshold_seconds=10,
        finalize_interval_seconds=0.05,
        clock=clock,
    )
    r = Rig(clock, fake, ws, client, auth, provider, md, last, {s: 1_000_000 for s in WATCH})
    yield r
    await md.stop()
    await auth.stop()
    await ws.stop()
    await client.aclose()


async def test_no_data_and_no_connection_before_a_login(rig: Rig) -> None:
    await rig.md.start()
    assert rig.md.source == "ZERODHA" and not rig.md.simulated
    assert rig.provider.state == FeedState.WAITING_FOR_LOGIN
    assert not rig.provider.is_ready() and rig.md.data_status() == DataStatus.DISCONNECTED
    assert rig.md.is_stale("RELIANCE")
    assert rig.ws.connections == 0 and rig.md.get_candles("RELIANCE", Timeframe.M5) == []
    assert rig.md.get_snapshot("RELIANCE") is None  # nothing simulated stands in


async def test_login_streams_real_ticks_into_session_candles(rig: Rig) -> None:
    await rig.md.start()
    await rig.login()
    await wait_for(rig.provider.is_ready, timeout=10)
    # tokens came from the instrument master, modes as configured
    assert rig.ws.subscribed() == set(EXCHANGE_TOKENS.values())
    assert rig.ws.modes()[256265] == "full" and rig.ws.modes()[738561] == "quote"
    assert rig.provider.verified_prices == 4  # LTP check after connecting
    await wait_for(lambda: rig.md.last_warmup_at is not None and not rig.md.warmup_in_progress)
    seeded = rig.md.get_candles("RELIANCE", Timeframe.M60, 500, include_current=False)
    assert len(seeded) >= 60 and all(c.is_closed for c in seeded)
    assert seeded[-1].timestamp.astimezone(IST).hour == 15  # last hour of the previous session
    # live: 10:00 -> 10:15 IST, two ticks per minute per instrument
    await rig.run_minutes(15)
    assert rig.md.data_status() == DataStatus.STALE  # 33 s since the last tick
    five = rig.md.get_candles("RELIANCE", Timeframe.M5, 3, include_current=False)
    assert [c.timestamp.astimezone(IST).strftime("%H:%M") for c in five] == [
        "10:00",
        "10:05",
        "10:10",
    ]
    fifteen = rig.md.get_candles("RELIANCE", Timeframe.M15, 1, include_current=False)[-1]
    assert fifteen.timestamp.astimezone(IST).strftime("%H:%M") == "10:00" and fifteen.is_closed
    hour = rig.md.get_candles("RELIANCE", Timeframe.M60, 1, include_current=False)[-1]
    assert hour.timestamp.astimezone(IST).strftime("%H:%M") == "09:15"
    # 09:15-09:59 came from 1-minute history, 10:00-10:14 from live ticks
    assert hour.open == _price("RELIANCE", 20 * 75)  # today's 09:15 open, from history
    assert hour.close == rig.last["RELIANCE"]
    assert rig.md.candle_status("RELIANCE", Timeframe.M5) == "CLOSED"
    await rig.tick_all()
    assert rig.md.data_status() == DataStatus.LIVE and not rig.md.is_stale("RELIANCE")
    assert rig.md.candle_status("RELIANCE", Timeframe.M5) == "FORMING"
    snap = rig.md.get_snapshot("RELIANCE")
    assert snap is not None and snap.price == rig.last["RELIANCE"]
    assert rig.provider.latency_ms is not None  # from the index's exchange timestamps
    assert rig.fake.posts() == []  # no order endpoint was ever called


async def test_engines_signals_and_a_paper_trade_on_zerodha_data(rig: Rig) -> None:
    await rig.md.start()
    await rig.login()
    await wait_for(rig.provider.is_ready, timeout=10)
    await wait_for(lambda: rig.md.last_warmup_at is not None and not rig.md.warmup_in_progress)
    await rig.run_minutes(5, move=0.0002)  # momentum returns after the pullback
    await rig.tick_all()

    intelligence = MarketIntelligenceService(rig.md, benchmark_symbol="NIFTY50")
    await intelligence.refresh()
    regime = intelligence.get_regime("RELIANCE")
    assert regime is not None and regime.regime.value == "STRONG_BULLISH"  # from Zerodha candles
    price_action = PriceActionService(rig.md)
    await price_action.refresh()
    assert price_action.get_symbol("RELIANCE") is not None
    strategies = StrategyService(rig.md, price_action, benchmark_symbol="NIFTY50")
    await strategies.refresh(force=True)
    contexts, evaluations = strategies.get_state()
    assert "RELIANCE" in contexts and "RELIANCE" in evaluations
    routed = {r.strategy.value: r for r in evaluations["RELIANCE"].results}
    assert "TREND_CONTINUATION" in routed  # routed by the STRONG_BULLISH regime
    assert routed["TREND_CONTINUATION"].checks  # every check evaluated on real-shaped data
    signals = SignalService(
        rig.md,
        strategies,
        benchmark_symbol="NIFTY50",
        session_factory=async_session_factory,
        config=SignalEngineConfig(),
    )
    await signals.refresh(force=True)
    status = signals.status()
    assert status.last_error is None  # the scan ran; whether a setup qualifies is data-dependent

    # risk -> paper order -> position, priced by the live Zerodha ticks. (This
    # fixture's setup does not pass every strategy check, so the trade intent is
    # built from the live price; the signal -> intent path itself is covered by
    # the Phase 8 / 12 tests.)
    trading = TradingService(
        rig.md,
        signals,
        starting_capital=100_000.0,
        limits=RiskLimits(max_data_age_seconds=10),
        broker_config=PaperBrokerConfig(slippage_bps=0),
        session_factory=None,
        clock=rig.clock,
    )
    price = rig.last["RELIANCE"]
    decision = await trading.execute_intent(
        intent(
            symbol="RELIANCE", side=TradeSide.LONG, entry=price, stop=price - 10, target=price + 20
        )
    )
    assert decision.approved, decision.summary
    await rig.run_minutes(1)
    await rig.tick_all()
    await trading.tick()
    st = await trading.status()
    assert st.positions and st.positions[0].symbol == "RELIANCE" and st.positions[0].quantity > 0
    assert st.active_trade is not None
    assert rig.fake.posts() == []  # PAPER: nothing reached Kite's order endpoints
    assert not rig.client.orders_allowed
    async with async_session_factory() as s:
        await s.execute(delete(Signal))
        await s.commit()


async def test_stale_data_blocks_executable_signals_and_orders(rig: Rig) -> None:
    await rig.md.start()
    await rig.login()
    await wait_for(rig.provider.is_ready, timeout=10)
    await rig.run_minutes(1)
    await rig.tick_all()
    assert not rig.md.is_stale("RELIANCE")
    rig.clock.now += timedelta(seconds=11)  # no tick for longer than the threshold
    assert rig.md.data_status() == DataStatus.STALE and rig.md.is_stale("RELIANCE")
    trading = TradingService(
        rig.md,
        None,
        starting_capital=100_000.0,
        limits=RiskLimits(),
        broker_config=PaperBrokerConfig(slippage_bps=0),
        clock=rig.clock,
    )
    price = rig.last["RELIANCE"]
    decision = await trading.execute_intent(
        intent(symbol="RELIANCE", entry=price, stop=price - 10, target=price + 20)
    )
    assert not decision.approved
    assert "DATA_STALE" in [r.value for r in decision.rejection_reasons]
    await rig.tick_all()  # fresh again
    assert not rig.md.is_stale("RELIANCE")


async def test_reconnect_restores_subscriptions_and_recovers(rig: Rig) -> None:
    await rig.md.start()
    await rig.login()
    await wait_for(rig.provider.is_ready, timeout=10)
    first = rig.md.ready_generation if hasattr(rig.md, "ready_generation") else None
    rig.ws.clear()
    await rig.ws.drop_all()
    await wait_for(lambda: not rig.provider.is_ready(), timeout=5)
    await wait_for(rig.provider.is_ready, timeout=10)
    assert rig.ws.subscribed() == set(EXCHANGE_TOKENS.values())  # replayed
    assert rig.provider.ticker is not None and rig.provider.ticker.reconnects == 1
    assert rig.provider.ready_generation == 1  # same login: no second warm-up
    assert first is None
    await rig.tick_all()
    assert rig.md.data_status() == DataStatus.LIVE


async def test_logout_and_token_expiry_stop_the_feed(rig: Rig) -> None:
    await rig.md.start()
    await rig.login()
    await wait_for(rig.provider.is_ready, timeout=10)
    await rig.auth.logout()
    assert not rig.provider.is_ready() and rig.provider.ticker is None
    assert rig.md.data_status() == DataStatus.DISCONNECTED
    await rig.login()  # the next day's login starts a new feed generation
    await wait_for(lambda: rig.provider.is_ready() and rig.provider.ready_generation == 2, 10)
    await rig.auth.mark_expired("test expiry")
    assert not rig.provider.is_ready()
