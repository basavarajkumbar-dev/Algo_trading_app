"""Volume engine: relative volume, spikes, volume trend, and a 0-100
relative volume score (50 = average volume)."""

from __future__ import annotations

from app.market_data.schemas import Candle
from app.market_intelligence.schemas import EngineResult, InsufficientDataError, VolumeTrend
from app.market_intelligence.utils import mean

DEFAULT_AVERAGE_WINDOW = 20
DEFAULT_SPIKE_THRESHOLD = 2.0
DEFAULT_TREND_SHORT_WINDOW = 5
DEFAULT_TREND_LONG_WINDOW = 20
DEFAULT_TREND_TOLERANCE = 0.15
DEFAULT_CONFIRMATION_THRESHOLD = 1.2


class VolumeResult(EngineResult):
    current_volume: int
    average_volume: float
    relative_volume: float
    relative_volume_score: float
    is_spike: bool
    volume_trend: VolumeTrend
    confirms_price_move: bool


def relative_volume_score(relative_volume: float) -> float:
    """0-100, 50 at average volume, approaching 100 as volume grows:
    rv=2 -> 66.7, rv=3 -> 75."""

    if relative_volume <= 0:
        return 0.0
    return round(100.0 * relative_volume / (relative_volume + 1.0), 2)


def analyze_volume(
    candles: list[Candle],
    *,
    average_window: int = DEFAULT_AVERAGE_WINDOW,
    spike_threshold: float = DEFAULT_SPIKE_THRESHOLD,
    trend_short_window: int = DEFAULT_TREND_SHORT_WINDOW,
    trend_long_window: int = DEFAULT_TREND_LONG_WINDOW,
    trend_tolerance: float = DEFAULT_TREND_TOLERANCE,
    confirmation_threshold: float = DEFAULT_CONFIRMATION_THRESHOLD,
) -> VolumeResult:
    if len(candles) < 2:
        raise InsufficientDataError("Volume analysis needs at least 2 candles.")

    volumes = [c.volume for c in candles]
    current = volumes[-1]
    # Average excludes the current candle so a spike can't dilute itself.
    average = mean(volumes[-(average_window + 1) : -1])
    rv = current / average if average > 0 else 0.0
    is_spike = rv >= spike_threshold

    short_avg = mean(volumes[-trend_short_window:])
    long_avg = mean(volumes[-trend_long_window:])
    if long_avg <= 0:
        trend = VolumeTrend.STABLE
    elif short_avg > long_avg * (1 + trend_tolerance):
        trend = VolumeTrend.INCREASING
    elif short_avg < long_avg * (1 - trend_tolerance):
        trend = VolumeTrend.DECREASING
    else:
        trend = VolumeTrend.STABLE

    latest = candles[-1]
    confirms = rv >= confirmation_threshold and latest.close != latest.open

    reasons: list[str] = []
    if is_spike:
        reasons.append(f"Volume spike ({rv:.1f}x average)")
    elif rv >= confirmation_threshold:
        reasons.append(f"Above-average volume ({rv:.1f}x)")
    elif rv < 0.7:
        reasons.append(f"Light volume ({rv:.1f}x average)")
    if trend == VolumeTrend.INCREASING:
        reasons.append("Volume trending higher")
    elif trend == VolumeTrend.DECREASING:
        reasons.append("Volume trending lower")

    return VolumeResult(
        current_volume=current,
        average_volume=round(average, 2),
        relative_volume=round(rv, 4),
        relative_volume_score=relative_volume_score(rv),
        is_spike=is_spike,
        volume_trend=trend,
        confirms_price_move=confirms,
        reasons=reasons,
    )
