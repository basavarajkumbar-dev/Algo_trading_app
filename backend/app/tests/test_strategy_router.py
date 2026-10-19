"""StrategyRouter, end-to-end evaluation, StrategyService and API."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.paper_provider import PaperMarketDataProvider
from app.market_data.schemas import Timeframe
from app.market_data.service import MarketDataService
from app.price_action.service import PriceActionService
from app.regime.schemas import MarketRegime
from app.strategies.config import (
    RouterConfig,
    RouteRule,
    StrategyEngineConfig,
    TrendContinuationConfig,
)
from app.strategies.context import build_context
from app.strategies.router import StrategyRouter
from app.strategies.schemas import EXTRA_STRATEGIES, StrategyName, TradeDirection
from app.strategies.service import StrategyService
from app.tests.candle_factory import candles_from_closes, uptrend_closes
from app.tests.strategy_scenarios import mean_reversion_short, regime, trend_long, trend_short

TC = StrategyName.TREND_CONTINUATION
BO = StrategyName.BREAKOUT_MOMENTUM
MR = StrategyName.MEAN_REVERSION

# The four added strategies are disabled by default; research views list
# every *enabled* strategy, i.e. the original three.
DEFAULT_ENABLED = set(StrategyName) - set(EXTRA_STRATEGIES)


# --- routing ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("regime_", "expected"),
    [
        (MarketRegime.STRONG_BULLISH, [TC]),
        (MarketRegime.STRONG_BEARISH, [TC]),
        (MarketRegime.BREAKOUT, [BO]),
        (MarketRegime.RANGE, [MR, BO]),
        (MarketRegime.HIGH_VOLATILITY, []),
        (MarketRegime.UNKNOWN, []),
    ],
)
def test_default_routes(regime_: MarketRegime, expected: list[StrategyName]) -> None:
    decision = StrategyRouter().route(regime_, 80.0)
    assert decision.eligible_strategies == expected
    assert {e.strategy for e in decision.entries} == set(
        StrategyName
    )  # every strategy accounted for
    for entry in decision.entries:
        assert entry.reason


def test_high_volatility_is_defensive() -> None:
    decision = StrategyRouter().route(MarketRegime.HIGH_VOLATILITY, 95.0)
    assert decision.eligible_strategies == []
    assert any("defensive" in r for r in decision.reasons)


def test_trend_routes_restrict_direction() -> None:
    router = StrategyRouter()
    bull = next(
        e for e in router.route(MarketRegime.STRONG_BULLISH, 80).entries if e.strategy == TC
    )
    bear = next(
        e for e in router.route(MarketRegime.STRONG_BEARISH, 80).entries if e.strategy == TC
    )
    assert bull.allowed_directions == [TradeDirection.LONG]
    assert bear.allowed_directions == [TradeDirection.SHORT]


def test_low_regime_confidence_routes_nothing() -> None:
    decision = StrategyRouter().route(MarketRegime.STRONG_BULLISH, 40.0)
    assert decision.eligible_strategies == []
    assert "below" in next(e for e in decision.entries if e.strategy == TC).reason


def test_router_is_configurable() -> None:
    config = StrategyEngineConfig(
        router=RouterConfig(
            routes={
                MarketRegime.HIGH_VOLATILITY: [RouteRule(strategy=MR)],
                MarketRegime.STRONG_BULLISH: [RouteRule(strategy=BO), RouteRule(strategy=TC)],
            },
            min_regime_confidence=30.0,
        )
    )
    router = StrategyRouter(config)
    assert router.route(MarketRegime.HIGH_VOLATILITY, 50).eligible_strategies == [MR]
    # configured order is kept: it is evaluation order, not a ranking
    assert router.route(MarketRegime.STRONG_BULLISH, 35).eligible_strategies == [BO, TC]
    assert (
        router.route(MarketRegime.RANGE, 90).eligible_strategies == []
    )  # unmapped regimes route nothing


def test_disabled_strategy_is_not_routed() -> None:
    router = StrategyRouter(
        StrategyEngineConfig(trend_continuation=TrendContinuationConfig(enabled=False))
    )
    decision = router.route(MarketRegime.STRONG_BULLISH, 90)
    assert decision.eligible_strategies == []
    assert "disabled" in next(e for e in decision.entries if e.strategy == TC).reason


def test_router_makes_no_superiority_claim() -> None:
    decision = StrategyRouter().route(MarketRegime.RANGE, 80)
    dumped = decision.model_dump_json().lower()
    for word in ("best", "superior", "better", "rank"):
        assert word not in dumped


# --- evaluation through the router --------------------------------------------------------


def test_router_evaluates_only_routed_strategies() -> None:
    evaluation = StrategyRouter().evaluate(trend_long())
    assert [r.strategy for r in evaluation.results] == [TC]
    assert [r.strategy for r in evaluation.candidates] == [TC]
    assert "not orders" in evaluation.note


def test_direction_not_allowed_is_a_blocker() -> None:
    ctx = trend_short()
    result = StrategyRouter().strategies[TC].evaluate(ctx, [TradeDirection.LONG])
    assert not result.eligible
    assert any("Direction allowed by router" in b for b in result.blockers)


def test_mean_reversion_is_not_routed_in_a_strong_trend() -> None:
    ctx = mean_reversion_short()
    ctx.regime = regime(MarketRegime.STRONG_BULLISH)
    evaluation = StrategyRouter().evaluate(ctx)
    assert MR not in [r.strategy for r in evaluation.results]


def test_evaluate_all_ignores_routing_for_research() -> None:
    ctx = mean_reversion_short()
    ctx.regime = regime(MarketRegime.HIGH_VOLATILITY)
    router = StrategyRouter()
    assert router.evaluate(ctx).results == []
    everything = router.evaluate_all(ctx)
    assert {r.strategy for r in everything} == DEFAULT_ENABLED
    assert next(r for r in everything if r.strategy == MR).eligible


def test_build_context_on_real_engines() -> None:
    data = {
        tf: candles_from_closes(uptrend_closes(), timeframe=tf)
        for tf in (Timeframe.M60, Timeframe.M15, Timeframe.M5)
    }
    ctx = build_context("TEST", data)
    assert ctx is not None
    assert ctx.regime.regime == MarketRegime.STRONG_BULLISH
    assert ctx.atr > 0
    evaluation = StrategyRouter().evaluate(ctx)
    assert evaluation.decision.eligible_strategies == [TC]
    for result in StrategyRouter().evaluate_all(ctx):
        assert not any(b.startswith("Strategy error") for b in result.blockers)
    assert build_context("TEST", {}) is None


# --- architecture guard ------------------------------------------------------------------


def test_strategy_code_has_no_path_to_brokers_or_orders() -> None:
    """The brief: the strategy engine must never call the broker. No module
    in app/strategies imports broker, order or execution code."""

    package = Path(__file__).resolve().parents[1] / "strategies"
    forbidden = ("broker", "zerodha", "kite", "order", "execution")
    for path in package.glob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(word in node.module.lower() for word in forbidden), (
                    path.name,
                    node.module,
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(word in alias.name.lower() for word in forbidden), (
                        path.name,
                        alias.name,
                    )


# --- service ---------------------------------------------------------------------------------


async def test_service_follows_price_action_refreshes() -> None:
    provider = PaperMarketDataProvider(
        delegate=MockMarketDataProvider(
            rng_seed=9, tick_interval_seconds=10.0, regime_rotation_seconds=None
        )
    )
    market_data = MarketDataService(
        provider, watchlist=["NIFTY50", "RELIANCE", "TCS", "INFY"], warmup_candles=120
    )
    await market_data.start()
    price_action = PriceActionService(market_data, refresh_seconds=60)
    strategies = StrategyService(
        market_data, price_action, benchmark_symbol="NIFTY50", poll_seconds=60
    )
    try:
        await price_action.start()
        await strategies.start()
        overview = strategies.get_overview()
        assert overview is not None
        assert {row.symbol for row in overview.symbols} == {"NIFTY50", "RELIANCE", "TCS", "INFY"}
        assert overview.status.price_action_refresh == price_action.refresh_count == 1
        first_count = strategies.status().refresh_count

        await strategies.refresh()  # no new price action: nothing recomputed
        assert strategies.status().refresh_count == first_count
        await price_action.refresh()
        await strategies.refresh()
        assert strategies.status().refresh_count == first_count + 1

        detail = strategies.get_symbol("RELIANCE", include_unrouted=True)
        assert detail is not None and detail.unrouted is not None
        assert {r.strategy for r in detail.unrouted} == DEFAULT_ENABLED
        routed = {r.strategy for r in detail.evaluation.results}
        assert routed == set(detail.evaluation.decision.eligible_strategies)
        ranks = sorted(
            c.relative_strength_rank
            for c in strategies._contexts.values()
            if c.relative_strength_rank is not None
        )
        assert ranks == [1, 2, 3]  # constituents ranked; the benchmark is not ranked against itself
        assert strategies.get_symbol("NOPE") is None
    finally:
        await strategies.stop()
        await price_action.stop()
        await market_data.stop()


# --- API -------------------------------------------------------------------------------------

FORBIDDEN_KEYS = {"order", "orders", "order_id", "quantity", "qty", "broker", "position_size"}


def _keys(value: object) -> set[str]:
    if isinstance(value, dict):
        found = set(value)
        for item in value.values():
            found |= _keys(item)
        return found
    if isinstance(value, list):
        nested: set[str] = set()
        for item in value:
            nested |= _keys(item)
        return nested
    return set()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_strategies_overview_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/strategies")
    assert response.status_code == 200
    body = response.json()
    assert len(body["symbols"]) == 51
    assert body["status"]["symbols_evaluated"] == 51
    assert "not orders" in body["note"]
    for row in body["symbols"]:
        assert set(row["eligible_strategies"]) <= {s.value for s in StrategyName}


def test_symbol_strategies_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/strategies/reliance?include_unrouted=true")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "RELIANCE"
    assert len(body["unrouted"]) == 3
    for result in body["unrouted"]:
        for key in (
            "eligible",
            "direction",
            "entry_candidate",
            "stop_candidate",
            "target_candidate",
            "confidence",
            "reasons",
            "features",
        ):
            assert key in result
    assert not (_keys(body) & FORBIDDEN_KEYS)


def test_route_and_config_endpoints(client: TestClient) -> None:
    route = client.get("/api/v1/strategies/route?regime=RANGE&confidence=70").json()
    assert route["eligible_strategies"] == ["MEAN_REVERSION", "BREAKOUT_MOMENTUM"]
    assert client.get("/api/v1/strategies/route?regime=NOPE").status_code == 422
    config = client.get("/api/v1/strategies/config").json()
    assert config["breakout_momentum"]["opening_range_minutes"] == 15
    assert config["router"]["routes"]["HIGH_VOLATILITY"] == []
    assert client.get("/api/v1/strategies/NOPE").status_code == 404
