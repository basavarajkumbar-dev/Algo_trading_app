"""The four added strategy families (range, breakout, volume, MA crossover):
a clean baseline is eligible with exact candidates; each required rule,
broken on its own, blocks the setup with a reason; shorts mirror longs;
breakout/volume entries are STOP triggers."""

from __future__ import annotations

import pytest

from app.market_intelligence.schemas import Relationship
from app.regime.schemas import MarketRegime
from app.strategies.breakout_trading import BreakoutTradingStrategy
from app.strategies.config import StrategyEngineConfig
from app.strategies.indicators import adx_series, rsi_series
from app.strategies.ma_crossover import MovingAverageCrossoverStrategy
from app.strategies.pdf_config import (
    BreakoutTradingConfig,
    MovingAverageCrossoverConfig,
    RangeTradingConfig,
)
from app.strategies.range_trading import RangeTradingStrategy
from app.strategies.router import StrategyRouter
from app.strategies.schemas import EntryType, StrategyName, TradeDirection
from app.strategies.volume_trading import VolumeTradingStrategy
from app.tests.candle_factory import candles_from_closes
from app.tests.strategy_scenarios import analysis, context, quiet_rows, regime

Row = tuple[float, float, float, float]


def _rows_from_closes(closes: list[float], wick: float = 0.1) -> list[Row]:
    rows: list[Row] = []
    prev = closes[0]
    for c in closes:
        rows.append((prev, max(prev, c) + wick, min(prev, c) - wick, c))
        prev = c
    return rows


def _mirror(rows: list[Row], pivot: float = 200.0) -> list[Row]:
    return [(pivot - o, pivot - lo, pivot - h, pivot - c) for o, h, lo, c in rows]


def _blockers(result) -> str:  # noqa: ANN001
    return " | ".join(result.blockers)


# --- indicators ---------------------------------------------------------------------------


def test_rsi_extremes_and_midpoint() -> None:
    assert rsi_series([float(i) for i in range(30)], 14)[-1] == 100.0
    assert rsi_series([float(30 - i) for i in range(30)], 14)[-1] == pytest.approx(0.0)
    flat_up_down = [100.0 + (i % 2) for i in range(40)]
    value = rsi_series(flat_up_down, 14)[-1]
    assert value is not None and 40 < value < 60
    assert rsi_series([1.0, 2.0], 14) == [None, None]


def test_adx_strong_trend_vs_chop() -> None:
    trend = candles_from_closes([100 + i * 0.5 for i in range(60)])
    adx, pdi, mdi = adx_series(trend, 14)
    assert adx[-1] is not None and adx[-1] > 40 and pdi[-1] > mdi[-1]  # type: ignore[operator]
    import math

    chop = candles_from_closes([100 + 2 * math.sin(i * 1.3) for i in range(80)])
    assert adx_series(chop, 14)[0][-1] < 25  # type: ignore[operator]


# --- A. range trading ---------------------------------------------------------------------


def _range_rows() -> list[Row]:
    cycle = [100.0, 101.0, 102.0, 103.0, 104.0, 103.0, 102.0, 101.0]
    closes = cycle * 5  # 40 candles oscillating 100..104
    closes += [103.4, 102.7, 102.0, 101.3, 100.6]  # slide toward support
    rows = _rows_from_closes(closes)
    rows.append((100.25, 100.75, 100.05, 100.7))  # bullish reversal candle at support
    return rows


def _range_ctx(rows: list[Row] | None = None, **kw):  # noqa: ANN003, ANN202
    rows = rows or _range_rows()
    return context(
        rows,
        analysis_=analysis(price=rows[-1][3], price_vs_vwap=kw.pop("vwap", Relationship.BELOW)),
        regime_=regime(kw.pop("regime", MarketRegime.RANGE)),
    )


def test_range_long_baseline() -> None:
    cfg = RangeTradingConfig(rsi_oversold=50)  # the scenario's RSI sits near 46
    r = RangeTradingStrategy(cfg).evaluate(_range_ctx())
    assert r.eligible, _blockers(r)
    assert r.strategy == StrategyName.RANGE_TRADING and r.direction == TradeDirection.LONG
    assert r.entry_type == EntryType.MARKET
    assert r.features["range_low"] == pytest.approx(99.9)
    assert r.stop_candidate == pytest.approx(99.9 - 0.25)
    # 80% of the way from 100.7 to the range high 104.1
    assert r.target_candidate == pytest.approx(100.7 + 0.8 * (104.1 - 100.7), abs=0.01)


