"""Phase 14 live safety layer, against the fake Kite REST server (no network,
no money): arming, every pre-trade check, the live trade lifecycle (entry ->
stop + target -> exit), one-cancels-other, kill switch, entry timeout,
square-off, unknown order outcomes (never re-sent), one active trade, and
that nothing is sent unless every condition holds."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from app.brokers.zerodha import ZerodhaBrokerAdapter
from app.execution.order_manager import ExecutionGate, OrderManager
from app.live.config import LiveConfig
from app.live.order_manager import LiveExitReason, LiveTradeStatus
from app.live.service import LiveTradingError, LiveTradingService
from app.risk.config import MarketSessionConfig, RiskLimits
from app.risk.schemas import TradeIntent, TradeSide
from app.tests.fake_kite import ACCESS_TOKEN, API_KEY, API_SECRET, FakeKite

T0 = datetime(2026, 10, 1, 4, 30, tzinfo=UTC)  # Thursday 10:00 IST
ADMIN = uuid.uuid4()


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> datetime:
        return self.now


@dataclass
class Market:
    clock: Clock
    prices: dict[str, float] = field(default_factory=lambda: {"INFY": 1500.0})
    simulated: bool = False
    status: str = "LIVE"
    stale: set[str] = field(default_factory=set)
    websocket: str = "CONNECTED"

    def data_status(self) -> str:
        return self.status

    def is_stale(self, symbol: str | None = None, now: Any = None) -> bool:
        return self.status != "LIVE" or (symbol in self.stale)

    def get_snapshot(self, symbol: str) -> Any:
        if symbol not in self.prices:
            return None
        return SimpleNamespace(price=self.prices[symbol], timestamp=self.clock())

    @property
    def provider(self) -> Any:
        return SimpleNamespace(status_details=lambda: {"websocket": self.websocket})


def report(status: str = "HEALTHY", db: bool = True, redis: bool = True) -> Any:
    def c(name: str, ok: bool) -> Any:
        return SimpleNamespace(name=name, ok=ok, status="CONNECTED" if ok else "DISCONNECTED")

    return SimpleNamespace(status=status, checks=[c("database", db), c("redis", redis)])


class Trading:
    """The paper TradingService surface the live layer uses."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.intents: dict[uuid.UUID, TradeIntent] = {}
        self.engine = SimpleNamespace(emergency_stop_active=False)
        self.stops: list[str] = []

    def add(self, **kw: Any) -> uuid.UUID:
        sid = uuid.uuid4()
        base: dict[str, Any] = {
            "signal_id": sid,
            "symbol": "INFY",
            "side": TradeSide.LONG,
            "entry": 1500.0,
            "stop_loss": 1485.0,
            "target": 1530.0,
            "score": 90.0,
            "strategy": "TREND_CONTINUATION",
            "signal_last_seen_at": self.clock(),
            "signal_active": True,
        }
        base.update(kw)
        self.intents[sid] = TradeIntent(**base)
        return sid

    async def build_intent(self, signal_id: uuid.UUID) -> tuple[TradeIntent, None]:
        return self.intents[signal_id], None

    async def emergency_stop(self, reason: str, *, close_position: bool | None = None) -> None:
        self.engine.emergency_stop_active = True
        self.stops.append(reason)


@dataclass
class Rig:
    clock: Clock
    kite: FakeKite
    adapter: ZerodhaBrokerAdapter
    orders: OrderManager
    market: Market
    trading: Trading
    svc: LiveTradingService
    health: dict[str, Any]
    auth: dict[str, bool]

    def entry_posts(self) -> list[dict[str, Any]]:
        return [p for p in self.kite.posts() if p.get("tag")]

    def order_for(self, suffix: str) -> dict[str, Any]:
        from app.brokers.zerodha.mapping import order_tag

        trade = self.svc.manager.active or self.svc.manager.closed[-1]
        tag = order_tag(f"{trade.correlation_id}-{suffix}")
        return next(o for o in self.kite.orders.values() if o["tag"] == tag)

    async def tick(self, seconds: float = 2) -> None:
        self.clock.now += timedelta(seconds=seconds)
        await self.svc.monitor.check()
        await self.svc.tick()


