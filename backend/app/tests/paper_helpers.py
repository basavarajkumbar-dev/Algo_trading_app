"""Shared builders for the Phase 8 risk / broker / engine tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.execution.paper_broker import PaperBrokerAdapter, PaperBrokerConfig
from app.risk.config import RiskLimits
from app.risk.manager import AccountView, RiskManager
from app.risk.schemas import MarketConditions, TradeIntent, TradeSide
from app.trading.engine import EngineConfig, PaperTradingEngine

# Tuesday 6 Jan 2026, 10:30 IST (05:00 UTC): inside the NSE session.
T0 = datetime(2026, 1, 6, 5, 0, tzinfo=UTC)


class Clock:
    def __init__(self, start: datetime = T0) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float = 1.0) -> datetime:
        self.now += timedelta(seconds=seconds)
        return self.now


def limits(**overrides: Any) -> RiskLimits:
    return RiskLimits(**overrides)


def intent(
    *,
    symbol: str = "RELIANCE",
    side: TradeSide = TradeSide.LONG,
    entry: float = 1000.0,
    stop: float | None = None,
    target: float | None = None,
    score: float | None = 90.0,
    signal_id: uuid.UUID | None | bool = True,
    **extra: Any,
) -> TradeIntent:
    long = side == TradeSide.LONG
    if stop is None:
        stop = entry - 10 if long else entry + 10
    if target is None:
        target = entry + 20 if long else entry - 20
    sid = uuid.uuid4() if signal_id is True else (signal_id or None)
    return TradeIntent(
        signal_id=sid,
        symbol=symbol,
        side=side,
        entry=entry,
        stop_loss=stop,
        target=target,
        score=score,
        strategy="TREND_CONTINUATION",
        **extra,
    )


def conditions(
    clock: Clock, price: float | None = 1000.0, *, age: float = 0.0, healthy: bool = True
) -> MarketConditions:
    return MarketConditions(
        now=clock(),
        last_price=price,
        price_timestamp=(clock() - timedelta(seconds=age)) if price is not None else None,
        system_healthy=healthy,
        health_detail="ok" if healthy else "market data feed is not running",
    )


def account(capital: float = 100_000.0, **extra: Any) -> AccountView:
    return AccountView(capital=capital, available_capital=extra.pop("available", capital), **extra)


def make_engine(
    clock: Clock,
    *,
    capital: float = 100_000.0,
    risk: dict[str, Any] | None = None,
    broker: dict[str, Any] | None = None,
    engine: dict[str, Any] | None = None,
) -> PaperTradingEngine:
    b = PaperBrokerAdapter(
        starting_capital=capital,
        config=PaperBrokerConfig(**{"slippage_bps": 0.0, **(broker or {})}),
        clock=clock,
    )
    r = RiskManager(RiskLimits(**(risk or {})), capital=capital, now=clock())
    return PaperTradingEngine(b, r, config=EngineConfig(**(engine or {})), clock=clock)


async def price(engine: PaperTradingEngine, clock: Clock, value: float, symbol: str = "RELIANCE"):
    clock.advance(1)
    await engine.on_price(symbol, value, clock())


async def open_trade(
    engine: PaperTradingEngine, clock: Clock, *, at: float = 1000.0, **intent_kw: Any
):
    """Feed a price, then execute an intent at that price."""

    await engine.on_price(intent_kw.get("symbol", "RELIANCE"), at, clock())
    return await engine.execute(intent(entry=at, **intent_kw), conditions(clock, at))
