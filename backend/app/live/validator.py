"""PreTradeValidator: every check that must pass before a live entry.

If ANY check fails, no order is placed. The checks:

  live_configured       EXECUTION_MODE=ZERODHA and LIVE_TRADING_ENABLED=true
  armed                 an admin armed live trading (explicit action)
  broker_connected      Zerodha CONNECTED and reconciled
  market_open           NSE session, Mon-Fri, before the last entry time
  market_data_healthy   real Zerodha data, LIVE, this symbol fresh
  websocket_healthy     the Kite ticker is connected
  database_healthy      PostgreSQL reachable (recent health report)
  redis_healthy         Redis reachable (recent health report)
  broker_health         the broker health monitor reports HEALTHY
  risk_engine_healthy   risk engine running for today, no account block
  no_emergency_stop     kill switch and paper emergency stop off
  no_unresolved_trade   no live trade left over from a previous run
  risk_limits_valid     the configured limits are sane for live
  risk_approved         the RiskManager approved this trade (all its checks)
  account_balance       available cash at the broker covers the position
  no_duplicate_position no open position or working order at the broker,
                        and fewer than max_active_positions (1) trades
  no_stale_signal       the signal is active, recent and not already traded
  valid_quantity        positive, within the live quantity and value caps
  valid_stop            right side of entry, at least a tick, within the cap
  valid_target          right side of entry, risk/reward at least the minimum
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.execution.broker import BrokerAdapter, BrokerError
from app.execution.models import ConnectionState
from app.live.config import LiveConfig
from app.risk.config import RiskLimits
from app.risk.manager import RiskManager
from app.risk.schemas import RiskDecision, TradeIntent, TradeSide

IST = ZoneInfo("Asia/Kolkata")


class PreTradeCheck(BaseModel):
    name: str
    passed: bool
    detail: str


class PreTradeReport(BaseModel):
    ok: bool
    symbol: str
    quantity: int | None
    checked_at: datetime
    checks: list[PreTradeCheck] = Field(default_factory=list)

    @property
    def failed(self) -> list[PreTradeCheck]:
        return [c for c in self.checks if not c.passed]

    @property
    def summary(self) -> str:
        if self.ok:
            return f"all {len(self.checks)} pre-trade checks passed"
        return "blocked: " + "; ".join(f"{c.name} ({c.detail})" for c in self.failed)


@dataclass
class ValidatorDeps:
    """What the validator reads. Callables, so each check sees current state."""

    config: LiveConfig
    limits: RiskLimits
    broker: BrokerAdapter
    risk: RiskManager
    market_data: Any  # MarketDataService
    armed: Callable[[], bool]
    kill_switch_engaged: Callable[[], bool]
    paper_emergency_stop: Callable[[], bool]
    health_report: Callable[[], Any]  # latest BrokerHealthReport or None
    health_age_seconds: Callable[[], float | None]
    resync_pending: Callable[[], bool]
    active_trades: Callable[[], int]
    working_order_symbols: Callable[[], set[str]]
    unresolved_trade: Callable[[], str | None]
    executed_signal: Callable[[Any], bool]
    clock: Callable[[], datetime]


class PreTradeValidator:
    def __init__(self, deps: ValidatorDeps) -> None:
        self.d = deps

    async def validate(self, intent: TradeIntent, decision: RiskDecision | None) -> PreTradeReport:
        d = self.d
        now = d.clock()
        checks: list[PreTradeCheck] = []

        def add(name: str, ok: bool, detail: str) -> None:
            checks.append(PreTradeCheck(name=name, passed=bool(ok), detail=detail))

        add(
            "live_configured",
            d.config.configured,
            (
                "EXECUTION_MODE=ZERODHA, LIVE_TRADING_ENABLED=true"
                if d.config.configured
                else "live trading is not configured (PAPER)"
            ),
        )
        add("armed", d.armed(), "armed by an admin" if d.armed() else "live trading is not armed")

        state = d.broker.connection_state
        add(
            "broker_connected",
            state is ConnectionState.CONNECTED and not d.resync_pending(),
            f"Zerodha {state.value}"
            + (" (waiting for reconciliation)" if d.resync_pending() else ""),
        )

        local = now.astimezone(IST)
        s = d.limits.session
        in_session = (
            local.weekday() < 5
            and local.date() not in s.holidays
            and s.open_time <= local.time() < s.last_entry_time
        )
        add(
            "market_open",
            in_session,
            f"{local:%a %H:%M} IST; entries {s.open_time:%H:%M}-{s.last_entry_time:%H:%M}",
        )

        md = d.market_data
        real = md is not None and not md.simulated
        status = md.data_status() if md is not None else "DISCONNECTED"
        symbol_fresh = md is not None and not md.is_stale(intent.symbol)
        add(
            "market_data_healthy",
            real and status == "LIVE" and symbol_fresh,
            (
                "simulated data cannot be traded live"
                if not real
                else f"feed {status}; {intent.symbol} " + ("fresh" if symbol_fresh else "stale")
            ),
        )
        details = md.provider.status_details() if md is not None else {}
        ws = str(details.get("websocket", "N/A"))
        add("websocket_healthy", real and ws == "CONNECTED", f"Kite WebSocket {ws}")

        report = d.health_report()
        age = d.health_age_seconds()
        fresh = report is not None and age is not None and age <= d.config.health_max_age_seconds
        by = {c.name: c for c in report.checks} if report is not None else {}

        def component(name: str) -> tuple[bool, str]:
            c = by.get(name)
            if not fresh or c is None:
                return False, "no recent health report"
            return c.ok, c.status

        ok, detail = component("database")
        add("database_healthy", ok, detail)
        ok, detail = component("redis")
        add("redis_healthy", ok, detail)
        add(
            "broker_health",
            fresh and report is not None and report.status == "HEALTHY",
            f"{report.status if report else 'unknown'}"
            + (f", {age:.0f}s old" if age is not None else ""),
        )

        blocks = d.risk.account_blocks()
        add(
            "risk_engine_healthy",
            not blocks and d.risk.state.trading_day == d.risk.trading_day(now),
            "; ".join(t for _, t in blocks) if blocks else "running, no account block",
        )
        stop_on = d.kill_switch_engaged() or d.paper_emergency_stop()
        add(
            "no_emergency_stop",
            not stop_on,
            "kill switch / emergency stop is ACTIVE" if stop_on else "not active",
        )
        leftover = d.unresolved_trade()
        add(
            "no_unresolved_trade",
            leftover is None,
            leftover or "no live trade left from a previous run",
        )

        lim = d.limits
        sane = (
            0 < lim.risk_per_trade_percent <= 2
            and 0 < lim.daily_loss_limit_percent <= 5
            and lim.max_position_value <= d.config.max_position_value
            and lim.max_order_quantity <= d.config.max_order_quantity
            and d.config.max_active_positions == 1
            and lim.margin_multiplier == 1.0
            and s.mode.value == "EXCHANGE"
        )
        add(
            "risk_limits_valid",
            sane,
            f"risk {lim.risk_per_trade_percent}%/trade, daily loss {lim.daily_loss_limit_percent}%, "
            f"max ₹{lim.max_position_value:,.0f}, max {d.config.max_active_positions} position",
        )

        add(
            "risk_approved",
            decision is not None and decision.approved,
            decision.summary if decision is not None else "not evaluated",
        )
        qty = decision.size.quantity if decision is not None and decision.size else 0
        entry = intent.entry
        need = qty * entry
        try:
            margins = await d.broker.get_margins()
            cash = margins.available_cash
            add(
                "account_balance",
                qty > 0 and cash >= need,
                f"available ₹{cash:,.2f}, needed ₹{need:,.2f}",
            )
        except BrokerError as exc:
            add("account_balance", False, f"margins unavailable: {exc}")

        dup: list[str] = []
        try:
            positions = await d.broker.get_positions()
            open_pos = [p for p in positions if p.quantity != 0]
            if any(p.symbol == intent.symbol for p in open_pos):
                dup.append(f"an open {intent.symbol} position exists at the broker")
            if len(open_pos) + d.active_trades() >= d.config.max_active_positions:
                dup.append(
                    f"{len(open_pos)} open position(s) + {d.active_trades()} live trade(s); "
                    f"max {d.config.max_active_positions}"
                )
        except BrokerError as exc:
            dup.append(f"positions unavailable: {exc}")
        if intent.symbol in d.working_order_symbols():
            dup.append(f"a working {intent.symbol} order exists")
        add("no_duplicate_position", not dup, "; ".join(dup) or "no position, no working order")

        stale: list[str] = []
        if not intent.signal_active:
            stale.append("signal is no longer active")
        if intent.signal_last_seen_at is None:
            stale.append("signal has no scan time")
        else:
            sig_age = (now - intent.signal_last_seen_at).total_seconds()
            if sig_age > d.config.signal_max_age_seconds:
                stale.append(
                    f"last confirmed {sig_age:.0f}s ago (max {d.config.signal_max_age_seconds:.0f}s)"
                )
        if intent.signal_id is not None and d.executed_signal(intent.signal_id):
            stale.append("signal was already traded")
        add("no_stale_signal", not stale, "; ".join(stale) or "active and current")

        value = qty * entry
        add(
            "valid_quantity",
            0 < qty <= d.config.max_order_quantity and value <= d.config.max_position_value,
            f"{qty} × ₹{entry:,.2f} = ₹{value:,.2f} (caps {d.config.max_order_quantity} / "
            f"₹{d.config.max_position_value:,.0f})",
        )
        long = intent.side == TradeSide.LONG
        risk_per_share = (entry - intent.stop_loss) if long else (intent.stop_loss - entry)
        stop_pct = risk_per_share / entry * 100 if entry else 0.0
        add(
            "valid_stop",
            risk_per_share >= lim.tick_size and stop_pct <= d.config.max_stop_percent,
            f"stop {intent.stop_loss:.2f}, {stop_pct:.2f}% from entry "
            f"(max {d.config.max_stop_percent}%)",
        )
        reward = (intent.target - entry) if long else (entry - intent.target)
        rr = reward / risk_per_share if risk_per_share > 0 else 0.0
        add(
            "valid_target",
            reward >= lim.tick_size and rr >= d.config.min_risk_reward,
            f"target {intent.target:.2f}, risk/reward {rr:.2f} (min {d.config.min_risk_reward})",
        )

        return PreTradeReport(
            ok=all(c.passed for c in checks),
            symbol=intent.symbol,
            quantity=qty or None,
            checked_at=now,
            checks=checks,
        )


__all__ = ["PreTradeCheck", "PreTradeReport", "PreTradeValidator", "ValidatorDeps"]