def test_range_short_mirrors_long() -> None:
    cfg = RangeTradingConfig(rsi_oversold=50, rsi_overbought=50)
    rows = _mirror(_range_rows())
    r = RangeTradingStrategy(cfg).evaluate(_range_ctx(rows, vwap=Relationship.ABOVE))
    assert r.eligible, _blockers(r)
    assert r.direction == TradeDirection.SHORT
    assert r.stop_candidate == pytest.approx(200 - 99.9 + 0.25)


def test_range_rejected_in_a_strong_trend_regime() -> None:
    r = RangeTradingStrategy(RangeTradingConfig(rsi_oversold=50)).evaluate(
        _range_ctx(regime=MarketRegime.STRONG_BEARISH)
    )
    assert not r.eligible and "Not trending" in _blockers(r)


def test_range_rejected_without_rsi_confirmation() -> None:
    r = RangeTradingStrategy(RangeTradingConfig(rsi_oversold=20)).evaluate(_range_ctx())
    assert not r.eligible and "RSI" in _blockers(r)


def test_range_invalid_after_a_close_below_support() -> None:
    rows = _range_rows()[:-1] + [(100.2, 100.3, 99.2, 99.4)]  # closed beyond the range low
    r = RangeTradingStrategy(RangeTradingConfig(rsi_oversold=50)).evaluate(_range_ctx(rows))
    assert not r.eligible and "Range intact" in _blockers(r)


def test_range_needs_a_reversal_candle() -> None:
    rows = _range_rows()[:-1] + [(100.7, 100.75, 100.05, 100.25)]  # bearish candle
    r = RangeTradingStrategy(RangeTradingConfig(rsi_oversold=50)).evaluate(_range_ctx(rows))
    assert not r.eligible and "Reversal candle" in _blockers(r)


# --- B. breakout trading --------------------------------------------------------------------


def _breakout_rows() -> list[Row]:
    return [*quiet_rows(25), (100.4, 101.6, 100.3, 101.5)]


def _breakout_ctx(rows: list[Row] | None = None, volume: int = 3000):  # noqa: ANN202
    rows = rows or _breakout_rows()
    vols = [1000] * (len(rows) - 1) + [volume]
    return context(
        rows,
        analysis_=analysis(price=rows[-1][3], momentum=70.0),
        regime_=regime(MarketRegime.BREAKOUT),
        volumes=vols,
    )


def test_breakout_long_waits_for_a_trigger() -> None:
    r = BreakoutTradingStrategy().evaluate(_breakout_ctx())
    assert r.eligible, _blockers(r)
    assert r.direction == TradeDirection.LONG
    assert r.entry_type == EntryType.STOP and r.entry_valid_bars == 2
    assert r.entry_candidate == pytest.approx(101.6)  # above the breakout candle
    assert r.stop_candidate == pytest.approx(100.3 - 0.2)  # below the confirming low
    assert r.target_candidate == pytest.approx(101.6 + 2 * 1.5)  # measured move too small: 2R


def test_breakout_short_mirrors_long() -> None:
    rows = _mirror(_breakout_rows())
    ctx = context(
        rows,
        analysis_=analysis(price=rows[-1][3], momentum=30.0, price_vs_vwap=Relationship.BELOW),
        regime_=regime(MarketRegime.BREAKOUT),
        volumes=[1000] * (len(rows) - 1) + [3000],
    )
    r = BreakoutTradingStrategy().evaluate(ctx)
    assert r.eligible, _blockers(r)
    assert r.direction == TradeDirection.SHORT
    assert r.entry_candidate == pytest.approx(200 - 101.6)


def test_breakout_without_volume_is_rejected() -> None:
    r = BreakoutTradingStrategy().evaluate(_breakout_ctx(volume=1100))
    assert not r.eligible and "Breakout volume" in _blockers(r)


def test_a_level_merely_nearby_is_not_a_breakout() -> None:
    rows = [*quiet_rows(25), (100.3, 100.55, 100.2, 100.45)]  # close below the 100.5 high
    r = BreakoutTradingStrategy().evaluate(_breakout_ctx(rows))
    assert not r.eligible and r.direction == TradeDirection.NONE


