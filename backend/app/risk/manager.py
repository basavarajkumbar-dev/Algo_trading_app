"""RiskManager.

`evaluate` runs every check and reports every failure, not just the
first, so a rejected trade explains itself completely. It also owns
today's counters (trades taken, realized P&L, consecutive losses) and
the market-session rules (session hours, entry cut-off, square-off).

Calculations:

    risk amount       = capital x risk per trade %
    position size     = risk amount / |entry - stop| (see position_sizing)
    maximum exposure  = open position value + this trade's value
    daily loss        = realized P&L today, net of charges
    trade count       = entries taken today (a cancelled, unfilled entry
                        does not count)
    consecutive losses = closing losses in a row today; a win or a
                        break-even trade resets the run

The daily loss check is two-sided: no new entry once today's realized
loss reaches the limit, and a new trade's quantity is reduced so that
its planned risk fits inside what is left of the limit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from app.risk.config import RiskLimits, SessionMode
from app.risk.position_sizing import calculate_position_size, floor_to_lot
from app.risk.schemas import (
    MarketConditions,
    PositionSize,
    RejectionReason,
    RiskCheck,
    RiskDecision,
    RiskSnapshot,
    RiskState,
    TradeIntent,
    TradeSide,
)

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class AccountView:
    """The engine's account and position facts at evaluation time."""

    capital: float  # equity used for the risk amount
    available_capital: float  # cash not tied up as margin
    open_exposure: float = 0.0  # value of open positions
    emergency_stop: bool = False
    active_trade: str | None = None  # description of the active trade, if any
    open_order_symbols: set[str] = field(default_factory=set)
    estimated_charges: float = 0.0  # round-trip charges for this trade, if known


