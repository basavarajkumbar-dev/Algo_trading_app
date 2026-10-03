"""TradingService: runs the paper trading engine against the live
simulated feed.

Every `poll_seconds`:

1. feeds the latest price of every symbol with an active trade or a
   working order into the engine (and through it the paper broker);
2. lets the engine handle timeouts and square-off;
3. with automatic trading on, after each new scan, sends the best READY
   candidate not yet tried today through SIGNAL -> RISK -> ORDER; in
   APPROVAL mode it proposes READY candidates for human approval instead
   (SIGNAL -> SCORE -> RISK CHECK -> PENDING APPROVAL) and never executes
   on its own;
4. persists whatever changed (orders, trades, events).

System health for the risk check covers the market data feed, the
signal scanner and trade persistence: if trades cannot be recorded, no
new trade is opened.

PAPER only. The engine is constructed with a PaperBrokerAdapter and
there is no code path to a real broker.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.execution.models import Balance, Order, Position
from app.execution.paper_broker import PaperBrokerAdapter, PaperBrokerConfig
from app.market_data.base import utcnow
from app.market_data.schemas import MarketDataProviderStatus
from app.market_data.service import MarketDataService
from app.risk.config import RiskLimits
from app.risk.manager import RiskManager
from app.risk.schemas import (
    MarketConditions,
    RiskDecision,
    RiskSnapshot,
    TradeIntent,
    TradeSide,
)
from app.signals.repository import SignalRecord, get_signal
from app.signals.schemas import SignalDirection, SignalStatus
from app.signals.service import SignalService
from app.trading.engine import EngineConfig, EngineError, PaperTradingEngine
from app.trading.repository import restore, save_changes
from app.trading.schemas import PAPER_NOTE, PaperTrade, TradeStatus
from app.trading.state import StateChange, TradingState

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]


class TradingStatus(BaseModel):
    mode: str
    state: TradingState
    state_since: datetime
    auto_trading: bool
    emergency_stop: bool
    emergency_reason: str | None
    active_trade: PaperTrade | None
    last_decision: RiskDecision | None
    balance: Balance
    positions: list[Position]
    working_orders: list[Order]
    risk: RiskSnapshot
    system_healthy: bool
    health_detail: str
    persistence_ok: bool
    persistence_error: str | None
    restored_trades_today: int
    interrupted_trades: int
    broker: PaperBrokerConfig
    entry_timeout_seconds: float
    recent_transitions: list[StateChange]
    is_running: bool
    market_open: bool  # exchange session (or simulated 24h) is open
    entries_allowed: bool  # session open and before the last entry time
    market_detail: str
    session_mode: str
    note: str = PAPER_NOTE


class SignalNotFound(Exception):
    pass


class TradingService:
    def __init__(
        self,
        market_data: MarketDataService,
        signals: SignalService | None,
        *,
        starting_capital: float,
        limits: RiskLimits,
        broker_config: PaperBrokerConfig,
        engine_config: EngineConfig | None = None,
        session_factory: SessionFactory | None = None,
        poll_seconds: float = 0.5,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._market_data = market_data
        self._signals = signals
        self._session_factory = session_factory
        self._poll_seconds = poll_seconds
        self._clock = clock
        self._starting_capital = starting_capital
        self._limits = limits
        self._broker_config = broker_config
        self._engine_config = engine_config or EngineConfig()
        self.engine = self._new_engine(starting_capital)
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._last_seen: dict[str, tuple[datetime, float]] = {}
        self._seen_scan = -1
        # APPROVAL mode (Phase 12): set by the ApprovalService
        self.approval_hook: Callable[[uuid.UUID], Awaitable[bool]] | None = None
        self.on_mode_change: Callable[[str, str], Awaitable[None]] | None = None
        self.on_emergency_stop: Callable[[str], Awaitable[None]] | None = None
        self._auto_tried: set[uuid.UUID] = set()
        self._persistence_error: str | None = None
        self._persisted_once = False
        self._restored_today = 0
        self._interrupted = 0

    def _new_engine(self, capital: float) -> PaperTradingEngine:
        broker = PaperBrokerAdapter(
            starting_capital=capital, config=self._broker_config, clock=self._clock
        )
        risk = RiskManager(self._limits, capital=capital, now=self._clock())
        return PaperTradingEngine(broker, risk, config=self._engine_config, clock=self._clock)

    # --- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self._task is not None:
            return
        await self._restore()
        self._task = asyncio.create_task(self._run(), name="paper-trading")
        logger.info(
            "Paper trading service started (mode PAPER, automatic trading %s)",
            "on" if self.engine.auto_trading else "off",
        )

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        async with self._lock:
            await self._persist()

    async def _restore(self) -> None:
        """Rebuild capital and today's risk counters from stored trades."""

        if self._session_factory is None:
            self._persistence_error = "No database session configured"
            return
        now = self._clock()
        try:
            async with self._session_factory() as session:
                restored = await restore(session, day=RiskManager.trading_day(now), now=now)
        except Exception as exc:  # noqa: BLE001 - surfaced as unhealthy
            self._persistence_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Could not restore paper trading state: %s", self._persistence_error)
            return
        self._persistence_error = None
        self._persisted_once = True
        realized_today = sum(t.net_pnl for t in restored.today if t.status == TradeStatus.CLOSED)
        capital = self._starting_capital + restored.realized_before_today + realized_today
        day_start = self._starting_capital + restored.realized_before_today
        self.engine = self._new_engine(capital)
        state = self.engine.risk.state
        state.day_start_capital = day_start
        for t in restored.today:
            if t.signal_id is not None:
                state.executed_signal_ids.append(t.signal_id)
            if t.filled_quantity > 0 or t.status == TradeStatus.INTERRUPTED:
                state.trades_today += 1
            if t.status == TradeStatus.CLOSED and t.filled_quantity > 0:
                self.engine.risk.record_trade_result(t.net_pnl)
            self.engine.closed.append(t)
        self._restored_today = len(restored.today)
        self._interrupted = len(restored.interrupted)
        if restored.interrupted:
            logger.warning(
                "%d paper trade(s) were open at shutdown and are marked INTERRUPTED",
                len(restored.interrupted),
            )

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self.tick()
            except Exception:  # noqa: BLE001 - the loop must survive
                logger.exception("Paper trading tick failed")

    # --- the loop body ----------------------------------------------------

    async def tick(self) -> None:
        async with self._lock:
            await self._feed_prices()
            await self.engine.on_clock()
            await self._auto_trade()
            await self._persist()

    async def _feed_prices(self, extra: str | None = None) -> None:
        symbols = {o.symbol for o in self.engine.broker.working_orders()}
        if self.engine.active is not None:
            symbols.add(self.engine.active.symbol)
        if extra:
            symbols.add(extra)
        for symbol in symbols:
            snapshot = self._market_data.get_snapshot(symbol)
            if snapshot is None:
                continue
            seen = (snapshot.timestamp, snapshot.price)
            if self._last_seen.get(symbol) == seen and symbol != extra:
                continue
            self._last_seen[symbol] = seen
            await self.engine.on_price(symbol, snapshot.price, snapshot.timestamp)

    async def _auto_trade(self) -> None:
        engine = self.engine
        if not engine.auto_trading or self._signals is None:
            return
        approval = self.mode == "APPROVAL"
        if approval and self.approval_hook is None:
            return  # nothing may execute without an approval service
        if not approval and (engine.active is not None or not engine.state.is_resting):
            return
        scan_count = self._signals.status().scan_count
        if scan_count == self._seen_scan:
            return
        self._seen_scan = scan_count
        scan = self._signals.get_scan()
        if scan is None:
            return
        for candidate in scan.candidates:  # ranked; READY = at or above the executable score
            if candidate.status != "READY" or candidate.signal_id is None:
                continue
            if candidate.signal_id in self._auto_tried:
                continue
            if candidate.signal_id in engine.risk.state.executed_signal_ids:
                continue
            if approval:
                assert self.approval_hook is not None
                try:
                    handled = await self.approval_hook(candidate.signal_id)
                except Exception:  # noqa: BLE001 - a failed proposal must not stop the loop
                    logger.exception("Approval request for %s failed", candidate.signal_id)
                    handled = True
                if handled:
                    self._auto_tried.add(candidate.signal_id)
                continue  # propose READY candidates once each; a person decides
            self._auto_tried.add(candidate.signal_id)
            try:
                await self._execute_signal_locked(candidate.signal_id)
            except SignalNotFound:
                continue
            break  # one attempt per scan; one active trade at a time

    async def _persist(self) -> None:
        orders, trades, events = self.engine.drain()
        if not (orders or trades or events):
            return
        if self._session_factory is None:
            self._persistence_error = "No database session configured"
            await self._broadcast(events)
            return
        try:
            async with self._session_factory() as session:
                await save_changes(
                    session,
                    orders=orders,
                    trades=trades,
                    events=events,
                    trade_of=self.engine.trade_of,
                    purpose_of=self.engine.purpose_of,
                )
        except Exception as exc:  # noqa: BLE001 - reported as unhealthy, blocks new entries
            self._persistence_error = f"{type(exc).__name__}: {exc}"
            logger.warning("Paper trading changes not stored: %s", self._persistence_error)
            self.engine.requeue(orders, trades, events)  # retried on the next tick
            return
        self._persistence_error = None
        self._persisted_once = True
        await self._broadcast(events)

    async def _broadcast(self, events: list) -> None:
        """Push engine events to /ws/market clients so dashboards refresh
        immediately instead of waiting for their next poll."""

        manager = getattr(self._market_data, "connection_manager", None)
        if manager is None or not events:
            return
        for event in events:
            try:
                await manager.broadcast(
                    {
                        "type": "trading_event",
                        "event_type": event.event_type,
                        "severity": event.severity,
                        "message": event.message,
                        "at": event.at.isoformat(),
                        "metadata": event.metadata,
                    }
                )
            except Exception:  # noqa: BLE001 - a broadcast must never break trading
                logger.exception("Failed to broadcast a trading event")

    # --- health -------------------------------------------------------------

    async def health(self) -> tuple[bool, str]:
        problems: list[str] = []
        if not self._market_data.is_running:
            problems.append("market data feed is not running")
        else:
            try:
                provider = await self._market_data.get_provider_status()
                if provider != MarketDataProviderStatus.CONNECTED:
                    problems.append(f"market data provider is {provider.value}")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"market data provider check failed: {exc}")
        if self._signals is not None:
            status = self._signals.status()
            if status.last_error:
                problems.append(f"signal scan error: {status.last_error}")
        if self._persistence_error:
            problems.append(f"trade storage unavailable: {self._persistence_error}")
        if problems:
            return False, "; ".join(problems)
        return True, "market data, scanner and trade storage healthy"

    async def _conditions(self, symbol: str) -> MarketConditions:
        healthy, detail = await self.health()
        snapshot = self._market_data.get_snapshot(symbol)
        now = self._clock()
        data_stale: str | None = None
        is_stale = getattr(self._market_data, "is_stale", None)
        if is_stale is not None and is_stale(symbol):
            data_stale = (
                f"{self._market_data.source} market data is "
                f"{self._market_data.data_status()} for {symbol}"
            )
        return MarketConditions(
            now=now,
            last_price=snapshot.price if snapshot else None,
            price_timestamp=snapshot.timestamp if snapshot else None,
            system_healthy=healthy,
            health_detail=detail,
            data_stale=data_stale,
        )

    # --- operator actions ----------------------------------------------------

    async def execute_signal(self, signal_id: uuid.UUID) -> RiskDecision:
        async with self._lock:
            decision = await self._execute_signal_locked(signal_id)
            await self._persist()
            return decision

    async def _execute_signal_locked(self, signal_id: uuid.UUID) -> RiskDecision:
        intent, _ = await self.build_intent(signal_id)
        return await self.execute_intent(intent, locked=True)

    async def build_intent(self, signal_id: uuid.UUID) -> tuple[TradeIntent, SignalRecord]:
        """The trade a stored signal describes (no risk check, no side effect)."""

        if self._session_factory is None:
            raise SignalNotFound("Signals are not stored (no database configured).")
        async with self._session_factory() as session:
            record = await get_signal(session, signal_id)
        if record is None:
            raise SignalNotFound(f"Signal {signal_id} not found.")
        signal = record.signal
        if signal.direction == SignalDirection.NONE:
            raise SignalNotFound(f"Signal {signal_id} has no direction.")
        intent = TradeIntent(
            signal_id=signal_id,
            symbol=signal.symbol,
            side=TradeSide.LONG if signal.direction == SignalDirection.BUY else TradeSide.SHORT,
            entry=signal.entry,
            stop_loss=signal.stop_loss,
            target=signal.target,
            score=signal.score,
            strategy=signal.strategy.value,
            signal_last_seen_at=record.last_seen_at,
            signal_active=signal.status == SignalStatus.ACTIVE,
        )
        return intent, record

    async def preview_intent(self, intent: TradeIntent, *, locked: bool = False) -> RiskDecision:
        """Every risk check and the position size, without executing anything."""

        if not locked:
            async with self._lock:
                return await self.preview_intent(intent, locked=True)
        await self._feed_prices(extra=intent.symbol)
        conditions = await self._conditions(intent.symbol)
        return self.engine.preview(intent, conditions)

    @property
    def trade_active(self) -> bool:
        return self.engine.active is not None or not self.engine.state.is_resting

    async def execute_approved(self, intent: TradeIntent) -> tuple[RiskDecision, uuid.UUID | None]:
        """Execute an approved request (paper broker). The risk checks run
        again here; returns the decision and the trade it opened, if any."""

        if intent.approval_id is None:
            raise ValueError("execute_approved needs an approval id")
        async with self._lock:
            before = self.engine.active
            decision = await self.execute_intent(intent, locked=True)
            trade = self.engine.active
            if (
                trade is None
                and self.engine.closed
                and self.engine.closed[-1].signal_id == intent.signal_id
            ):
                trade = self.engine.closed[-1]  # opened and rejected by the broker at once
            trade_id = (
                trade.id
                if decision.approved and trade is not None and trade is not before
                else None
            )
            await self._persist()
        return decision, trade_id

    @property
    def mode(self) -> str:
        return self.engine.config.trading_mode

    async def set_mode(self, mode: str) -> str:
        """Switch between PAPER and APPROVAL (both execute on the paper broker).
        LIVE is not available. Returns the previous mode."""

        async with self._lock:
            previous = self.mode
            self.engine.set_trading_mode(mode)
            self._seen_scan = -1
            self._auto_tried.clear()
        if previous != mode and self.on_mode_change is not None:
            await self.on_mode_change(previous, mode)
        return previous

    async def execute_intent(self, intent: TradeIntent, *, locked: bool = False) -> RiskDecision:
        """Run an intent through risk and, if approved, the paper broker."""

        if not locked:
            async with self._lock:
                decision = await self.execute_intent(intent, locked=True)
                await self._persist()
                return decision
        # make sure the broker has the current price before any order
        await self._feed_prices(extra=intent.symbol)
        conditions = await self._conditions(intent.symbol)
        return await self.engine.execute(intent, conditions)

    async def set_auto_trading(self, enabled: bool) -> None:
        async with self._lock:
            self.engine.set_auto_trading(enabled)
            self._seen_scan = -1
            await self._persist()

    async def exit_position(self) -> None:
        async with self._lock:
            await self._feed_prices()
            await self.engine.exit_position()
            await self._persist()

    async def emergency_stop(self, reason: str, *, close_position: bool | None = None) -> None:
        async with self._lock:
            await self._feed_prices()
            await self.engine.emergency_stop(reason, close_position=close_position)
            await self._persist()
        if self.on_emergency_stop is not None:
            await self.on_emergency_stop(reason)  # pending approvals are cancelled

    async def reset_emergency_stop(self) -> None:
        async with self._lock:
            self.engine.reset_emergency_stop()
            await self._persist()

    # --- read access ----------------------------------------------------------

    async def status(self) -> TradingStatus:
        engine = self.engine
        healthy, detail = await self.health()
        now = self._clock()
        market_open, market_detail = engine.risk.market_open(now)
        entries_allowed, entries_detail = engine.risk.entries_allowed(now)
        return TradingStatus(
            market_open=market_open,
            entries_allowed=entries_allowed,
            market_detail=entries_detail if market_open else market_detail,
            session_mode=engine.risk.limits.session.mode.value,
            mode=self.mode,
            state=engine.state,
            state_since=engine.machine.changed_at,
            auto_trading=engine.auto_trading,
            emergency_stop=engine.emergency_stop_active,
            emergency_reason=engine.emergency_reason,
            active_trade=engine.active.model_copy(deep=True) if engine.active else None,
            last_decision=engine.last_decision,
            balance=engine.broker.balance(),
            positions=[p for p in engine.broker.positions() if p.quantity != 0],
            working_orders=engine.broker.working_orders(),
            risk=engine.risk.snapshot(engine.account_view(), now),
            system_healthy=healthy,
            health_detail=detail,
            persistence_ok=self._persistence_error is None and self._persisted_once,
            persistence_error=self._persistence_error,
            restored_trades_today=self._restored_today,
            interrupted_trades=self._interrupted,
            broker=engine.broker.config,
            entry_timeout_seconds=engine.config.entry_timeout_seconds,
            recent_transitions=engine.machine.history[-20:],
            is_running=self._task is not None and not self._task.done(),
        )


__all__ = ["EngineError", "SignalNotFound", "TradingService", "TradingStatus"]
