"""LiveTradingService: the live safety layer (Phase 14).

    SIGNAL -> RISK (live limits) -> PRE-TRADE VALIDATOR (every check)
           -> LiveOrderManager -> OrderManager (execution gate) -> Zerodha

Live orders need, at the same time:
  1. configuration: EXECUTION_MODE=ZERODHA and LIVE_TRADING_ENABLED=true;
  2. an explicit user action: an ADMIN arms live trading (typed
     confirmation + password), for the current session only;
  3. every pre-trade check passing (PreTradeValidator), including the
     risk engine's approval;
  4. the execution gate (armed, healthy, session open, no kill switch,
     broker connected and reconciled).
If anything fails, no order is placed. The default remains PAPER.

One active live trade at a time (max_active_positions = 1). Every live
action is written to audit_logs. Orders are never retried blindly: ids are
derived from the signal, unknown outcomes are reconciled, not re-sent.

A live trade left open by a previous run (the process restarted) blocks
new live entries until an admin confirms it was handled in Kite.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.database.models.audit_log import AuditLog
from app.database.models.live_trade import LiveTradeRecord
from app.execution.broker import BrokerError
from app.execution.order_manager import OrderManager
from app.live.arming import ArmingError, LiveArming
from app.live.config import LiveConfig
from app.live.health_monitor import BrokerHealthMonitor
from app.live.kill_switch import KillSwitch, KillSwitchResult
from app.live.order_manager import (
    LiveExitReason,
    LiveOrderManager,
    LiveTrade,
    LiveTradeStatus,
    correlation_for,
)
from app.live.validator import PreTradeReport, PreTradeValidator, ValidatorDeps
from app.market_data.base import utcnow
from app.risk.config import RiskLimits
from app.risk.manager import AccountView, RiskManager
from app.risk.schemas import MarketConditions, RiskDecision, TradeIntent

logger = get_logger(__name__)

SessionFactory = Callable[[], AsyncSession]


class LiveTradingError(Exception):
    pass


class LiveTradingService:
    def __init__(
        self,
        *,
        config: LiveConfig,
        limits: RiskLimits,
        orders: OrderManager,
        market_data: Any,
        trading: Any,  # the paper TradingService: stored signals, paper emergency stop
        signals: Any | None,
        health_probe: Callable[[], Awaitable[Any]],
        kite_authenticated: Callable[[], bool],
        tick_size: Callable[[str], float] = lambda _s: 0.05,
        session_factory: SessionFactory | None = None,
        clock: Callable[[], datetime] = utcnow,
        health_interval: float = 5.0,
    ) -> None:
        self.config = config
        self.limits = limits
        self.orders = orders
        self.broker = orders.broker
        self.market_data = market_data
        self.trading = trading
        self.signals = signals
        self._kite_authenticated = kite_authenticated
        self._tick_size = tick_size
        self._session_factory = session_factory
        self._clock = clock
        self.arming = LiveArming(configured=lambda: self.config.configured, clock=clock)
        self.kill_switch = KillSwitch(on_engage=self._on_kill, clock=clock)
        self.monitor = BrokerHealthMonitor(
            health_probe,
            interval=health_interval,
            max_age=config.health_max_age_seconds,
            on_change=self._on_health_change,
        )
        self.risk = RiskManager(limits, capital=0.0, now=clock())
        self.manager = LiveOrderManager(
            orders,
            entry_timeout=timedelta(seconds=config.entry_timeout_seconds),
            square_off_due=self.risk.square_off_due,
            audit=self._system_audit,
            clock=clock,
        )
        self.validator = PreTradeValidator(
            ValidatorDeps(
                config=config,
                limits=limits,
                broker=self.broker,
                risk=self.risk,
                market_data=market_data,
                armed=lambda: self.arming.is_armed,
                kill_switch_engaged=lambda: self.kill_switch.engaged,
                paper_emergency_stop=lambda: bool(
                    getattr(getattr(trading, "engine", None), "emergency_stop_active", False)
                ),
                health_report=lambda: self.monitor.report,
                health_age_seconds=lambda: self.monitor.age_seconds,
                resync_pending=lambda: orders.resync_pending,
                active_trades=lambda: 0 if self.manager.active is None else 1,
                working_order_symbols=lambda: {o.symbol for o in orders.working()},
                unresolved_trade=lambda: self.unresolved,
                executed_signal=lambda sid: sid in self.risk.state.executed_signal_ids,
                clock=clock,
            )
        )
        self.auto_trading = False
        self.unresolved: str | None = None
        self.last_report: PreTradeReport | None = None
        self.last_error: str | None = None
        self._auto_tried: set[uuid.UUID] = set()
        self._seen_scan = -1
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # --- lifecycle -------------------------------------------------------------

    async def start(self) -> None:
        await self._load_unresolved()
        await self.monitor.check()
        await self.monitor.start()
        self._task = asyncio.create_task(self._loop(), name="live-trading")
        logger.info(
            "Live safety layer started: configured=%s (default PAPER; live needs arming)",
            self.config.configured,
        )

    async def stop(self) -> None:
        if self.arming.disarm("shutdown"):
            await self._system_audit("LIVE_DISARMED", {"reason": "shutdown"})
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None
        await self.monitor.stop()

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(self.config.poll_seconds)
            try:
                await self.tick()
                self.last_error = None
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - the loop must keep supervising
                self.last_error = f"{type(exc).__name__}: {exc}"
                logger.exception("Live trading step failed")

    async def tick(self) -> None:
        async with self._lock:
            if self.arming.expired():
                self.arming.disarm("session closed")
                self.auto_trading = False
                await self._system_audit("LIVE_DISARMED", {"reason": "session closed (15:30 IST)"})
            if self.arming.is_armed and not self._kite_authenticated():
                self.arming.disarm("Kite session lost")
                self.auto_trading = False
                await self._system_audit("LIVE_DISARMED", {"reason": "Kite session lost"})
            if self.manager.active is not None:
                before = self.manager.active
                await self.manager.sync()
                if self.manager.active is None and before.status.is_final:
                    self._record_result(before)
                await self._persist(before)
            if self.auto_trading and self.arming.is_armed and self.manager.active is None:
                await self._auto_trade()

    def _record_result(self, trade: LiveTrade) -> None:
        if trade.status == LiveTradeStatus.CLOSED and trade.gross_pnl is not None:
            self.risk.record_trade_result(trade.gross_pnl)

    # --- arming ------------------------------------------------------------------

    def arm_blockers(self) -> list[str]:
        blockers: list[str] = []
        if self.kill_switch.engaged:
            blockers.append("the kill switch is engaged (reset it first)")
        if self.unresolved:
            blockers.append(self.unresolved)
        if not self._kite_authenticated():
            blockers.append("no Kite session (log in to Zerodha first)")
        if self.market_data is None or self.market_data.simulated:
            blockers.append("market data is simulated (MARKET_DATA_MODE=ZERODHA required)")
        return blockers

    async def arm(self, *, user_id: uuid.UUID, email: str) -> dict[str, Any]:
        async with self._lock:
            try:
                state = self.arming.arm(user_id=user_id, email=email, blockers=self.arm_blockers())
            except ArmingError as exc:
                await self._audit(user_id, "LIVE_ARM_REFUSED", {"reason": str(exc)})
                raise LiveTradingError(str(exc)) from exc
            await self._audit(
                user_id,
                "LIVE_ARMED",
                {"expires_at": state.expires_at.isoformat() if state.expires_at else None},
            )
            logger.warning("LIVE TRADING ARMED by %s until %s", email, state.expires_at)
            return state.model_dump(mode="json")

    async def disarm(self, *, user_id: uuid.UUID | None, reason: str) -> None:
        async with self._lock:
            was = self.arming.disarm(reason)
            self.auto_trading = False
            await self._audit(user_id, "LIVE_DISARMED", {"reason": reason, "was_armed": was})

    async def set_auto(self, enabled: bool, *, user_id: uuid.UUID) -> None:
        async with self._lock:
            if enabled and not self.arming.is_armed:
                raise LiveTradingError("Arm live trading before enabling live auto trading")
            self.auto_trading = enabled
            self._seen_scan = -1
            await self._audit(user_id, "LIVE_AUTO_TRADING", {"enabled": enabled})

    # --- evaluation --------------------------------------------------------------

    async def _account(self) -> AccountView:
        try:
            m = await self.broker.get_margins()
            capital, available = max(m.net, 0.0), max(m.available_cash, 0.0)
        except BrokerError:
            capital = available = 0.0
        self.risk.ensure_day(self._clock(), capital)
        if self.risk.state.day_start_capital <= 0 < capital:
            # first account read of the day: the daily loss limit is a
            # percentage of this capital
            self.risk.state.day_start_capital = capital
        active = self.manager.active
        return AccountView(
            capital=capital,
            available_capital=available,
            emergency_stop=self.kill_switch.engaged,
            active_trade=(
                f"{active.side.value} {active.quantity} {active.symbol} ({active.status.value})"
                if active is not None
                else None
            ),
            open_order_symbols={o.symbol for o in self.orders.working()},
        )

    def _conditions(self, symbol: str) -> MarketConditions:
        md = self.market_data
        snap = md.get_snapshot(symbol) if md is not None else None
        stale = None
        if md is None or md.simulated or md.is_stale(symbol):
            stale = f"live market data for {symbol} is not fresh"
        return MarketConditions(
            now=self._clock(),
            last_price=snap.price if snap else None,
            price_timestamp=snap.timestamp if snap else None,
            system_healthy=self.monitor.healthy,
            health_detail=f"broker health {self.monitor.status}",
            trading_mode="LIVE",
            live_authorized=(
                self.config.configured and self.arming.is_armed and not self.kill_switch.engaged
            ),
            data_stale=stale,
        )

    async def evaluate(self, intent: TradeIntent) -> tuple[RiskDecision, PreTradeReport]:
        account = await self._account()
        decision = self.risk.evaluate(intent, self._conditions(intent.symbol), account)
        report = await self.validator.validate(intent, decision)
        return decision, report

    async def preflight(self, signal_id: uuid.UUID) -> tuple[RiskDecision, PreTradeReport]:
        """Every check for a signal, without placing anything."""

        intent, _ = await self.trading.build_intent(signal_id)
        async with self._lock:
            return await self.evaluate(intent)

    # --- execution -----------------------------------------------------------------

    async def execute_signal(
        self, signal_id: uuid.UUID, *, user_id: uuid.UUID | None, source: str = "MANUAL"
    ) -> tuple[PreTradeReport, LiveTrade | None]:
        intent, _ = await self.trading.build_intent(signal_id)
        async with self._lock:
            return await self._execute_locked(intent, user_id=user_id, source=source)

    async def _execute_locked(
        self, intent: TradeIntent, *, user_id: uuid.UUID | None, source: str
    ) -> tuple[PreTradeReport, LiveTrade | None]:
        decision, report = await self.evaluate(intent)
        self.last_report = report
        await self._audit(
            user_id,
            "LIVE_PRETRADE_CHECK",
            {
                "source": source,
                "signal_id": str(intent.signal_id) if intent.signal_id else None,
                "symbol": intent.symbol,
                "side": intent.side.value,
                "ok": report.ok,
                "quantity": report.quantity,
                "failed": [f"{c.name}: {c.detail}" for c in report.failed],
            },
        )
        if not report.ok or decision.size is None:
            logger.info("Live entry for %s blocked: %s", intent.symbol, report.summary)
            return report, None
        self.risk.record_signal_attempt(intent.signal_id)
        trade = LiveTrade(
            correlation_id=correlation_for(intent.signal_id),
            signal_id=intent.signal_id,
            symbol=intent.symbol,
            side=intent.side,
            quantity=decision.size.quantity,
            planned_entry=intent.entry,
            stop_loss=intent.stop_loss,
            target=intent.target,
            tick_size=self._tick_size(intent.symbol),
            opened_at=self._clock(),
            armed_by=self.arming.state.armed_by,
        )
        trade = await self.manager.open_trade(trade)
        if trade.status != LiveTradeStatus.FAILED:
            self.risk.record_entry(intent.signal_id)
        await self._persist(trade)
        return report, trade

    async def _auto_trade(self) -> None:
        if self.signals is None:
            return
        status = self.signals.status()
        if status.scan_count == self._seen_scan:
            return
        self._seen_scan = status.scan_count
        scan = self.signals.get_scan()
        if scan is None:
            return
        for candidate in scan.candidates:
            if candidate.status != "READY" or candidate.signal_id is None:
                continue
            if candidate.signal_id in self._auto_tried:
                continue
            self._auto_tried.add(candidate.signal_id)
            try:
                intent, _ = await self.trading.build_intent(candidate.signal_id)
            except Exception:  # noqa: BLE001 - the signal vanished; try the next one
                continue
            await self._execute_locked(intent, user_id=None, source="AUTO")
            break  # one attempt per scan; one active trade at a time

    # --- exits, kill switch, recovery ------------------------------------------------

    async def request_exit(self, *, user_id: uuid.UUID) -> bool:
        async with self._lock:
            ok = self.manager.request_exit(LiveExitReason.MANUAL)
            await self._audit(user_id, "LIVE_EXIT_REQUESTED", {"accepted": ok})
            return ok

    async def kill(
        self, *, reason: str, user_id: uuid.UUID | None, close_positions: bool | None
    ) -> KillSwitchResult:
        close = self.config.close_positions_on_kill if close_positions is None else close_positions
        async with self._lock:
            result = await self.kill_switch.engage(
                reason=reason, actor=user_id, close_positions=close
            )
            await self._audit(
                user_id,
                "LIVE_KILL_SWITCH",
                {
                    "reason": reason,
                    "close_positions": close,
                    "was_armed": result.was_armed,
                    "entry_cancelled": result.entry_cancelled,
                    "exit_requested": result.exit_requested,
                },
            )
        logger.critical("LIVE KILL SWITCH engaged: %s", reason)
        if self.trading is not None and not self.trading.engine.emergency_stop_active:
            try:
                await self.trading.emergency_stop(f"kill switch: {reason}", close_position=False)
            except Exception:  # noqa: BLE001
                logger.exception("Paper emergency stop from the kill switch failed")
        return result

    async def _on_kill(self, reason: str, close_positions: bool) -> tuple[bool, bool, bool]:
        was_armed = self.arming.disarm(f"kill switch: {reason}")
        self.auto_trading = False
        cancelled = await self.manager.cancel_pending_entry("kill switch")
        exiting = False
        if close_positions:
            exiting = self.manager.request_exit(LiveExitReason.KILL_SWITCH)
        if self.manager.active is not None:
            await self._persist(self.manager.active)
        return was_armed, cancelled, exiting

    async def on_paper_emergency_stop(self, reason: str) -> None:
        """The paper emergency stop (dashboard) also stops live trading."""

        if not self.kill_switch.engaged:
            await self.kill(reason=f"emergency stop: {reason}", user_id=None, close_positions=None)

    async def reset_kill(self, *, user_id: uuid.UUID) -> None:
        async with self._lock:
            previous = self.kill_switch.reset()
            await self._audit(
                user_id,
                "LIVE_KILL_SWITCH_RESET",
                {"previous_reason": previous.reason, "rearmed": False},
            )

    async def acknowledge_unresolved(self, *, user_id: uuid.UUID, note: str) -> None:
        async with self._lock:
            previous = self.unresolved
            self.unresolved = None
            if self._session_factory is not None:
                async with self._session_factory() as session:
                    rows = (
                        await session.execute(
                            select(LiveTradeRecord).where(
                                LiveTradeRecord.status.in_(
                                    [s.value for s in LiveTradeStatus if not s.is_final]
                                )
                            )
                        )
                    ).scalars()
                    for row in rows:
                        row.status = LiveTradeStatus.CANCELLED.value
                        row.exit_reason = "ACKNOWLEDGED_AFTER_RESTART"
                        row.closed_at = self._clock()
                    await session.commit()
            await self._audit(
                user_id, "LIVE_RECOVERY_ACKNOWLEDGED", {"previous": previous, "note": note}
            )

    async def _on_health_change(self, previous: str | None, current: str) -> None:
        if self.arming.is_armed and previous is not None:
            await self._system_audit("LIVE_HEALTH_CHANGED", {"from": previous, "to": current})
            if current == "AUTH_REQUIRED":
                self.arming.disarm("Kite authentication required")
                self.auto_trading = False
                await self._system_audit(
                    "LIVE_DISARMED", {"reason": "Kite authentication required"}
                )

    # --- persistence & audit ------------------------------------------------------------

    async def _load_unresolved(self) -> None:
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                rows = (
                    (
                        await session.execute(
                            select(LiveTradeRecord).where(
                                LiveTradeRecord.status.in_(
                                    [s.value for s in LiveTradeStatus if not s.is_final]
                                )
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
        except Exception:  # noqa: BLE001 - without the database live cannot be armed anyway
            logger.warning("Could not check for unresolved live trades")
            self.unresolved = "live trade storage unavailable"
            return
        if rows:
            names = ", ".join(f"{r.side} {r.quantity} {r.symbol} ({r.status})" for r in rows)
            self.unresolved = (
                f"live trade(s) from a previous run: {names}. Check positions and orders in "
                "Kite, then acknowledge"
            )
            logger.critical("Unresolved live trades after restart: %s", names)

    async def _persist(self, trade: LiveTrade) -> None:
        if self._session_factory is None:
            return
        row = {
            "id": trade.id,
            "correlation_id": trade.correlation_id,
            "signal_id": trade.signal_id,
            "symbol": trade.symbol,
            "side": trade.side.value,
            "status": trade.status.value,
            "quantity": trade.quantity,
            "filled_quantity": trade.filled_quantity,
            "planned_entry": trade.planned_entry,
            "stop_loss": trade.stop_loss,
            "target": trade.target,
            "average_entry": trade.average_entry,
            "average_exit": trade.average_exit,
            "gross_pnl": trade.gross_pnl,
            "exit_reason": trade.exit_reason.value if trade.exit_reason else None,
            "opened_at": trade.opened_at,
            "closed_at": trade.closed_at,
            "armed_by": trade.armed_by,
            "snapshot": trade.model_dump(mode="json"),
        }
        try:
            async with self._session_factory() as session:
                stmt = insert(LiveTradeRecord).values(row)
                await session.execute(
                    stmt.on_conflict_do_update(
                        index_elements=["id"],
                        set_={k: v for k, v in row.items() if k != "id"},
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001 - the audit log still records every action
            logger.exception("Live trade %s not stored", trade.correlation_id)

    async def _system_audit(self, action: str, details: dict[str, Any]) -> None:
        await self._audit(None, action, details)

    async def _audit(self, user_id: uuid.UUID | None, action: str, details: dict[str, Any]) -> None:
        logger.info("AUDIT %s %s", action, details)
        if self._session_factory is None:
            return
        try:
            async with self._session_factory() as session:
                session.add(
                    AuditLog(
                        user_id=user_id,
                        action=action,
                        before=None,
                        after={**details, "at": self._clock().isoformat()},
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001
            logger.error("AUDIT WRITE FAILED for %s %s", action, details)

    # --- read --------------------------------------------------------------------------

    def status(self) -> dict[str, Any]:
        active = self.manager.active
        return {
            "configured": self.config.configured,
            "execution_mode": "ZERODHA" if self.config.configured else "PAPER",
            "arming": self.arming.state.model_dump(mode="json"),
            "auto_trading": self.auto_trading,
            "kill_switch": self.kill_switch.state.model_dump(mode="json"),
            "health": {
                "status": self.monitor.status,
                "age_seconds": (
                    None if self.monitor.age_seconds is None else round(self.monitor.age_seconds, 1)
                ),
                "checks": (
                    [c.model_dump() for c in self.monitor.report.checks]
                    if self.monitor.report is not None
                    else []
                ),
            },
            "max_active_positions": self.config.max_active_positions,
            "active_trade": active.model_dump(mode="json") if active else None,
            "recent_trades": [
                t.model_dump(mode="json") for t in reversed(self.manager.closed[-10:])
            ],
            "unresolved": self.unresolved,
            "arm_blockers": self.arm_blockers(),
            "last_report": self.last_report.model_dump(mode="json") if self.last_report else None,
            "last_error": self.last_error,
            "limits": {
                "max_position_value": self.config.max_position_value,
                "max_order_quantity": self.config.max_order_quantity,
                "max_stop_percent": self.config.max_stop_percent,
                "min_risk_reward": self.config.min_risk_reward,
                "entry_timeout_seconds": self.config.entry_timeout_seconds,
                "close_positions_on_kill": self.config.close_positions_on_kill,
                "risk_per_trade_percent": self.limits.risk_per_trade_percent,
                "daily_loss_limit_percent": self.limits.daily_loss_limit_percent,
                "last_entry_time": self.limits.session.last_entry_time.strftime("%H:%M"),
                "square_off_time": self.limits.session.square_off_time.strftime("%H:%M"),
            },
        }


__all__ = ["LiveTradingError", "LiveTradingService"]