class RiskManager:
    def __init__(self, limits: RiskLimits, *, capital: float, now: datetime) -> None:
        self.limits = limits
        self.state = RiskState(trading_day=self.trading_day(now), day_start_capital=capital)

    # --- session ----------------------------------------------------------

    @staticmethod
    def trading_day(now: datetime):  # noqa: ANN205 - returns a date
        return now.astimezone(IST).date()

    def market_open(self, now: datetime) -> tuple[bool, str]:
        s = self.limits.session
        if s.mode == SessionMode.SIMULATED_24H:
            return True, "simulated 24h session (exchange hours not enforced)"
        local = now.astimezone(IST)
        if local.weekday() >= 5:
            return False, f"weekend ({local:%A})"
        if local.date() in s.holidays:
            return False, f"exchange holiday {local.date()}"
        if not (s.open_time <= local.time() < s.close_time):
            return False, (
                f"{local:%H:%M} IST is outside {s.open_time:%H:%M}-{s.close_time:%H:%M} IST"
            )
        return True, f"{local:%H:%M} IST, session open"

    def entries_allowed(self, now: datetime) -> tuple[bool, str]:
        is_open, detail = self.market_open(now)
        if not is_open:
            return False, f"market closed: {detail}"
        s = self.limits.session
        if s.mode == SessionMode.EXCHANGE:
            local = now.astimezone(IST)
            if local.time() >= s.last_entry_time:
                return False, (
                    f"no new entries at or after {s.last_entry_time:%H:%M} IST "
                    f"(now {local:%H:%M})"
                )
        return True, detail

    def square_off_due(self, now: datetime) -> bool:
        s = self.limits.session
        if s.mode == SessionMode.SIMULATED_24H:
            return False
        local = now.astimezone(IST)
        return local.time() >= s.square_off_time or local.time() < s.open_time

    # --- daily state ------------------------------------------------------

    def ensure_day(self, now: datetime, capital: float) -> bool:
        """Start a new day's counters when the IST date changes."""

        day = self.trading_day(now)
        if day != self.state.trading_day:
            self.state = RiskState(trading_day=day, day_start_capital=capital)
            return True
        return False

    @property
    def daily_loss_limit_amount(self) -> float:
        return round(self.state.day_start_capital * self.limits.daily_loss_limit_percent / 100, 2)

    @property
    def daily_loss_remaining(self) -> float:
        return round(
            max(self.daily_loss_limit_amount + min(self.state.realized_pnl_today, 0), 0), 2
        )

    def record_entry(self, signal_id: uuid.UUID | None) -> None:
        self.state.trades_today += 1
        if signal_id is not None and signal_id not in self.state.executed_signal_ids:
            self.state.executed_signal_ids.append(signal_id)

    def record_signal_attempt(self, signal_id: uuid.UUID | None) -> None:
        """An order was placed for this signal: never place another for it."""

        if signal_id is not None and signal_id not in self.state.executed_signal_ids:
            self.state.executed_signal_ids.append(signal_id)

    def record_trade_result(self, net_pnl: float) -> None:
        self.state.realized_pnl_today = round(self.state.realized_pnl_today + net_pnl, 2)
        if net_pnl < 0:
            self.state.consecutive_losses += 1
            self.state.losses_today += 1
        else:
            self.state.consecutive_losses = 0
            self.state.wins_today += 1

    # --- account-level blocks (independent of any one trade) --------------

    def account_blocks(self) -> list[tuple[RejectionReason, str]]:
        blocks: list[tuple[RejectionReason, str]] = []
        st, lim = self.state, self.limits
        if st.realized_pnl_today <= -self.daily_loss_limit_amount:
            blocks.append(
                (
                    RejectionReason.DAILY_LOSS_LIMIT,
                    f"realized P&L today ₹{st.realized_pnl_today:,.2f} has reached the daily "
                    f"loss limit ₹{self.daily_loss_limit_amount:,.2f} "
                    f"({lim.daily_loss_limit_percent}%)",
                )
            )
        if st.trades_today >= lim.max_trades_per_day:
            blocks.append(
                (
                    RejectionReason.MAX_TRADES_REACHED,
                    f"{st.trades_today} of {lim.max_trades_per_day} trades taken today",
                )
            )
        if st.consecutive_losses >= lim.max_consecutive_losses:
            blocks.append(
                (
                    RejectionReason.MAX_CONSECUTIVE_LOSSES,
                    f"{st.consecutive_losses} consecutive losses "
                    f"(limit {lim.max_consecutive_losses})",
                )
            )
        return blocks

    # --- evaluation ---------------------------------------------------------

    def evaluate(
        self, intent: TradeIntent, conditions: MarketConditions, account: AccountView
    ) -> RiskDecision:
        lim = self.limits
        now = conditions.now
        self.ensure_day(now, account.capital)
        checks: list[RiskCheck] = []

        def check(name: str, ok: bool, detail: str, reason: RejectionReason) -> None:
            checks.append(
                RiskCheck(name=name, passed=ok, detail=detail, reason=None if ok else reason)
            )

        mode = conditions.trading_mode
        check(
            "trading_mode",
            mode in ("PAPER", "APPROVAL"),
            f"mode {mode}"
            + ("" if mode in ("PAPER", "APPROVAL") else "; only paper execution exists"),
            RejectionReason.TRADING_MODE_NOT_PAPER,
        )
        if mode == "APPROVAL":
            approved = intent.approval_id is not None or conditions.preview
            check(
                "approval",
                approved,
                (
                    "previewed for approval"
                    if conditions.preview
                    else (
                        f"approved request {intent.approval_id}"
                        if intent.approval_id
                        else "APPROVAL mode: a person must approve this trade first"
                    )
                ),
                RejectionReason.APPROVAL_REQUIRED,
            )
        check(
            "emergency_stop",
            not account.emergency_stop,
            "emergency stop is ACTIVE" if account.emergency_stop else "not active",
            RejectionReason.EMERGENCY_STOP,
        )
        check(
            "system_health",
            conditions.system_healthy,
            conditions.health_detail or ("healthy" if conditions.system_healthy else "unhealthy"),
            RejectionReason.SYSTEM_UNHEALTHY,
        )
        ok, detail = self.entries_allowed(now)
        check("market_hours", ok, detail, RejectionReason.MARKET_CLOSED)

        # data freshness: the live price and the signal itself
        stale: list[str] = []
        if conditions.last_price is None or conditions.price_timestamp is None:
            stale.append(f"no price for {intent.symbol}")
        else:
            age = (now - conditions.price_timestamp).total_seconds()
            if age > lim.max_data_age_seconds:
                stale.append(f"last price is {age:.0f}s old (max {lim.max_data_age_seconds:.0f}s)")
        if not intent.signal_active:
            stale.append("signal has expired")
        elif intent.signal_last_seen_at is not None:
            sig_age = (now - intent.signal_last_seen_at).total_seconds()
            if sig_age > lim.max_signal_age_seconds:
                stale.append(
                    f"signal last confirmed {sig_age:.0f}s ago (max {lim.max_signal_age_seconds:.0f}s)"
                )
        check(
            "data_freshness",
            not stale,
            "; ".join(stale) if stale else "price and signal are current",
            RejectionReason.DATA_STALE,
        )

        check(
            "one_active_trade",
            account.active_trade is None,
            (
                f"a trade is already active: {account.active_trade}"
                if account.active_trade
                else "no active trade"
            ),
            RejectionReason.ACTIVE_POSITION_EXISTS,
        )
        dup: list[str] = []
        if intent.signal_id is not None and intent.signal_id in self.state.executed_signal_ids:
            dup.append(f"signal {intent.signal_id} was already sent as an order today")
        if intent.symbol in account.open_order_symbols:
            dup.append(f"an order for {intent.symbol} is already open")
        check(
            "duplicate_order",
            not dup,
            "; ".join(dup) if dup else "no matching order",
            RejectionReason.DUPLICATE_ORDER,
        )

        blocks = {reason: text for reason, text in self.account_blocks()}
        for name, reason, fine in (
            (
                "daily_loss_limit",
                RejectionReason.DAILY_LOSS_LIMIT,
                f"realized today ₹{self.state.realized_pnl_today:,.2f}; "
                f"₹{self.daily_loss_remaining:,.2f} of ₹{self.daily_loss_limit_amount:,.2f} left",
            ),
            (
                "max_trades",
                RejectionReason.MAX_TRADES_REACHED,
                f"{self.state.trades_today} of {lim.max_trades_per_day} trades taken today",
            ),
            (
                "consecutive_losses",
                RejectionReason.MAX_CONSECUTIVE_LOSSES,
                f"{self.state.consecutive_losses} in a row (limit {lim.max_consecutive_losses})",
            ),
        ):
            check(name, reason not in blocks, blocks.get(reason, fine), reason)

        if intent.score is not None:
            check(
                "signal_score",
                intent.score >= lim.min_signal_score,
                f"score {intent.score:.1f}, executable threshold {lim.min_signal_score:.0f}",
                RejectionReason.SIGNAL_NOT_EXECUTABLE,
            )
        if intent.side == TradeSide.SHORT:
            check(
                "short_allowed",
                lim.allow_short,
                "short selling allowed" if lim.allow_short else "short selling is disabled",
                RejectionReason.SHORT_NOT_ALLOWED,
            )

        # prices: validate against the price the order would actually see
        reference = conditions.last_price if conditions.last_price else intent.entry
        long = intent.side == TradeSide.LONG
        stop_ok = (
            intent.stop_loss > 0
            and (intent.stop_loss < reference if long else intent.stop_loss > reference)
            and (intent.stop_loss < intent.entry if long else intent.stop_loss > intent.entry)
            and abs(reference - intent.stop_loss) >= lim.tick_size
        )
        check(
            "stop_validity",
            stop_ok,
            (
                f"stop {intent.stop_loss:.2f} is {'below' if long else 'above'} entry "
                f"{intent.entry:.2f} and price {reference:.2f}"
                if stop_ok
                else f"stop {intent.stop_loss:.2f} is not on the loss side of entry "
                f"{intent.entry:.2f} / price {reference:.2f} by at least one tick"
            ),
            RejectionReason.INVALID_STOP,
        )
        target_ok = intent.target > reference if long else 0 < intent.target < reference
        check(
            "target_validity",
            target_ok,
            f"target {intent.target:.2f} vs price {reference:.2f}"
            + ("" if target_ok else ": not on the profit side"),
            RejectionReason.INVALID_TARGET,
        )
        planned_risk = abs(intent.entry - intent.stop_loss)
        adverse = (reference - intent.entry) if long else (intent.entry - reference)
        allowed = lim.max_entry_deviation_r * planned_risk
        check(
            "entry_deviation",
            adverse <= allowed + 1e-9,
            f"price {reference:.2f} is {max(adverse, 0):.2f} worse than planned entry "
            f"{intent.entry:.2f} (allowed {allowed:.2f} = {lim.max_entry_deviation_r}R)",
            RejectionReason.PRICE_MOVED_AWAY,
        )

        size: PositionSize | None = None
        if stop_ok:
            size = calculate_position_size(
                capital=account.capital,
                available_capital=account.available_capital,
                entry=reference,
                stop_loss=intent.stop_loss,
                side=intent.side,
                limits=lim,
            )
            # Fit the trade's planned risk inside what is left of today's loss limit.
            remaining = self.daily_loss_remaining
            if size.actual_risk > remaining and size.risk_per_unit > 0:
                qty = floor_to_lot(remaining / size.risk_per_unit, lim.lot_size)
                size = size.model_copy(
                    update={
                        "quantity": qty,
                        "actual_risk": round(qty * size.risk_per_unit, 2),
                        "position_value": round(qty * size.entry, 2),
                        "constraints": [*size.constraints, "capped by remaining daily loss"],
                    }
                )
            # An approved request never executes more shares than were approved.
            if intent.max_quantity is not None and size.quantity > intent.max_quantity:
                qty = floor_to_lot(intent.max_quantity, lim.lot_size)
                size = size.model_copy(
                    update={
                        "quantity": qty,
                        "actual_risk": round(qty * size.risk_per_unit, 2),
                        "position_value": round(qty * size.entry, 2),
                        "constraints": [*size.constraints, "capped at the approved quantity"],
                    }
                )
            qty_ok = size.quantity >= lim.lot_size and size.quantity % lim.lot_size == 0
            check(
                "quantity_validity",
                qty_ok,
                (
                    f"{size.quantity} shares (raw {size.raw_quantity:.2f}"
                    + (f"; {', '.join(size.constraints)}" if size.constraints else "")
                    + ")"
                ),
                RejectionReason.INVALID_QUANTITY,
            )
            needed = size.position_value / lim.margin_multiplier + account.estimated_charges
            check(
                "sufficient_capital",
                needed <= account.available_capital + 1e-6 and size.quantity > 0,
                f"needs ₹{needed:,.2f}, available ₹{account.available_capital:,.2f}",
                RejectionReason.INSUFFICIENT_CAPITAL,
            )
            exposure = account.open_exposure + size.position_value
            check(
                "max_exposure",
                exposure <= lim.max_exposure + 1e-6,
                f"exposure would be ₹{exposure:,.2f} (limit ₹{lim.max_exposure:,.2f})",
                RejectionReason.MAX_EXPOSURE,
            )
        else:
            check(
                "quantity_validity",
                False,
                "no quantity without a valid stop",
                RejectionReason.INVALID_QUANTITY,
            )

        reasons: list[RejectionReason] = []
        for c in checks:
            if c.reason is not None and c.reason not in reasons:
                reasons.append(c.reason)
        return RiskDecision(
            approved=not reasons,
            intent=intent,
            size=size,
            checks=checks,
            rejection_reasons=reasons,
            evaluated_at=now,
        )

    # --- reporting ----------------------------------------------------------

    def snapshot(self, account: AccountView, now: datetime) -> RiskSnapshot:
        self.ensure_day(now, account.capital)
        blocked = [reason for reason, _ in self.account_blocks()]
        if account.emergency_stop:
            blocked.insert(0, RejectionReason.EMERGENCY_STOP)
        allowed, _ = self.entries_allowed(now)
        if not allowed:
            blocked.append(RejectionReason.MARKET_CLOSED)
        if account.active_trade:
            blocked.append(RejectionReason.ACTIVE_POSITION_EXISTS)
        return RiskSnapshot(
            state=self.state.model_copy(deep=True),
            capital=round(account.capital, 2),
            available_capital=round(account.available_capital, 2),
            open_exposure=round(account.open_exposure, 2),
            daily_loss_limit_amount=self.daily_loss_limit_amount,
            daily_loss_remaining=self.daily_loss_remaining,
            risk_per_trade_amount=round(
                account.capital * self.limits.risk_per_trade_percent / 100, 2
            ),
            trades_remaining=max(self.limits.max_trades_per_day - self.state.trades_today, 0),
            consecutive_losses_remaining=max(
                self.limits.max_consecutive_losses - self.state.consecutive_losses, 0
            ),
            entries_blocked=bool(blocked),
            blocked_reasons=blocked,
        )
