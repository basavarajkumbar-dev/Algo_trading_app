"""Everything a strategy may look at for one symbol, computed once.

The context bundles the Phase 4 market intelligence (engines + regime)
and the Phase 5 price action analysis for the same closed candles, so
every strategy sees one consistent snapshot. Strategies only read it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.market_data.base import utcnow
from app.market_data.schemas import Candle, Timeframe
from app.market_intelligence.analyzer import AnalysisConfig, SymbolAnalysis, analyze_candles
from app.price_action.analyzer import PriceActionAnalysis, analyze_price_action
from app.price_action.config import PriceActionConfig
from app.price_action.swing_structure import reference_atr
from app.regime.config import RegimeConfig
from app.regime.detector import classify_regime
from app.regime.schemas import RegimeResult


@dataclass
class StrategyContext:
    symbol: str
    timeframe: Timeframe
    candles: list[Candle]  # closed entry-timeframe candles, oldest first
    analysis: SymbolAnalysis  # Phase 4 engines on `candles`
    regime: RegimeResult
    price_action: PriceActionAnalysis  # Phase 5, same entry candles
    atr: float  # ATR(14) at the latest candle, as used by the price action engine
    relative_strength_rank: int | None = None
    universe_size: int | None = None
    now: datetime = field(default_factory=utcnow)

    @property
    def price(self) -> float:
        return self.candles[-1].close

    @property
    def latest(self) -> Candle:
        return self.candles[-1]


def build_context(
    symbol: str,
    candles_by_timeframe: dict[Timeframe, list[Candle]],
    *,
    benchmark_candles: list[Candle] | None = None,
    day_candles: list[Candle] | None = None,
    price_action: PriceActionAnalysis | None = None,
    price_action_config: PriceActionConfig | None = None,
    analysis_config: AnalysisConfig | None = None,
    regime_config: RegimeConfig | None = None,
    now: datetime | None = None,
) -> StrategyContext | None:
    """Build a context from closed candles. Returns None when there are
    no entry-timeframe candles yet. `price_action` may be passed in when
    it was already computed for exactly these candles."""

    pa_config = price_action_config or PriceActionConfig()
    entry_tf = pa_config.multi_timeframe.entry_timeframe
    candles = candles_by_timeframe.get(entry_tf, [])
    if not candles:
        return None
    stamp = now or utcnow()
    analysis = analyze_candles(
        symbol,
        entry_tf,
        candles,
        benchmark_candles=benchmark_candles,
        config=analysis_config,
    )
    regime = classify_regime(analysis, regime_config or RegimeConfig(), now=stamp)
    pa = price_action or analyze_price_action(
        symbol,
        candles_by_timeframe,
        day_candles=day_candles,
        config=pa_config,
        analysis_config=analysis_config,
        regime_config=regime_config,
        now=stamp,
    )
    return StrategyContext(
        symbol=symbol,
        timeframe=entry_tf,
        candles=candles,
        analysis=analysis,
        regime=regime,
        price_action=pa,
        atr=reference_atr(candles, pa_config.swings.atr_period),
        now=stamp,
    )