def test_breakout_failure_back_inside_the_range() -> None:
    rows = [*_breakout_rows(), (101.4, 101.45, 100.0, 100.2)]
    vols = [1000] * 25 + [3000, 1500]
    ctx = context(
        rows,
        analysis_=analysis(price=rows[-1][3]),
        regime_=regime(MarketRegime.BREAKOUT),
        volumes=vols,
    )
    r = BreakoutTradingStrategy().evaluate(ctx)
    assert not r.eligible and "Still beyond the level" in _blockers(r)


def test_breakout_weak_candle_is_rejected() -> None:
    rows = [*quiet_rows(25), (100.4, 102.5, 100.3, 100.9)]  # long upper wick
    r = BreakoutTradingStrategy().evaluate(_breakout_ctx(rows))
    assert not r.eligible and "Strong breakout candle" in _blockers(r)


def test_breakout_retest_required_when_configured() -> None:
    cfg = BreakoutTradingConfig(require_retest=True)
    assert not BreakoutTradingStrategy(cfg).evaluate(_breakout_ctx()).eligible
    rows = [*_breakout_rows(), (101.4, 101.5, 100.6, 101.3)]  # came back to 100.5, held
    vols = [1000] * 25 + [3000, 1200]
    ctx = context(
        rows, analysis_=analysis(price=101.3), regime_=regime(MarketRegime.BREAKOUT), volumes=vols
    )
    r = BreakoutTradingStrategy(cfg).evaluate(ctx)
    assert r.eligible, _blockers(r)
    assert r.features["confirm_candle"] == "retest"
    assert r.entry_candidate == pytest.approx(101.5)  # above the retest candle


# --- C. volume-based ------------------------------------------------------------------------


def _volume_rows(last: Row = (100.0, 101.1, 99.9, 101.0)) -> list[Row]:
    closes = [90 + i * (10 / 59) for i in range(60)]  # steady uptrend 90 -> 100
    return [*_rows_from_closes(closes), last]


def _volume_ctx(rows: list[Row] | None = None, volume: int = 2500, **kw):  # noqa: ANN003, ANN202
    rows = rows or _volume_rows()
    return context(
        rows,
        analysis_=analysis(price=rows[-1][3], momentum=kw.pop("momentum", 68.0)),
        regime_=regime(kw.pop("regime", MarketRegime.STRONG_BULLISH)),
        volumes=[1000] * (len(rows) - 1) + [volume],
    )


def test_volume_continuation_long() -> None:
    r = VolumeTradingStrategy().evaluate(_volume_ctx())
    assert r.eligible, _blockers(r)
    assert r.direction == TradeDirection.LONG and r.features["setup_kind"] == "continuation"
    assert r.entry_type == EntryType.STOP
    assert r.entry_candidate == pytest.approx(101.1)
    assert r.stop_candidate == pytest.approx(99.9 - 0.2)


def test_high_volume_alone_is_not_a_signal() -> None:
    # bearish high-volume candle in an uptrend, at no level: no setup
    r = VolumeTradingStrategy().evaluate(_volume_ctx(_volume_rows((100.2, 100.3, 99.0, 99.2))))
    assert not r.eligible and "Trend or level" in _blockers(r)


def test_volume_needs_expansion() -> None:
    r = VolumeTradingStrategy().evaluate(_volume_ctx(volume=1500))
    assert not r.eligible and "Volume expansion" in _blockers(r)


def test_volume_reversal_at_support() -> None:
    from app.price_action.schemas import LevelType
    from app.price_action.support_resistance import LevelRole
    from app.tests.strategy_scenarios import level

    closes = [100 - i * (6 / 59) for i in range(60)]  # downtrend 100 -> 94
    rows = [*_rows_from_closes(closes), (94.2, 94.4, 93.0, 94.3)]  # long lower wick at 93
    support = level("swing low", LevelType.SWING_LOW, 93.05, 94.3, strength=70)
    assert support.role == LevelRole.SUPPORT
    ctx = context(
        rows,
        analysis_=analysis(price=94.3, momentum=45.0),
        regime_=regime(MarketRegime.RANGE),
        volumes=[1000] * 60 + [2600],
        levels=[support],
    )
    r = VolumeTradingStrategy().evaluate(ctx)
    assert r.eligible, _blockers(r)
    assert r.direction == TradeDirection.LONG and r.features["setup_kind"] == "reversal"


