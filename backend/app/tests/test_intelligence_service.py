"""MarketIntelligenceService, /market/intelligence + /market/regime API,
and the market_intelligence / market_regime WebSocket events."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.market_data.mock_provider import MockMarketDataProvider
from app.market_data.paper_provider import PaperMarketDataProvider
from app.market_data.schemas import Timeframe
from app.market_data.service import MarketDataService
from app.market_data.universe import NIFTY_50_UNIVERSE, NIFTY_INDEX_SYMBOL
from app.market_intelligence.service import MarketIntelligenceService
from app.regime.schemas import MarketRegime

WATCHLIST = [NIFTY_INDEX_SYMBOL, "RELIANCE", *(i.symbol for i in NIFTY_50_UNIVERSE[:9])]


class _RecordingSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, message: dict[str, Any]) -> None:
        self.messages.append(message)


async def _started_services(seed: int = 7) -> tuple[MarketDataService, MarketIntelligenceService]:
    provider = PaperMarketDataProvider(
        delegate=MockMarketDataProvider(
            rng_seed=seed, tick_interval_seconds=10.0, regime_rotation_seconds=None
        )
    )
    market_data = MarketDataService(provider, watchlist=WATCHLIST, warmup_candles=250)
    await market_data.start()
    intelligence = MarketIntelligenceService(
        market_data, benchmark_symbol=NIFTY_INDEX_SYMBOL, timeframe=Timeframe.M5, refresh_seconds=60
    )
    await intelligence.start()
    return market_data, intelligence


async def test_service_computes_snapshot_summaries_and_breadth_on_start() -> None:
    market_data, intelligence = await _started_services()
    try:
        snapshot = intelligence.get_snapshot()
        assert snapshot is not None
        assert snapshot.benchmark_symbol == NIFTY_INDEX_SYMBOL
        assert snapshot.market_regime in MarketRegime
        assert 0 <= snapshot.confidence <= 100

        breadth = snapshot.breadth
        assert breadth.symbols_counted == len(WATCHLIST) - 1
        assert breadth.advancing + breadth.declining + breadth.unchanged == breadth.symbols_counted
        assert sum(snapshot.regime_distribution.values()) == breadth.symbols_counted

        summaries = intelligence.get_summaries()
        assert len(summaries) == len(WATCHLIST)
        assert summaries[0].symbol == NIFTY_INDEX_SYMBOL  # benchmark listed first
        ranks = [s.relative_strength_rank for s in summaries[1:]]
        assert sorted(ranks) == list(range(1, len(WATCHLIST)))  # constituents ranked 1..n
        assert summaries[0].relative_strength_rank is None  # index isn't ranked against itself

        status = intelligence.status()
        assert status.is_running and status.refresh_count == 1 and status.last_error is None
    finally:
        await intelligence.stop()
        await market_data.stop()


async def test_symbol_intelligence_includes_every_engine_and_factors() -> None:
    market_data, intelligence = await _started_services()
    try:
        detail = intelligence.get_symbol("RELIANCE")
        assert detail is not None
        a = detail.analysis
        for engine in (
            "vwap",
            "moving_averages",
            "volatility",
            "momentum",
            "volume",
            "structure",
            "range",
            "relative_strength",
            "trend_quality",
        ):
            assert getattr(a, engine) is not None, engine
        assert {f.key for f in detail.factors} >= {"trend", "momentum", "vwap", "relative_strength"}
        assert intelligence.get_symbol("NOT_ON_WATCHLIST") is None

        index = intelligence.get_symbol(NIFTY_INDEX_SYMBOL)
        assert index is not None
        assert "breadth" in {f.key for f in index.factors}
    finally:
        await intelligence.stop()
        await market_data.stop()


async def test_analysis_uses_closed_candles_only() -> None:
    market_data, intelligence = await _started_services()
    try:
        detail = intelligence.get_symbol("RELIANCE")
        assert detail is not None
        closed = market_data.get_candles("RELIANCE", Timeframe.M5, 300, include_current=False)
        assert detail.analysis.data_as_of == closed[-1].timestamp
    finally:
        await intelligence.stop()
        await market_data.stop()


async def test_refresh_failure_keeps_last_good_results(monkeypatch: pytest.MonkeyPatch) -> None:
    market_data, intelligence = await _started_services()
    try:
        before = intelligence.get_snapshot()

        def boom(_inputs: Any) -> Any:
            raise RuntimeError("engine crashed")

        monkeypatch.setattr(intelligence, "_compute", boom)
        await intelligence.refresh()

        assert intelligence.get_snapshot() is before
        status = intelligence.status()
        assert status.last_error == "RuntimeError: engine crashed"
        assert status.refresh_count == 1
    finally:
        await intelligence.stop()
        await market_data.stop()


async def test_refresh_broadcasts_intelligence_and_regime_changes() -> None:
    market_data, intelligence = await _started_services()
    try:
        socket = _RecordingSocket()
        await market_data.connection_manager.connect(socket)  # type: ignore[arg-type]

        # Pretend RELIANCE was previously classified differently.
        current = intelligence.get_regime("RELIANCE")
        assert current is not None
        other = next(r for r in MarketRegime if r != current.regime)
        intelligence._regimes["RELIANCE"] = current.model_copy(update={"regime": other})

        await intelligence.refresh()

        types = [m["type"] for m in socket.messages]
        assert types.count("market_intelligence") == 1
        regime_messages = {m["symbol"]: m for m in socket.messages if m["type"] == "market_regime"}
        assert NIFTY_INDEX_SYMBOL in regime_messages  # benchmark is always sent
        assert regime_messages["RELIANCE"]["changed"] is True
        assert regime_messages["RELIANCE"]["previous_regime"] == other.value
    finally:
        await intelligence.stop()
        await market_data.stop()


async def test_warmup_seeds_history_and_session_open() -> None:
    provider = PaperMarketDataProvider(
        delegate=MockMarketDataProvider(
            rng_seed=3, tick_interval_seconds=10.0, regime_rotation_seconds=None
        )
    )
    market_data = MarketDataService(provider, watchlist=["RELIANCE"], warmup_candles=120)
    await market_data.start()
    try:
        candles = market_data.get_candles("RELIANCE", Timeframe.M5, 500, include_current=False)
        assert len(candles) == 120
        assert candles[-1].close == await provider.get_ltp(
            "RELIANCE"
        )  # history joins the live price
        snapshot = market_data.get_snapshot("RELIANCE")
        assert snapshot is not None
        assert market_data._day_open["RELIANCE"] == candles[-75].open  # 375-minute session open
    finally:
        await market_data.stop()


# --- HTTP API ---------------------------------------------------------------------


def test_market_intelligence_endpoint() -> None:
    with TestClient(app) as client:
        response = client.get("/api/v1/market/intelligence")
        assert response.status_code == 200
        body = response.json()
        snapshot = body["snapshot"]
        for key in (
            "market_direction",
            "market_regime",
            "confidence",
            "volatility",
            "trend_strength",
            "momentum",
            "timestamp",
            "breadth",
            "factors",
            "note",
        ):
            assert key in snapshot
        assert "not a forecast" in snapshot["note"]
        assert len(body["symbols"]) == 51
        assert body["status"]["symbols_analyzed"] == 51


def test_market_regime_endpoint_defaults_to_benchmark_and_accepts_symbol() -> None:
    with TestClient(app) as client:
        default = client.get("/api/v1/market/regime").json()
        assert default["symbol"] == NIFTY_INDEX_SYMBOL
        for key in ("regime", "confidence", "features", "reasons", "timestamp"):
            assert key in default
        assert client.get("/api/v1/market/regime?symbol=reliance").json()["symbol"] == "RELIANCE"
        assert client.get("/api/v1/market/regime?symbol=NOPE").status_code == 404


def test_symbol_intelligence_and_config_endpoints() -> None:
    with TestClient(app) as client:
        detail = client.get("/api/v1/market/intelligence/TCS")
        assert detail.status_code == 200
        assert detail.json()["analysis"]["moving_averages"]["ema_fast_period"] == 20
        assert client.get("/api/v1/market/intelligence/NOPE").status_code == 404

        config = client.get("/api/v1/market/intelligence/config").json()
        assert config["analysis"]["ma_period"] == 200
        assert config["regime"]["min_regime_score"] == 55.0


def test_intelligence_endpoints_return_503_when_service_missing() -> None:
    client = TestClient(app)  # no context manager: lifespan doesn't run
    previous = getattr(app.state, "market_intelligence_service", None)
    app.state.market_intelligence_service = None
    try:
        assert client.get("/api/v1/market/intelligence").status_code == 503
        assert client.get("/api/v1/market/regime").status_code == 503
    finally:
        app.state.market_intelligence_service = previous


def test_websocket_sends_current_intelligence_on_connect() -> None:
    with TestClient(app) as client:
        with client.websocket_connect("/api/v1/ws/market") as websocket:
            types = []
            for _ in range(60):  # ack + 51 snapshots + intelligence
                message = websocket.receive_json()
                types.append(message["type"])
                if message["type"] == "market_intelligence":
                    assert message["snapshot"]["benchmark_symbol"] == NIFTY_INDEX_SYMBOL
                    assert len(message["symbols"]) == 51
                    break
            assert "market_intelligence" in types