async def make(configured: bool = True, **cfg: Any) -> Rig:
    clock = Clock()
    kite = FakeKite()
    kite.net_positions = []
    adapter = ZerodhaBrokerAdapter(
        api_key=API_KEY,
        api_secret=API_SECRET,
        access_token=ACCESS_TOKEN,
        transport=kite.transport(),
        clock=clock,
    )
    await adapter.connect()
    holder: dict[str, LiveTradingService] = {}
    gate = ExecutionGate(
        live_mode_configured=lambda: configured,
        user_armed=lambda: holder["svc"].arming.is_armed,
        system_healthy=lambda: holder["svc"].monitor.healthy,
        session_open=lambda: holder["svc"].risk.entries_allowed(clock())[0],
        emergency_stop=lambda: holder["svc"].kill_switch.engaged,
    )
    orders = OrderManager(adapter, gate, clock=clock)
    market = Market(clock)
    trading = Trading(clock)
    health: dict[str, Any] = {"report": report()}
    auth = {"ok": True}

    async def probe() -> Any:
        return health["report"]

    limits = RiskLimits(
        max_order_quantity=5_000,
        max_position_value=20_000.0,
        max_exposure=20_000.0,
        min_signal_score=60,
        session=MarketSessionConfig(last_entry_time=datetime(2026, 1, 1, 14, 45).time()),
    )
    config = LiveConfig(configured=configured, max_position_value=20_000.0, **cfg)
    svc = LiveTradingService(
        config=config,
        limits=limits,
        orders=orders,
        market_data=market,
        trading=trading,
        signals=None,
        health_probe=probe,
        kite_authenticated=lambda: auth["ok"],
        clock=clock,
    )
    holder["svc"] = svc
    await svc.monitor.check()
    return Rig(clock, kite, adapter, orders, market, trading, svc, health, auth)


async def armed(**cfg: Any) -> Rig:
    rig = await make(**cfg)
    await rig.svc.arm(user_id=ADMIN, email="admin@example.com")
    return rig


# --- arming and gating -------------------------------------------------------------


async def test_not_configured_means_no_arming_and_no_orders() -> None:
    rig = await make(configured=False)
    with pytest.raises(LiveTradingError):
        await rig.svc.arm(user_id=ADMIN, email="a@example.com")
    sid = rig.trading.add()
    report_, trade = await rig.svc.execute_signal(sid, user_id=ADMIN)
    failed = {c.name for c in report_.failed}
    assert {"live_configured", "armed"} <= failed and trade is None
    assert rig.kite.posts() == []


async def test_configured_but_not_armed_places_nothing() -> None:
    rig = await make()
    report_, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert [c.name for c in report_.failed] == ["armed", "risk_approved"] and trade is None
    risk = next(c for c in report_.checks if c.name == "risk_approved")
    assert "TRADING_MODE_NOT_PAPER" in risk.detail  # the risk engine refuses LIVE unarmed
    assert rig.kite.posts() == []


async def test_arming_is_refused_when_blocked_and_expires_at_close() -> None:
    rig = await make()
    rig.auth["ok"] = False
    with pytest.raises(LiveTradingError, match="Kite session"):
        await rig.svc.arm(user_id=ADMIN, email="a@example.com")
    rig.auth["ok"] = True
    rig.market.simulated = True
    with pytest.raises(LiveTradingError, match="simulated"):
        await rig.svc.arm(user_id=ADMIN, email="a@example.com")
    rig.market.simulated = False
    await rig.svc.arm(user_id=ADMIN, email="a@example.com")
    assert rig.svc.arming.is_armed
    rig.clock.now = T0.replace(hour=10, minute=0)  # 15:30 IST
    await rig.svc.tick()
    assert not rig.svc.arming.is_armed
    assert rig.svc.arming.state.last_disarm_reason == "session closed"


async def test_lost_kite_session_disarms() -> None:
    rig = await armed()
    rig.auth["ok"] = False
    await rig.svc.tick()
    assert not rig.svc.arming.is_armed and not rig.svc.auto_trading


