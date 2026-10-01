"""Turn engine results into the 0-100 factor scores shown on the
Market Intelligence dashboard ("market brain" panel)."""

from __future__ import annotations

from app.market_intelligence.analyzer import SymbolAnalysis
from app.market_intelligence.models import FactorScore, MarketBreadth
from app.market_intelligence.schemas import Trend
from app.market_intelligence.utils import clamp, sigmoid_score

DIRECTIONAL = "directional"
INTENSITY = "intensity"


def _direction_label(score: float, neutral_band: float = 5.0) -> str:
    if score >= 50 + neutral_band:
        return "Bullish"
    if score <= 50 - neutral_band:
        return "Bearish"
    return "Neutral"


def vwap_score(analysis: SymbolAnalysis) -> float | None:
    """Directional: distance from VWAP measured in ATRs, squashed to
    0-100. One ATR above VWAP scores ~82, one ATR below ~18."""

    if analysis.vwap is None or analysis.volatility is None or analysis.volatility.atr <= 0:
        return None
    distance_atr = analysis.vwap.distance_from_vwap / analysis.volatility.atr
    return round(sigmoid_score(distance_atr, 1.5), 2)


def volatility_score(analysis: SymbolAnalysis) -> float | None:
    """Intensity: ATR relative to its baseline; 1.0x baseline = 40,
    2.5x or more = 100."""

    if analysis.volatility is None:
        return None
    return round(clamp(analysis.volatility.atr_ratio / 2.5 * 100.0), 2)


def build_factors(
    analysis: SymbolAnalysis, breadth: MarketBreadth | None = None
) -> list[FactorScore]:
    factors: list[FactorScore] = []

    tq = analysis.trend_quality
    if tq is not None:
        direction = {Trend.UP: "Uptrend", Trend.DOWN: "Downtrend"}.get(tq.direction, "No trend")
        factors.append(
            FactorScore(
                key="trend",
                label="Trend",
                score=tq.score,
                scale=INTENSITY,
                interpretation=direction,
                detail="Trend quality: direction, slope, structure, momentum, volume, volatility",
            )
        )

    mom = analysis.momentum
    if mom is not None:
        factors.append(
            FactorScore(
                key="momentum",
                label="Momentum",
                score=mom.momentum_score,
                scale=DIRECTIONAL,
                interpretation=_direction_label(mom.momentum_score),
                detail="Volatility-normalised rate of change over 3/10/20 candles",
            )
        )

    vs = vwap_score(analysis)
    if vs is not None and analysis.vwap is not None:
        factors.append(
            FactorScore(
                key="vwap",
                label="VWAP",
                score=vs,
                scale=DIRECTIONAL,
                interpretation=f"{analysis.vwap.price_vs_vwap.value.capitalize()} VWAP",
                detail=f"{analysis.vwap.distance_from_vwap_percent:+.2f}% from VWAP",
            )
        )

    vol = analysis.volume
    if vol is not None:
        label = (
            "Heavy"
            if vol.relative_volume >= 1.5
            else "Light" if vol.relative_volume < 0.7 else "Average"
        )
        factors.append(
            FactorScore(
                key="volume",
                label="Volume",
                score=vol.relative_volume_score,
                scale=INTENSITY,
                interpretation=label,
                detail=f"{vol.relative_volume:.2f}x the 20-candle average",
            )
        )

    vls = volatility_score(analysis)
    if vls is not None and analysis.volatility is not None:
        factors.append(
            FactorScore(
                key="volatility",
                label="Volatility",
                score=vls,
                scale=INTENSITY,
                interpretation=analysis.volatility.volatility_level.value.capitalize(),
                detail=f"ATR {analysis.volatility.atr_ratio:.2f}x baseline ({analysis.volatility.atr_percent:.2f}% of price)",
            )
        )

    st = analysis.structure
    if st is not None:
        trend_word = {Trend.UP: "Bullish", Trend.DOWN: "Bearish"}.get(st.trend, "Mixed")
        factors.append(
            FactorScore(
                key="structure",
                label="Structure",
                score=st.confidence,
                scale=INTENSITY,
                interpretation=f"{trend_word} ({st.structure_state.value.replace('_', ' ').lower()})",
                detail="Confidence in the swing-structure reading",
            )
        )

    rs = analysis.relative_strength
    if rs is not None:
        factors.append(
            FactorScore(
                key="relative_strength",
                label="Relative strength",
                score=rs.relative_strength_score,
                scale=DIRECTIONAL,
                interpretation="Outperforming" if rs.is_outperforming else "Underperforming",
                detail=f"{rs.relative_return_percent:+.2f}pp vs NIFTY50 over {rs.lookback} candles",
            )
        )
    elif breadth is not None and breadth.percent_above_vwap is not None:
        factors.append(
            FactorScore(
                key="breadth",
                label="Breadth",
                score=breadth.percent_above_vwap,
                scale=DIRECTIONAL,
                interpretation=_direction_label(breadth.percent_above_vwap, 10.0),
                detail=f"{breadth.above_vwap} of {breadth.symbols_counted} constituents above VWAP",
            )
        )

    return factors