# --- D. moving-average crossover ------------------------------------------------------------


def _cross_closes() -> list[float]:
    down = [110 - i * (10 / 49) for i in range(50)]  # 110 -> 100
    up = [100.6, 101.3, 102.0, 102.8, 103.6, 104.4, 105.2, 106.0, 106.8]
    return down + up


def _cross_ctx(closes: list[float] | None = None, **kw):  # noqa: ANN003, ANN202
    rows = _rows_from_closes(closes or _cross_closes(), wick=0.15)
    return context(
        rows,
        analysis_=analysis(price=rows[-1][3]),
        regime_=regime(kw.pop("regime", MarketRegime.BREAKOUT)),
    )


def _first_cross_lag(closes: list[float]) -> int:
    from app.strategies.indicators import moving_average

    f, s = moving_average(closes, 9), moving_average(closes, 21)
    sign = [None if a is None or b is None else a > b for a, b in zip(f, s, strict=True)]
    i = next(i for i in range(1, len(sign)) if sign[i] and sign[i - 1] is False)
    return len(closes) - 1 - i


def test_ma_crossover_long() -> None:
    closes = _cross_closes()
    lag = _first_cross_lag(closes)
    cfg = MovingAverageCrossoverConfig(
        max_bars_since_cross=max(lag, 1), min_adx=15, swing_lookback=2
    )
    r = MovingAverageCrossoverStrategy(cfg).evaluate(_cross_ctx(closes))
    assert r.eligible, _blockers(r)
    assert r.direction == TradeDirection.LONG and r.entry_type == EntryType.MARKET
    assert r.features["bars_since_cross"] == lag


def test_ma_crossover_stale_cross_is_rejected() -> None:
    closes = _cross_closes()
    lag = _first_cross_lag(closes)
    if lag < 2:
        pytest.skip("scenario crosses on the last candle")
    cfg = MovingAverageCrossoverConfig(max_bars_since_cross=lag - 1, min_adx=15, swing_lookback=2)
    r = MovingAverageCrossoverStrategy(cfg).evaluate(_cross_ctx(closes))
    assert not r.eligible and "Confirmed crossover" in _blockers(r)


def test_ma_crossover_rejected_in_chop() -> None:
    chop = [100 + (1.2 if (i // 3) % 2 else -1.2) for i in range(60)]
    r = MovingAverageCrossoverStrategy(
        MovingAverageCrossoverConfig(max_bars_since_cross=6)
    ).evaluate(_cross_ctx(chop))
    assert not r.eligible
    assert (
        "Not choppy" in _blockers(r)
        or "Trend strength" in _blockers(r)
        or not r.features.get("cross_direction")
    )


def test_fast_period_must_be_shorter() -> None:
    with pytest.raises(ValueError):
        MovingAverageCrossoverConfig(fast_period=30, slow_period=21)


# --- integration with the router ------------------------------------------------------------


def test_added_strategies_are_off_by_default_and_routed_when_enabled() -> None:
    router = StrategyRouter()
    decision = router.route(MarketRegime.RANGE, 80.0)
    assert StrategyName.RANGE_TRADING not in decision.eligible_strategies
    assert decision.eligible_strategies == [
        StrategyName.MEAN_REVERSION,
        StrategyName.BREAKOUT_MOMENTUM,
    ]  # unchanged default routing
    cfg = StrategyEngineConfig()
    cfg.range_trading.enabled = True
    cfg.breakout_trading.enabled = True
    on = StrategyRouter(cfg).route(MarketRegime.RANGE, 80.0)
    assert StrategyName.RANGE_TRADING in on.eligible_strategies
    assert StrategyName.BREAKOUT_TRADING in on.eligible_strategies


def test_versions_are_traceable() -> None:
    for strategy in (
        RangeTradingStrategy(),
        BreakoutTradingStrategy(),
        VolumeTradingStrategy(),
        MovingAverageCrossoverStrategy(),
    ):
        assert strategy.version_id.startswith("1.0.0+")