async def test_preflight_passes_every_check_and_places_nothing() -> None:
    rig = await armed()
    decision, rep = await rig.svc.preflight(rig.trading.add())
    assert rep.ok, rep.summary
    assert decision.approved and rep.quantity == decision.size.quantity  # type: ignore[union-attr]
    assert len(rep.checks) == 20
    assert rig.kite.posts() == []


# --- every failing check blocks the order -----------------------------------------------


@pytest.mark.parametrize(
    ("break_it", "check"),
    [
        (lambda r: setattr(r.market, "status", "STALE"), "market_data_healthy"),
        (lambda r: r.market.stale.add("INFY"), "market_data_healthy"),
        (lambda r: setattr(r.market, "websocket", "RECONNECTING"), "websocket_healthy"),
        (lambda r: r.health.update(report=report(db=False)), "database_healthy"),
        (lambda r: r.health.update(report=report(redis=False)), "redis_healthy"),
        (lambda r: r.health.update(report=report("DEGRADED")), "broker_health"),
        (lambda r: setattr(r.clock, "now", T0.replace(hour=9, minute=20)), "market_open"),
        (lambda r: setattr(r.clock, "now", T0 + timedelta(days=2)), "market_open"),
        (lambda r: setattr(r.trading.engine, "emergency_stop_active", True), "no_emergency_stop"),
        (
            lambda r: r.kite.net_positions.append(
                {
                    "tradingsymbol": "TCS",
                    "exchange": "NSE",
                    "product": "MIS",
                    "quantity": 5,
                    "average_price": 3000,
                    "last_price": 3000,
                    "realised": 0,
                    "unrealised": 0,
                }
            ),
            "no_duplicate_position",
        ),
        (
            lambda r: setattr(
                r.kite,
                "margins",
                {"net": 900.0, "available": {"cash": 900.0, "live_balance": 900.0}, "utilised": {}},
            ),
            "account_balance",
        ),
        (lambda r: setattr(r.svc, "unresolved", "left over"), "no_unresolved_trade"),
    ],
)
async def test_any_failing_check_blocks_the_order(break_it: Any, check: str) -> None:
    rig = await armed()
    sid = rig.trading.add(signal_last_seen_at=T0)
    break_it(rig)
    rig.trading.intents[sid] = rig.trading.intents[sid].model_copy(
        update={"signal_last_seen_at": rig.clock()}
    )
    await rig.svc.monitor.check()
    rep, trade = await rig.svc.execute_signal(sid, user_id=ADMIN)
    assert not rep.ok and check in {c.name for c in rep.failed}, rep.summary
    assert trade is None and rig.kite.posts() == []


@pytest.mark.parametrize(
    ("intent", "check"),
    [
        ({"stop_loss": 1400.0}, "valid_stop"),  # 6.7% away (max 3%)
        ({"target": 1505.0}, "valid_target"),  # risk/reward 0.33
        ({"signal_active": False}, "no_stale_signal"),
        ({"signal_last_seen_at": T0 - timedelta(minutes=10)}, "no_stale_signal"),
    ],
)
async def test_invalid_trade_parameters_block_the_order(intent: dict[str, Any], check: str) -> None:
    rig = await armed()
    rep, trade = await rig.svc.execute_signal(rig.trading.add(**intent), user_id=ADMIN)
    assert check in {c.name for c in rep.failed} and trade is None
    assert rig.kite.posts() == []


async def test_quantity_is_capped_by_the_live_position_value() -> None:
    rig = await armed()
    rep, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert rep.ok and trade is not None
    assert trade.quantity * 1500.0 <= 20_000.0 and trade.quantity > 0


# --- lifecycle -------------------------------------------------------------------------


