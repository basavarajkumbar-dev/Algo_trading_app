"""Candle aggregation engine.

Builds 1-minute candles directly from validated ticks, then aggregates
completed 1-minute candles into the higher timeframes
(3m/5m/15m/30m/60m) per PROJECT_CONTRACT.md's Candle Engine section.
All timestamps are UTC and represent the candle's bucket *start* time.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.market_data.schemas import AGGREGATE_TIMEFRAMES, Candle, Tick, Timeframe

#: How many closed candles to retain in memory per (symbol, timeframe).
DEFAULT_HISTORY_LENGTH = 500


def floor_to_timeframe(timestamp: datetime, minutes: int) -> datetime:
    """Floor a UTC timestamp to the start of its N-minute bucket, aligned
    to the top of the hour (so a 15m bucket always starts at :00/:15/:30/:45,
    not at an arbitrary offset from the first tick seen)."""

    ts = timestamp.astimezone(UTC).replace(second=0, microsecond=0)
    bucket_minute = (ts.minute // minutes) * minutes
    return ts.replace(minute=bucket_minute)


@dataclass
class _SymbolCandleState:
    history_length: int = DEFAULT_HISTORY_LENGTH
    current_1m: Candle | None = None
    history: dict[Timeframe, deque[Candle]] = field(init=False)
    current_higher: dict[Timeframe, Candle] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.history = {tf: deque(maxlen=self.history_length) for tf in Timeframe}


class CandleBuilder:
    """Stateful, per-symbol candle aggregation.

    Feed ticks in timestamp order via `process_tick`. Completed candles
    (1m and every higher timeframe) are appended to that symbol's
    history and can be read with `get_candles`. The in-progress
    (not-yet-closed) candle for a timeframe is available via
    `get_current_candle`.
    """

    def __init__(self, history_length: int = DEFAULT_HISTORY_LENGTH) -> None:
        self._history_length = history_length
        self._state: dict[str, _SymbolCandleState] = defaultdict(
            lambda: _SymbolCandleState(history_length=history_length)
        )

    def process_tick(self, tick: Tick) -> list[Candle]:
        """Feed one validated tick. Returns the list of candles (across
        all timeframes) that were *closed* as a result of this tick,
        if any — useful for the caller to know what to broadcast."""

        state = self._state[tick.symbol]
        bucket_start = floor_to_timeframe(tick.timestamp, Timeframe.M1.minutes)
        closed: list[Candle] = []

        current = state.current_1m
        if current is None:
            state.current_1m = self._new_candle(tick, Timeframe.M1, bucket_start)
        elif current.timestamp == bucket_start:
            self._merge_tick_into_candle(current, tick)
        else:
            # A new minute has started: close the previous 1m candle,
            # then aggregate it into every higher timeframe.
            current.is_closed = True
            state.history[Timeframe.M1].append(current)
            closed.append(current)
            closed.extend(self._aggregate_into_higher_timeframes(state, current))

            state.current_1m = self._new_candle(tick, Timeframe.M1, bucket_start)

        return closed

    def _aggregate_into_higher_timeframes(
        self, state: _SymbolCandleState, closed_1m: Candle
    ) -> list[Candle]:
        closed: list[Candle] = []

        for timeframe in AGGREGATE_TIMEFRAMES:
            bucket_start = floor_to_timeframe(closed_1m.timestamp, timeframe.minutes)
            current = state.current_higher.get(timeframe)

            if current is None or current.timestamp != bucket_start:
                # Starting a new higher-timeframe bucket. If a previous
                # one exists for a different (now-past) bucket, close it
                # first — it wasn't closed earlier because we only close
                # on bucket *change*, matching how real exchange candles
                # aren't known-closed until the next period begins.
                if current is not None and current.timestamp != bucket_start:
                    current.is_closed = True
                    state.history[timeframe].append(current)
                    closed.append(current)

                current = Candle(
                    symbol=closed_1m.symbol,
                    timeframe=timeframe,
                    timestamp=bucket_start,
                    open=closed_1m.open,
                    high=closed_1m.high,
                    low=closed_1m.low,
                    close=closed_1m.close,
                    volume=closed_1m.volume,
                    is_closed=False,
                )
                state.current_higher[timeframe] = current
            else:
                current.high = max(current.high, closed_1m.high)
                current.low = min(current.low, closed_1m.low)
                current.close = closed_1m.close
                current.volume += closed_1m.volume

        return closed

    @staticmethod
    def _new_candle(tick: Tick, timeframe: Timeframe, bucket_start: datetime) -> Candle:
        return Candle(
            symbol=tick.symbol,
            timeframe=timeframe,
            timestamp=bucket_start,
            open=tick.price,
            high=tick.price,
            low=tick.price,
            close=tick.price,
            volume=tick.volume,
            is_closed=False,
        )

    @staticmethod
    def _merge_tick_into_candle(candle: Candle, tick: Tick) -> None:
        candle.high = max(candle.high, tick.price)
        candle.low = min(candle.low, tick.price)
        candle.close = tick.price
        candle.volume += tick.volume

    def seed_history(self, symbol: str, timeframe: Timeframe, candles: list[Candle]) -> int:
        """Preload closed candles (e.g. provider warm-up history) for one
        symbol/timeframe so analysis engines have data immediately after
        startup instead of waiting hours for live candles to accumulate.

        Only closed candles strictly older than anything already in the
        history (or the in-progress candle) are accepted, so seeding can
        never overwrite or reorder live data. Returns the number seeded.
        """

        state = self._state[symbol]
        history = state.history[timeframe]

        earliest_existing = history[0].timestamp if history else None
        current = self.get_current_candle(symbol, timeframe)
        if current is not None and (
            earliest_existing is None or current.timestamp < earliest_existing
        ):
            earliest_existing = current.timestamp

        accepted = sorted(
            (
                c
                for c in candles
                if c.is_closed
                and c.timeframe == timeframe
                and (earliest_existing is None or c.timestamp < earliest_existing)
            ),
            key=lambda c: c.timestamp,
        )
        # Keep unique timestamps only.
        unique: list[Candle] = []
        for candle in accepted:
            if not unique or candle.timestamp != unique[-1].timestamp:
                unique.append(candle)

        existing = list(history)
        history.clear()
        history.extend([*unique, *existing])
        return len(unique)

    def get_candles(self, symbol: str, timeframe: Timeframe, limit: int = 100) -> list[Candle]:
        """Return up to `limit` most recent CLOSED candles, oldest first."""

        state = self._state.get(symbol)
        if state is None:
            return []

        return list(state.history[timeframe])[-limit:]

    def get_current_candle(self, symbol: str, timeframe: Timeframe) -> Candle | None:
        state = self._state.get(symbol)
        if state is None:
            return None
        if timeframe == Timeframe.M1:
            return state.current_1m
        return state.current_higher.get(timeframe)

    def get_candles_including_current(
        self, symbol: str, timeframe: Timeframe, limit: int = 100
    ) -> list[Candle]:
        candles = self.get_candles(symbol, timeframe, limit)
        current = self.get_current_candle(symbol, timeframe)
        if current is not None:
            candles = [*candles, current][-limit:]
        return candles
