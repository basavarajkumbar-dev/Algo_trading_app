"""Support / resistance engine.

Collects candidate levels, measures how price has interacted with each,
and scores them with a configurable 0-100 Level Strength Score.

Levels:
- Previous day high / low / close and intraday (current day) high / low.
  Days are calendar days in `session_timezone` (IST by default); the
  simulated feed trades around the clock, so a simulated "day" is the
  full IST date rather than the 09:15-15:30 NSE session.
- Significant swing highs / lows (ATR zig-zag, swing_structure.py).
- Range high / low of the recent window. Once price closes beyond one,
  it is reported as a breakout / breakdown level instead.
- VWAP, EMAs and MAs (dynamic levels: touches are measured against the
  indicator's value at each candle, not today's value).
- Retracement levels of the current impulse.

Level Strength Score = weighted blend of
- type:        how much weight that kind of level usually carries
- touches:     distinct times price came within tolerance of it
- confluence:  other levels within a small distance
- recency:     how recently price touched it (exponential decay)
- timeframe:   levels from higher timeframes (and daily levels) weigh more

Every level is a reference zone, not a guaranteed turning point.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import Enum
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.market_data.schemas import Candle
from app.market_intelligence.moving_average import compute_ema_series, compute_sma_series
from app.market_intelligence.schemas import EngineResult, InsufficientDataError
from app.market_intelligence.utils import clamp
from app.market_intelligence.vwap import compute_vwap_series
from app.price_action.config import LevelConfig, SwingConfig
from app.price_action.retracement import RetracementResult
from app.price_action.schemas import LEVEL_DISCLAIMER, LevelType, ReferenceLevel
from app.price_action.swing_structure import PivotType, reference_atr, zigzag_pivots


class LevelRole(str, Enum):
    SUPPORT = "SUPPORT"
    RESISTANCE = "RESISTANCE"


class PriceLevel(BaseModel):
    label: str
    type: LevelType
    price: float
    role: LevelRole
    distance_percent: float  # signed: level minus price, as % of price
    distance_atr: float
    strength: float  # Level Strength Score, 0-100
    touch_count: int
    timeframe: str
    recency: int | None  # candles since the last touch (0 = latest candle)
    confluence: list[str] = Field(default_factory=list)
    components: dict[str, float] = Field(default_factory=dict)  # score points by component
    dynamic: bool = False  # VWAP / moving averages move with each candle


class SupportResistanceResult(EngineResult):
    price: float
    atr: float
    timeframe: str
    session_date: date | None
    previous_session_date: date | None
    levels: list[PriceLevel]  # highest price first
    nearest_support: PriceLevel | None
    nearest_resistance: PriceLevel | None
    strongest_support: PriceLevel | None
    strongest_resistance: PriceLevel | None
    unavailable: dict[str, str] = Field(default_factory=dict)
    note: str = LEVEL_DISCLAIMER


@dataclass
class _Candidate:
    label: str
    type: LevelType
    price: float
    timeframe: str
    series: list[float | None] | None = None  # per-candle values for dynamic levels


def _local_date(candle: Candle, tz: ZoneInfo) -> date:
    return candle.timestamp.astimezone(tz).date()


def _day_candidates(
    candles: list[Candle],
    day_candles: list[Candle],
    tz: ZoneInfo,
    unavailable: dict[str, str],
) -> tuple[list[_Candidate], date | None, date | None]:
    today = _local_date(candles[-1], tz)
    by_day: dict[date, list[Candle]] = {}
    for candle in day_candles:
        by_day.setdefault(_local_date(candle, tz), []).append(candle)

    found: list[_Candidate] = []
    earlier = sorted(d for d in by_day if d < today)
    previous = earlier[-1] if earlier else None
    if previous is not None:
        day = by_day[previous]
        found += [
            _Candidate(
                "Prev day high", LevelType.PREVIOUS_DAY_HIGH, max(c.high for c in day), "1d"
            ),
            _Candidate("Prev day low", LevelType.PREVIOUS_DAY_LOW, min(c.low for c in day), "1d"),
            _Candidate("Prev close", LevelType.PREVIOUS_CLOSE, day[-1].close, "1d"),
        ]
    else:
        unavailable["previous_day"] = "No candles from an earlier day in the available history."

    intraday = [c for c in candles if _local_date(c, tz) == today]
    if intraday:
        found += [
            _Candidate("Day high", LevelType.INTRADAY_HIGH, max(c.high for c in intraday), "1d"),
            _Candidate("Day low", LevelType.INTRADAY_LOW, min(c.low for c in intraday), "1d"),
        ]
    return found, today, previous


def _touches(
    candles: Sequence[Candle], level: _Candidate, tolerance: float, offset: int
) -> tuple[int, int | None]:
    """Distinct touch episodes and the index (within `candles`) of the
    last touching candle. Consecutive touching candles count once."""

    episodes = 0
    last: int | None = None
    touching = False
    for k, candle in enumerate(candles):
        if level.series is not None:
            value = level.series[offset + k]
            if value is None:
                touching = False
                continue
        else:
            value = level.price
        touch = candle.low - tolerance <= value <= candle.high + tolerance
        if touch:
            if not touching:
                episodes += 1
            last = k
        touching = touch
    return episodes, last


def score_level(
    level_type: LevelType,
    timeframe: str,
    touches: int,
    recency: int | None,
    confluence: int,
    config: LevelConfig,
) -> tuple[float, dict[str, float]]:
    """The Level Strength Score (0-100) and its points by component."""

    w = config.weights
    scores = {
        "type": config.type_weights.get(level_type, 0.5),
        "touches": min(touches, config.touch_cap) / config.touch_cap,
        "confluence": min(confluence, config.confluence_cap) / config.confluence_cap,
        "recency": 0.5 ** (recency / config.recency_half_life) if recency is not None else 0.0,
        "timeframe": config.timeframe_weights.get(timeframe, 0.5),
    }
    weights = {
        "type": w.type,
        "touches": w.touches,
        "confluence": w.confluence,
        "recency": w.recency,
        "timeframe": w.timeframe,
    }
    total = sum(weights.values()) or 1.0
    points = {k: round(100.0 * weights[k] * scores[k] / total, 1) for k in scores}
    return round(clamp(sum(points.values())), 1), points


def analyze_support_resistance(
    candles: list[Candle],
    *,
    day_candles: list[Candle] | None = None,
    retracement: RetracementResult | None = None,
    config: LevelConfig | None = None,
    swing_config: SwingConfig | None = None,
) -> SupportResistanceResult:
    cfg = config or LevelConfig()
    swing_cfg = swing_config or SwingConfig()
    if len(candles) < cfg.range_window + 2:
        raise InsufficientDataError(
            f"Support/resistance needs at least {cfg.range_window + 2} candles."
        )

    timeframe = candles[-1].timeframe.value
    tz = ZoneInfo(cfg.session_timezone)
    price = candles[-1].close
    atr = reference_atr(candles, swing_cfg.atr_period)
    unavailable: dict[str, str] = {}

    candidates, today, previous = _day_candidates(candles, day_candles or candles, tz, unavailable)

    pivots = [p for p in zigzag_pivots(candles, swing_cfg.reversal_atr * atr) if p.confirmed]
    for kind, label, level_type in (
        (PivotType.HIGH, "Swing high", LevelType.SWING_HIGH),
        (PivotType.LOW, "Swing low", LevelType.SWING_LOW),
    ):
        for pivot in [p for p in pivots if p.type == kind][-cfg.swing_levels :]:
            candidates.append(_Candidate(label, level_type, pivot.price, timeframe))

    window = candles[-(cfg.range_window + 1) : -1]
    range_high = max(c.high for c in window)
    range_low = min(c.low for c in window)
    if price > range_high:
        candidates.append(
            _Candidate("Breakout level", LevelType.BREAKOUT_LEVEL, range_high, timeframe)
        )
    else:
        candidates.append(_Candidate("Range high", LevelType.RANGE_HIGH, range_high, timeframe))
    if price < range_low:
        candidates.append(
            _Candidate("Breakdown level", LevelType.BREAKDOWN_LEVEL, range_low, timeframe)
        )
    else:
        candidates.append(_Candidate("Range low", LevelType.RANGE_LOW, range_low, timeframe))

    anchor = candles[-cfg.vwap_anchor_candles :]
    vwap_tail = compute_vwap_series(anchor)
    vwap_series: list[float | None] = [None] * (len(candles) - len(anchor)) + list(vwap_tail)
    candidates.append(_Candidate("VWAP", LevelType.VWAP, vwap_tail[-1], timeframe, vwap_series))

    closes = [c.close for c in candles]
    for period in cfg.ema_periods:
        series = compute_ema_series(closes, period)
        if series and series[-1] is not None:
            candidates.append(
                _Candidate(f"EMA {period}", LevelType.EMA, series[-1], timeframe, series)
            )
        else:
            unavailable[f"ema_{period}"] = f"Needs {period} candles."
    for period in cfg.ma_periods:
        series = compute_sma_series(closes, period)
        if series and series[-1] is not None:
            candidates.append(
                _Candidate(f"MA {period}", LevelType.MA, series[-1], timeframe, series)
            )
        else:
            unavailable[f"ma_{period}"] = f"Needs {period} candles."

    if retracement is not None and retracement.detected:
        for level in retracement.levels:
            if level.percent < 100:
                candidates.append(
                    _Candidate(
                        f"Retracement {level.percent:g}%",
                        LevelType.RETRACEMENT,
                        level.price,
                        timeframe,
                    )
                )

    candidates = [c for c in candidates if abs(c.price - price) <= cfg.max_distance_atr * atr]

    recent = candles[-cfg.touch_lookback :]
    offset = len(candles) - len(recent)
    tolerance = cfg.touch_tolerance_atr * atr
    confluence_distance = cfg.confluence_tolerance_atr * atr
    levels: list[PriceLevel] = []
    for candidate in candidates:
        touches, last = _touches(recent, candidate, tolerance, offset)
        recency = len(recent) - 1 - last if last is not None else None
        confluence = sorted(
            {
                other.label
                for other in candidates
                if other is not candidate
                and abs(other.price - candidate.price) <= confluence_distance
            }
        )
        strength, points = score_level(
            candidate.type, candidate.timeframe, touches, recency, len(confluence), cfg
        )
        levels.append(
            PriceLevel(
                label=candidate.label,
                type=candidate.type,
                price=round(candidate.price, 4),
                role=LevelRole.SUPPORT if candidate.price <= price else LevelRole.RESISTANCE,
                distance_percent=round((candidate.price - price) / price * 100.0, 3),
                distance_atr=round((candidate.price - price) / atr, 2) if atr else 0.0,
                strength=strength,
                touch_count=touches,
                timeframe=candidate.timeframe,
                recency=recency,
                confluence=confluence,
                components=points,
                dynamic=candidate.series is not None,
            )
        )

    levels.sort(key=lambda lvl: lvl.price, reverse=True)
    supports = [lvl for lvl in levels if lvl.role == LevelRole.SUPPORT]
    resistances = [lvl for lvl in levels if lvl.role == LevelRole.RESISTANCE]
    nearest_support = max(supports, key=lambda lvl: lvl.price, default=None)
    nearest_resistance = min(resistances, key=lambda lvl: lvl.price, default=None)
    strongest_support = max(supports, key=lambda lvl: lvl.strength, default=None)
    strongest_resistance = max(resistances, key=lambda lvl: lvl.strength, default=None)

    reasons = [f"{len(levels)} levels within {cfg.max_distance_atr:g} ATR of price"]
    if nearest_support is not None:
        reasons.append(
            f"Nearest support: {nearest_support.label} {nearest_support.price:g} "
            f"({abs(nearest_support.distance_atr):.1f} ATR below, strength {nearest_support.strength:.0f})"
        )
    if nearest_resistance is not None:
        reasons.append(
            f"Nearest resistance: {nearest_resistance.label} {nearest_resistance.price:g} "
            f"({nearest_resistance.distance_atr:.1f} ATR above, strength {nearest_resistance.strength:.0f})"
        )

    return SupportResistanceResult(
        price=price,
        atr=round(atr, 4),
        timeframe=timeframe,
        session_date=today,
        previous_session_date=previous,
        levels=levels,
        nearest_support=nearest_support,
        nearest_resistance=nearest_resistance,
        strongest_support=strongest_support,
        strongest_resistance=strongest_resistance,
        unavailable=unavailable,
        reasons=reasons,
    )


def reference_levels(
    result: SupportResistanceResult, *, min_strength: float = 0.0
) -> list[ReferenceLevel]:
    """Levels in the minimal form the pattern detectors use as context."""

    return [
        ReferenceLevel(label=lvl.label, price=lvl.price)
        for lvl in result.levels
        if lvl.strength >= min_strength
    ]
