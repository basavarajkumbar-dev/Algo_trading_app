"""Candle aggregation engine.

Builds 1-minute candles directly from validated ticks, then aggregates
completed 1-minute candles into the higher timeframes
(3m/5m/15m/30m/60m) per PROJECT_CONTRACT.md's Candle Engine section.
All timestamps are UTC and represent the candle's bucket *start* time.

Closing (Phase 13): a candle is CLOSED only once its period has really
ended, never before, and the in-progress candle is never returned as a
closed one. A candle closes when

  * a tick for a later period arrives (the 1-minute candle), or
  * the last minute of a higher-timeframe bucket closes, or
  * `finalize_due(now)` runs after the period's end (+ a small grace) and
    no tick came, so a quiet instrument's candles still close on time, or
  * the trading session ends (with a session configured): the last NSE
    60-minute candle is 15:15-15:30 IST, as in Kite's own candles.

Session anchoring (optional): with `session=NSE_SESSION` buckets are
anchored to the 09:15 IST open (so 30m / 60m candles start at 09:15,
09:45 / 09:15, 10:15 ... like Zerodha's), instead of the clock hour.
Without a session (the simulated 24/7 feed) buckets align to the hour.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.market_data.schemas import AGGREGATE_TIMEFRAMES, Candle, Tick, Timeframe

#: How many closed candles to retain in memory per (symbol, timeframe).
DEFAULT_HISTORY_LENGTH = 500
ONE_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True)
class SessionCalendar:
    """Trading session in the exchange's local time."""

    tz: str = "Asia/Kolkata"
    open: time = time(9, 15)
    close: time = time(15, 30)

    @property
    def zone(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def anchor_minute_utc(self) -> int:
        """Minute of the UTC day the session opens (09:15 IST = 03:45 UTC = 225)."""

        local = datetime.combine(datetime(2026, 1, 5).date(), self.open, tzinfo=self.zone)
        utc = local.astimezone(UTC)
        return utc.hour * 60 + utc.minute

    def close_on(self, ts: datetime) -> datetime:
        """Session close (UTC) of the trading day `ts` falls on."""

        local = ts.astimezone(self.zone)
        return datetime.combine(local.date(), self.close, tzinfo=self.zone).astimezone(UTC)


NSE_SESSION = SessionCalendar()


def floor_to_timeframe(timestamp: datetime, minutes: int, anchor_minute: int | None = None) -> datetime:
    """Floor a UTC timestamp to the start of its N-minute bucket.

    Without an anchor, buckets align to the top of the hour (a 15m bucket
    always starts at :00/:15/:30/:45). With `anchor_minute` (minute of the
    UTC day the session opens) buckets are counted from that minute."""

    ts = timestamp.astimezone(UTC).replace(second=0, microsecond=0)
    if anchor_minute is None:
        bucket_minute = (ts.minute // minutes) * minutes
        return ts.replace(minute=bucket_minute)
    offset = (anchor_minute % minutes) * 60
    epoch = int(ts.timestamp())
    start = ((epoch - offset) // (minutes * 60)) * (minutes * 60) + offset
    return datetime.fromtimestamp(start, tz=UTC)


def bucket_end(start: datetime, timeframe: Timeframe, session: SessionCalendar | None) -> datetime:
    end = start + timedelta(minutes=timeframe.minutes)
    if session is not None:
        close = session.close_on(start)
        if start < close < end:
            return close  # the last bucket of the day ends at the session close
    return end


def aggregate_candles(
    candles: list[Candle], timeframe: Timeframe, session: SessionCalendar | None = None
) -> list[Candle]:
    """Aggregate closed lower-timeframe candles into `timeframe` candles,
    using the same bucket alignment as live aggregation.

    Only complete buckets are returned: a bucket at either end of the
    input that is missing some of its source candles (for example the
    higher-timeframe candle still in progress) is dropped rather than
    reported as a closed candle with a partial range. With a session, a
    bucket cut short by the session close is complete when it has every
    source candle up to the close.
    """

    if not candles:
        return []
    source_minutes = candles[0].timeframe.minutes
    if timeframe.minutes % source_minutes != 0 or timeframe.minutes < source_minutes:
        raise ValueError(
            f"Cannot aggregate {candles[0].timeframe.value} candles into {timeframe.value}"
        )
    anchor = session.anchor_minute_utc() if session else None
    buckets: dict[datetime, list[Candle]] = {}
    for candle in sorted(candles, key=lambda c: c.timestamp):
        if not candle.is_closed:
            continue
        key = floor_to_timeframe(candle.timestamp, timeframe.minutes, anchor)
        buckets.setdefault(key, []).append(candle)

    result: list[Candle] = []
    for start, group in buckets.items():
        end = bucket_end(start, timeframe, session)
        expected = int((end - start).total_seconds() // 60) // source_minutes
        if len(group) != expected:
            continue
        result.append(
            Candle(
                symbol=group[0].symbol,
                timeframe=timeframe,
                timestamp=start,
                open=group[0].open,
                high=max(c.high for c in group),
                low=min(c.low for c in group),
                close=group[-1].close,
                volume=sum(c.volume for c in group),
                is_closed=True,
            )
        )
    return result


@dataclass
class _SymbolCandleState:
    history_length: int = DEFAULT_HISTORY_LENGTH
    current_1m: Candle | None = None
    history: dict[Timeframe, deque[Candle]] = field(init=False)
    current_higher: dict[Timeframe, Candle] = field(default_factory=dict)
    last_closed_1m: datetime | None = None

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

    def __init__(
        self,
        history_length: int = DEFAULT_HISTORY_LENGTH,
        *,
        session: SessionCalendar | None = None,
        timeframes: tuple[Timeframe, ...] = AGGREGATE_TIMEFRAMES,
        grace: timedelta = timedelta(seconds=2),
    ) -> None:
        self._history_length = history_length
        self.session = session
        self._anchor = session.anchor_minute_utc() if session else None
        self._timeframes = tuple(tf for tf in AGGREGATE_TIMEFRAMES if tf in timeframes)
        self._grace = grace
        self.late_ticks = 0
        self._state: dict[str, _SymbolCandleState] = defaultdict(
            lambda: _SymbolCandleState(history_length=history_length)
        )

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        return (Timeframe.M1, *self._timeframes)

    def _floor(self, ts: datetime, timeframe: Timeframe) -> datetime:
        return floor_to_timeframe(ts, timeframe.minutes, self._anchor)

    def process_tick(self, tick: Tick) -> list[Candle]:
        """Feed one validated tick. Returns the list of candles (across
        all timeframes) that were *closed* as a result of this tick,
        if any — useful for the caller to know what to broadcast."""

        state = self._state[tick.symbol]
        bucket_start = self._floor(tick.timestamp, Timeframe.M1)
        if state.last_closed_1m is not None and bucket_start <= state.last_closed_1m:
            self.late_ticks += 1  # its minute is already closed: never reopen a candle
            return []
        if self.session is not None and tick.timestamp >= self.session.close_on(tick.timestamp):
            self.late_ticks += 1  # after the session close (e.g. closing-auction prints)
            return []
        closed: list[Candle] = []

        current = state.current_1m
        if current is None:
            state.current_1m = self._new_candle(tick, Timeframe.M1, bucket_start)
        elif current.timestamp == bucket_start:
            self._merge_tick_into_candle(current, tick)
        else:
            # A new minute has started: close the previous 1m candle,
            # then aggregate it into every higher timeframe.
            closed.extend(self._close_1m(state))
            state.current_1m = self._new_candle(tick, Timeframe.M1, bucket_start)

        return closed

    def _close_1m(self, state: _SymbolCandleState) -> list[Candle]:
        current = state.current_1m
        assert current is not None
        current.is_closed = True
        state.history[Timeframe.M1].append(current)
        state.last_closed_1m = current.timestamp
        state.current_1m = None
        return [current, *self._aggregate_into_higher_timeframes(state, current)]

    def _close_higher(self, state: _SymbolCandleState, timeframe: Timeframe) -> Candle:
        candle = state.current_higher.pop(timeframe)
        candle.is_closed = True
        state.history[timeframe].append(candle)
        return candle

    def _aggregate_into_higher_timeframes(
        self, state: _SymbolCandleState, closed_1m: Candle
    ) -> list[Candle]:
        closed: list[Candle] = []
        minute_end = closed_1m.timestamp + ONE_MINUTE

        for timeframe in self._timeframes:
            bucket_start = self._floor(closed_1m.timestamp, timeframe)
            current = state.current_higher.get(timeframe)

            if current is not None and current.timestamp != bucket_start:
                # A previous bucket that never saw its last minute (a gap
                # in ticks): it is over now.
                closed.append(self._close_higher(state, timeframe))
                current = None

            if current is None:
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

            # The bucket's last minute just closed: the bucket is complete.
            if minute_end >= bucket_end(bucket_start, timeframe, self.session):
                closed.append(self._close_higher(state, timeframe))

        return closed

    def finalize_due(self, now: datetime) -> list[Candle]:
        """Close every candle whose period ended (plus the grace) without a
        tick from a later period. Call this regularly (every second)."""

        closed: list[Candle] = []
        for state in self._state.values():
            current = state.current_1m
            if current is not None:
                end = bucket_end(current.timestamp, Timeframe.M1, self.session)
                if now >= end + self._grace:
                    closed.extend(self._close_1m(state))
            for timeframe in list(state.current_higher):
                candle = state.current_higher[timeframe]
                if now >= bucket_end(candle.timestamp, timeframe, self.session) + self._grace:
                    closed.append(self._close_higher(state, timeframe))
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
        if timeframe == Timeframe.M1 and unique and state.last_closed_1m is None:
            state.last_closed_1m = unique[-1].timestamp
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

    def last_closed(self, symbol: str, timeframe: Timeframe) -> Candle | None:
        state = self._state.get(symbol)
        if state is None or not state.history[timeframe]:
            return None
        return state.history[timeframe][-1]