async def test_entry_then_stop_and_target_then_target_exit_closes_the_trade() -> None:
    rig = await armed()
    rep, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert rep.ok and trade is not None and trade.status == LiveTradeStatus.PENDING_ENTRY
    (entry_post,) = rig.kite.posts()
    assert entry_post["order_type"] == "LIMIT" and entry_post["product"] == "MIS"
    assert entry_post["transaction_type"] == "BUY" and entry_post["price"] == "1500.0"
    assert entry_post["tag"].startswith("GZ")
    qty = trade.quantity
    entry = rig.order_for("ENTRY")
    rig.kite.set_status(entry["order_id"], "COMPLETE", filled=qty, average=1500.05)
    await rig.tick()
    active = rig.svc.manager.active
    assert active is not None and active.status == LiveTradeStatus.OPEN
    stop = rig.order_for("SL")
    target = rig.order_for("TGT")
    assert stop["order_type"] == "SL-M" and stop["trigger_price"] == 1485.0
    assert stop["transaction_type"] == "SELL" and stop["quantity"] == qty
    assert target["order_type"] == "LIMIT" and target["price"] == 1530.0
    rig.kite.set_status(target["order_id"], "COMPLETE", filled=qty, average=1530.0)
    await rig.tick()
    assert rig.svc.manager.active is None
    closed = rig.svc.manager.closed[-1]
    assert closed.status == LiveTradeStatus.CLOSED and closed.exit_reason == LiveExitReason.TARGET
    assert closed.gross_pnl == pytest.approx((1530.0 - 1500.05) * qty)
    assert rig.kite.orders[stop["order_id"]]["status"] == "CANCELLED"  # one-cancels-other
    assert rig.svc.risk.state.trades_today == 1


async def test_partial_entry_fill_protects_only_the_filled_quantity() -> None:
    rig = await armed()
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert trade is not None
    entry = rig.order_for("ENTRY")
    rig.kite.set_status(entry["order_id"], "OPEN", filled=3, average=1500.0)
    await rig.tick()
    assert rig.order_for("SL")["quantity"] == 3
    rig.kite.set_status(entry["order_id"], "COMPLETE", filled=trade.quantity, average=1500.0)
    await rig.tick()
    assert rig.order_for("SL")["quantity"] == trade.quantity  # resized, not re-placed
    assert rig.order_for("TGT")["quantity"] == trade.quantity


async def test_stop_loss_exit() -> None:
    rig = await armed()
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert trade is not None
    rig.kite.set_status(
        rig.order_for("ENTRY")["order_id"], "COMPLETE", filled=trade.quantity, average=1500.0
    )
    await rig.tick()
    rig.kite.set_status(
        rig.order_for("SL")["order_id"], "COMPLETE", filled=trade.quantity, average=1484.9
    )
    await rig.tick()
    closed = rig.svc.manager.closed[-1]
    assert closed.exit_reason == LiveExitReason.STOP_LOSS and closed.gross_pnl < 0  # type: ignore[operator]
    assert rig.kite.orders[rig.order_for("TGT")["order_id"]]["status"] == "CANCELLED"
    assert rig.svc.risk.state.consecutive_losses == 1


async def test_unfilled_entry_is_cancelled_after_the_timeout() -> None:
    rig = await armed(entry_timeout_seconds=30)
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert trade is not None
    await rig.tick(31)
    closed = rig.svc.manager.closed[-1]
    assert closed.status == LiveTradeStatus.CANCELLED
    assert closed.exit_reason == LiveExitReason.ENTRY_TIMEOUT
    assert len(rig.kite.posts()) == 1  # nothing else was sent


async def test_square_off_closes_at_market() -> None:
    rig = await armed()
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert trade is not None
    rig.kite.set_status(
        rig.order_for("ENTRY")["order_id"], "COMPLETE", filled=trade.quantity, average=1500.0
    )
    await rig.tick()
    rig.clock.now = T0.replace(hour=9, minute=46)  # 15:16 IST
    await rig.tick()  # legs cancelled
    await rig.tick()  # market exit sent
    exit_order = rig.order_for("EXIT1")
    assert exit_order["order_type"] == "MARKET" and exit_order["transaction_type"] == "SELL"
    assert exit_order["quantity"] == trade.quantity
    rig.kite.set_status(exit_order["order_id"], "COMPLETE", filled=trade.quantity, average=1510.0)
    await rig.tick()
    assert rig.svc.manager.closed[-1].exit_reason == LiveExitReason.SQUARE_OFF


# --- duplicates and unknown outcomes -----------------------------------------------------


