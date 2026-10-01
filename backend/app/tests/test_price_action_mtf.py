"""Multi-timeframe engine, price action analyzer, service and API."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.paper_provider import PaperMarketDataProvider
from app.market_data.schemas import Candle, Timeframe
from app.market_data.service import MarketDataService
from app.market_intelligence.analyzer import analyze_candles
from app.market_intelligence.schemas import Trend
from app.price_action.analyzer import analyze_price_action
from app.price_action.config import MultiTimeframeConfig, PriceActionConfig
from app.price_action.multi_timeframe import (
    SetupState,
    analyze_multi_timeframe,
    classify_setup_state,
    timeframe_bias,
)
from app.price_action.retracement import RetracementResult
from app.price_action.schemas import PatternDirection, PatternType, RetracementZone
from app.price_action.service import PriceActionService
from app.tests.candle_factory import (
    candles_from_closes,
    downtrend_closes,
    range_closes,
    uptrend_closes,
)

TIMEFRAMES = (Timeframe.M60, Timeframe.M15, Timeframe.M5)


def series(
    closes: list[float], timeframe: Timeframe, volumes: list[int] | None = None
) -> list[Candle]:
    return candles_from_closes(closes, timeframe=timeframe, volumes=volumes)


def all_up() -> dict[Timeframe, list[Candle]]:
    return {tf: series(uptrend_closes(), tf) for tf in TIMEFRAMES}


# --- bias -------------------------------------------------------------------------------


def test_timeframe_bias_sign_follows_trend() -> None:
    up = analyze_candles("T", Timeframe.M5, series(uptrend_closes(), Timeframe.M5))
    down = analyze_candles("T", Timeframe.M5, series(downtrend_closes(), Timeframe.M5))
    up_bias, components = timeframe_bias(up)
    down_bias, _ = timeframe_bias(down)
    assert up_bias > 20 and down_bias < -20
    assert set(components) == {"moving_averages", "structure", "momentum", "vwap"}
    assert all(-1 <= v <= 1 for v in components.values())


# --- multi-timeframe ---------------------------------------------------------------------


def test_aligned_uptrend_on_every_timeframe() -> None:
    result = analyze_multi_timeframe("T", all_up())
    assert result.overall_bias == PatternDirection.BULLISH
    assert result.alignment_score >= 80
    assert not result.conflicts
    roles = [result.context_analysis, result.setup_analysis, result.entry_analysis]
    assert [r.timeframe for r in roles] == list(TIMEFRAMES)
    assert all(r.available and r.direction == Trend.UP for r in roles)
    assert result.context_analysis.key_levels  # important levels on the context timeframe
    limit = PriceActionConfig().multi_timeframe.key_level_max_distance_atr
    assert all(abs(lvl.distance_atr) <= limit for lvl in result.context_analysis.key_levels)
    assert result.setup_analysis.retracement is not None
    assert result.setup_analysis.setup_state is not None
    assert result.entry_analysis.confirmation is not None
    assert "not a trade signal" in result.note


def test_conflicting_timeframes_lower_alignment_and_are_reported() -> None:
    mixed = {
        Timeframe.M60: series(uptrend_closes(), Timeframe.M60),
        Timeframe.M15: series(downtrend_closes(), Timeframe.M15),
        Timeframe.M5: series(downtrend_closes(), Timeframe.M5),
    }
    aligned = analyze_multi_timeframe("T", all_up())
    result = analyze_multi_timeframe("T", mixed)
    assert result.alignment_score < aligned.alignment_score
    assert result.conflicts
    assert result.setup_analysis.setup_state in (
        SetupState.COUNTER_TREND,
        SetupState.WEAKENING_PULLBACK,
        SetupState.IMPULSE_INVALIDATED,
        SetupState.PULLBACK_IN_TREND,
    )


def test_sideways_market_has_low_alignment() -> None:
    sideways = {tf: series(range_closes(), tf) for tf in TIMEFRAMES}
    result = analyze_multi_timeframe("T", sideways)
    assert result.overall_bias == PatternDirection.NEUTRAL
    assert result.alignment_score < 50


def test_missing_timeframe_is_unavailable_and_excluded() -> None:
    data = all_up()
    data[Timeframe.M60] = data[Timeframe.M60][:20]
    result = analyze_multi_timeframe("T", data)
    assert not result.context_analysis.available
    assert "Needs" in (result.context_analysis.unavailable_reason or "")
    assert result.setup_analysis.available and result.entry_analysis.available
    assert result.overall_bias == PatternDirection.BULLISH  # from the remaining timeframes
    assert result.setup_analysis.setup_state == SetupState.NO_DIRECTIONAL_CONTEXT


def test_timeframes_are_configurable() -> None:
    config = PriceActionConfig(
        multi_timeframe=MultiTimeframeConfig(
            context_timeframe=Timeframe.M30,
            setup_timeframe=Timeframe.M15,
            entry_timeframe=Timeframe.M3,
        )
    )
    data = {tf: series(uptrend_closes(), tf) for tf in (Timeframe.M30, Timeframe.M15, Timeframe.M3)}
    result = analyze_multi_timeframe("T", data, config=config)
    assert result.context_analysis.timeframe == Timeframe.M30
    assert result.entry_analysis.timeframe == Timeframe.M3
    assert result.entry_analysis.available


def test_timeframe_order_is_validated() -> None:
    with pytest.raises(ValidationError):
        MultiTimeframeConfig(context_timeframe=Timeframe.M5, entry_timeframe=Timeframe.M60)


def test_entry_confirmation_with_aligned_wide_range_body_and_volume() -> None:
    data = all_up()
    closes = uptrend_closes()
    entry = series([*closes, closes[-1] + 1.5], Timeframe.M5, volumes=[1000] * 250 + [3000])
    data[Timeframe.M5] = entry
    result = analyze_multi_timeframe("T", data)
    confirmation = result.entry_analysis.confirmation
    assert confirmation is not None
    assert confirmation.reference_bias == PatternDirection.BULLISH
    assert "WRB" in confirmation.aligned_patterns
    assert confirmation.candle_confirmation and confirmation.volume_confirmed
    assert confirmation.confirmations >= 2
    assert any("not an order" in r for r in result.entry_analysis.reasons)


@pytest.mark.parametrize(
    ("context", "setup", "zone", "direction", "expected"),
    [
        (Trend.SIDEWAYS, Trend.UP, None, None, SetupState.NO_DIRECTIONAL_CONTEXT),
        (Trend.UP, Trend.UP, RetracementZone.SHALLOW, Trend.UP, SetupState.WITH_TREND),
        (
            Trend.UP,
            Trend.DOWN,
            RetracementZone.HEALTHY_PULLBACK,
            Trend.UP,
            SetupState.PULLBACK_IN_TREND,
        ),
        (
            Trend.UP,
            Trend.DOWN,
            RetracementZone.DEEP_PULLBACK,
            Trend.UP,
            SetupState.PULLBACK_IN_TREND,
        ),
        (
            Trend.DOWN,
            Trend.UP,
            RetracementZone.WEAKENING,
            Trend.DOWN,
            SetupState.WEAKENING_PULLBACK,
        ),
        (
            Trend.UP,
            Trend.DOWN,
            RetracementZone.INVALIDATION_CONTEXT,
            Trend.UP,
            SetupState.IMPULSE_INVALIDATED,
        ),
        (Trend.UP, Trend.DOWN, None, None, SetupState.COUNTER_TREND),
        (Trend.UP, Trend.SIDEWAYS, None, None, SetupState.UNCLEAR),
    ],
)
def test_setup_state(context, setup, zone, direction, expected) -> None:
    retracement = (
        RetracementResult(detected=True, direction=direction, zone=zone)
        if zone is not None
        else None
    )
    assert classify_setup_state(context, setup, retracement) == expected


# --- full analyzer -------------------------------------------------------------------------


def test_analyze_price_action_returns_every_part() -> None:
    result = analyze_price_action("T", all_up())
    assert result.timeframe == Timeframe.M5
    assert [p.pattern for p in result.patterns] == list(PatternType)
    assert all(p.context.levels_considered for p in result.patterns)
    assert result.swing_structure is not None
    assert result.retracement is not None
    assert result.support_resistance is not None and result.support_resistance.levels
    assert result.multi_timeframe is not None
    assert result.unavailable == {}
    assert "not trade signals" in result.note


def test_analyze_price_action_without_candles() -> None:
    result = analyze_price_action("T", {})
    assert "all" in result.unavailable
    assert result.patterns == []


# --- service ----------------------------------------------------------------------------------


async def test_service_analyses_every_symbol() -> None:
    provider = PaperMarketDataProvider(
        delegate=MockMarketDataProvider(
            rng_seed=5, tick_interval_seconds=10.0, regime_rotation_seconds=None
        )
    )
    market_data = MarketDataService(
        provider, watchlist=["NIFTY50", "RELIANCE", "TCS"], warmup_candles=120
    )
    await market_data.start()
    service = PriceActionService(market_data, refresh_seconds=60)
    try:
        await service.start()
        overview = service.get_overview()
        assert overview is not None
        assert {row.symbol for row in overview.symbols} == {"NIFTY50", "RELIANCE", "TCS"}
        assert overview.status.refresh_count == 1 and overview.status.last_error is None
        detail = service.get_symbol("RELIANCE")
        assert detail is not None
        assert len(detail.analysis.patterns) == 6
        assert detail.analysis.multi_timeframe is not None
        assert detail.analysis.multi_timeframe.context_analysis.available
        assert detail.analysis.support_resistance is not None
        assert detail.analysis.support_resistance.previous_session_date is not None
        assert 0 < len(detail.candles) <= 150
        assert all(c.is_closed for c in detail.candles)
        assert detail.candles[-1].timestamp == detail.analysis.data_as_of
        assert service.get_symbol("NOPE") is None
    finally:
        await service.stop()
        await market_data.stop()


# --- API ----------------------------------------------------------------------------------------

FORBIDDEN_TRADE_KEYS = {"order", "orders", "quantity", "stop_loss", "target", "entry_price", "side"}


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


def test_price_action_overview_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/market/price-action")
    assert response.status_code == 200
    body = response.json()
    assert len(body["symbols"]) == 51
    assert body["status"]["entry_timeframe"] == "5m"
    assert "not trade signals" in body["note"]
    row = body["symbols"][0]
    for key in ("patterns", "retracement_zone", "nearest_support", "alignment_score"):
        assert key in row


def test_symbol_price_action_endpoint(client: TestClient) -> None:
    response = client.get("/api/v1/market/price-action/reliance")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "RELIANCE"
    analysis = body["analysis"]
    assert {p["pattern"] for p in analysis["patterns"]} == {"COG", "NR", "NRB", "WRB", "BT", "TT"}
    for pattern in analysis["patterns"]:
        for key in ("detected", "direction", "confidence", "strength", "context", "reasons"):
            assert key in pattern
    retracement = analysis["retracement"]
    for key in (
        "retracement_percentage",
        "zone",
        "direction",
        "swing_high",
        "swing_low",
        "strength",
        "confidence",
    ):
        assert key in retracement
    mtf = analysis["multi_timeframe"]
    for key in (
        "context_analysis",
        "setup_analysis",
        "entry_analysis",
        "alignment_score",
        "overall_bias",
    ):
        assert key in mtf
    assert body["candles"]


def test_price_action_responses_contain_no_trade_instructions(client: TestClient) -> None:
    body = client.get("/api/v1/market/price-action/TCS").json()
    assert not (_keys(body) & FORBIDDEN_TRADE_KEYS)


def test_price_action_config_and_unknown_symbol(client: TestClient) -> None:
    config = client.get("/api/v1/market/price-action/config")
    assert config.status_code == 200
    assert config.json()["multi_timeframe"]["context_timeframe"] == "60m"
    assert config.json()["levels"]["weights"]["touches"] == 25.0
    assert client.get("/api/v1/market/price-action/NOPE").status_code == 404
