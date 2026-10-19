# Strategies from "Best Intraday Trading Strategies" (howtotrade.com PDF)

Four strategies were added as separate, versioned modules. The news-based
strategy in the PDF was deliberately **not** implemented. The PDF describes
ideas only and gives **no numeric parameters**: every number below is a
documented default chosen for this project, exposed in the config and in the
backtester's parameter specs. None of them has been shown to be profitable;
implementing a strategy is not evidence that it makes money. All four are
**off by default** (`STRATEGY_EXTRA_ENABLED`, or `strategy=ALL_EXTENDED` /
the individual names in a backtest).

Shared rules: closed candles only (no look-ahead; tested), stop and targets
from the strategy, R:R and geometry checked by `CandidateGeometry`, the
score is a model score and not a probability, missing confirmation or stale
data means NO TRADE.

| Module | Version | Entry | PDF idea | Implementation (defaults) |
|---|---|---|---|---|
| `range_trading.py` | 1.0.0 | MARKET next bar | Fade support/resistance in a range, RSI near the edge | 40-bar range, ≥2 touches per side (±0.3 ATR), width 2.5–10 ATR, ADX(14) < 25, price in outer 25 %, RSI(14) ≤35 / ≥65, target 80 % of the way across, stop 0.25 ATR beyond the boundary |
| `breakout_trading.py` | 1.0.0 | **STOP** at the trigger, valid 2 bars | Breakout confirmed by volume and a strong candle or a retest; stop near the level | 20-bar level ≤6 ATR wide, close ≥0.1 ATR beyond, relative volume ≥1.5, body ≥50 %, close in the outer 30 %, or a retest within 0.25 ATR; ≤3 bars since breakout, ≤1.5 ATR extension; stop 0.2 ATR inside the level |
| `volume_trading.py` | 1.0.0 | **STOP**, valid 2 bars | Volume spikes as continuation, or rejection at supply/demand, combined with MAs | Relative volume ≥2.0 vs the same time of day over 10 sessions (or rolling 20); continuation: EMA20/EMA50 trend + strong body; zone: level strength ≥50 within 0.3 ATR + ≥50 % rejection wick |
| `ma_crossover.py` | 1.0.0 | MARKET next bar | Fast/slow MA crossover confirmed by ADX | EMA 9/21 (or SMA), cross ≤3 bars ago, ADX(14) ≥20, ≤2 crosses in 30 bars (choppy filter), stop beyond the 5-bar swing + 0.2 ATR |

STOP entry: the trade fills only if price trades through the trigger within
the validity window; otherwise it expires ("trigger not reached"). The same
rule is applied by the backtester, the paper engine (SL-M) and the live
order manager (SL-M, or a capped LIMIT if already through).

Trend Continuation was changed to 1.1.0 with a STOP entry (audit finding F1:
the "beyond the confirmation candle" rule was not enforced before). The
other original strategies are unchanged.

Tests: `backend/app/tests/test_pdf_strategies.py` (25), `test_entry_triggers.py` (8).