async def test_one_active_trade_and_no_second_order_for_a_signal() -> None:
    rig = await armed()
    sid = rig.trading.add()
    _, first = await rig.svc.execute_signal(sid, user_id=ADMIN)
    assert first is not None
    rep2, again = await rig.svc.execute_signal(sid, user_id=ADMIN)
    assert again is None and "no_stale_signal" in {c.name for c in rep2.failed}
    rep3, other = await rig.svc.execute_signal(rig.trading.add(symbol="INFY"), user_id=ADMIN)
    assert other is None and "no_duplicate_position" in {c.name for c in rep3.failed}
    assert len(rig.kite.posts()) == 1


async def test_an_entry_with_an_unknown_outcome_is_reconciled_never_resent() -> None:
    rig = await armed()
    rig.kite.timeout_after_processing_next = True  # Kite got it; the response is lost
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert trade is not None and trade.status == LiveTradeStatus.PENDING_ENTRY
    assert len(rig.kite.posts()) == 1
    await rig.tick()
    await rig.tick()
    assert len(rig.kite.posts()) == 1  # still exactly one entry request
    entry = rig.order_for("ENTRY")
    rig.kite.set_status(entry["order_id"], "COMPLETE", filled=trade.quantity, average=1500.0)
    await rig.tick()
    assert rig.svc.manager.active is not None
    assert rig.svc.manager.active.status == LiveTradeStatus.OPEN  # adopted by its tag


# --- kill switch --------------------------------------------------------------------------


async def test_kill_switch_cancels_a_pending_entry_and_disarms() -> None:
    rig = await armed()
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert trade is not None
    result = await rig.svc.kill(reason="test", user_id=ADMIN, close_positions=None)
    assert result.was_armed and result.entry_cancelled and not result.exit_requested
    assert rig.kite.orders[rig.order_for("ENTRY")["order_id"]]["status"] == "CANCELLED"
    assert not rig.svc.arming.is_armed and rig.trading.stops  # paper stopped too
    rep, t2 = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert t2 is None and "no_emergency_stop" in {c.name for c in rep.failed}
    with pytest.raises(LiveTradingError):  # cannot re-arm until reset
        await rig.svc.arm(user_id=ADMIN, email="a@example.com")


async def test_kill_switch_keeps_the_protective_stop_unless_told_to_close() -> None:
    rig = await armed()
    _, trade = await rig.svc.execute_signal(rig.trading.add(), user_id=ADMIN)
    assert trade is not None
    rig.kite.set_status(
        rig.order_for("ENTRY")["order_id"], "COMPLETE", filled=trade.quantity, average=1500.0
    )
    await rig.tick()
    await rig.svc.kill(reason="test", user_id=ADMIN, close_positions=None)
    await rig.tick()
    assert rig.kite.orders[rig.order_for("SL")["order_id"]]["status"] == "TRIGGER PENDING"
    assert rig.svc.manager.active is not None  # the position is not closed
    await rig.svc.kill(reason="close it", user_id=ADMIN, close_positions=True)
    await rig.tick()
    await rig.tick()
    exit_order = rig.order_for("EXIT1")  # reduce-only: allowed while killed and disarmed
    assert exit_order["order_type"] == "MARKET" and exit_order["quantity"] == trade.quantity


async def test_reset_does_not_rearm() -> None:
    rig = await armed()
    await rig.svc.kill(reason="test", user_id=ADMIN, close_positions=False)
    await rig.svc.reset_kill(user_id=ADMIN)
    assert not rig.svc.kill_switch.engaged and not rig.svc.arming.is_armed


async def test_paper_emergency_stop_engages_the_kill_switch() -> None:
    rig = await armed()
    await rig.svc.on_paper_emergency_stop("dashboard")
    assert rig.svc.kill_switch.engaged and not rig.svc.arming.is_armed


async def test_live_auto_trading_needs_arming() -> None:
    rig = await make()
    with pytest.raises(LiveTradingError):
        await rig.svc.set_auto(True, user_id=ADMIN)
    await rig.svc.arm(user_id=ADMIN, email="a@example.com")
    await rig.svc.set_auto(True, user_id=ADMIN)
    await rig.svc.disarm(user_id=ADMIN, reason="done")
    assert not rig.svc.auto_trading
