# Backtesting (Phase 10)

A backtest replays the **same code** that runs paper trading — indicators,
regime detector, multi-timeframe and price-action analysis, the three
strategies, signal scoring, the risk manager, position sizing, the paper
engine with its stop / target / trailing logic, the paper broker and the
cost model — bar by bar over past data.

> Backtest results describe how these rules behaved on this history under
> the stated fill, slippage and cost assumptions. They do not predict or
> guarantee future performance. The built-in data source is **synthetic**;
> a result on it tests the machinery, not a market edge.

## How a run works

1. **Signal replay (phase A).** For every symbol, every 5-minute session
   bar (09:15–15:30 IST) is fed in order. At the close of each
   entry-timeframe bar (5m or 15m) the analysis runs on the bars that had
   closed by then, and each strategy's candidates are scored. NIFTY 50 is
   replayed first so every decision gets the market context that existed
   at that time. No decisions after 15:00 IST. Results are cached on disk
   per symbol, data fingerprint and strategy parameters
   (`BACKTEST_CACHE_DIR`), so changing only execution parameters (score
   threshold, trailing stop) or risk/costs re-uses them.
2. **Execution replay (phase B).** The signals drive the real
   `PaperTradingEngine` + `RiskManager` + `PaperBrokerAdapter` on a
   simulated clock. One trade at a time, the best-ranked executable signal
   first, the same ranking as the live scanner.

## Bias controls

| Risk | Control |
|---|---|
| Look-ahead | `ClosedBarHistory` raises `LookAheadError` if a bar that closes after the decision time is requested. Higher timeframes (15m/30m/60m) are built from 5m bars anchored to 09:15 and only **complete** buckets are emitted. A regression test changes every bar *after* a decision and checks the decision does not change. |
| Fill bias | Entries are market orders filled at the **next bar's open** plus slippage (never the decision bar's close). Inside a bar the path is open → adverse extreme → favourable extreme → close, so when a bar touches both stop and target the stop is assumed first. A stop crossed inside a bar fills at its level plus slippage; a gap through it fills at the open. |
| Data leakage | Indicators and regime are recomputed from history only. Out-of-sample and test days are never seen by parameter selection. Warm-up days before the range are used for indicators only, never traded. |
| Survivorship | Universe membership is checked per day. The synthetic source and CSV sources with a `universe.csv` are point-in-time; a CSV source without one is flagged as possibly survivor-biased. |
| Overfitting | Grids are capped at 27 combinations. Selection maximises **mean R minus one standard error** (penalising few or noisy trades), averaged with neighbouring grid points so an isolated spike is not chosen; ties go to fewer changed parameters. Never "highest net profit". Sets with too few training trades (`min_trades_for_selection`) are ineligible; if none qualify, your parameters are used and the run says so. |

## Modes

- **Historical** — your parameters over the whole range. A grid only
  produces a sensitivity table; nothing is selected.
- **Out-of-sample** — the range is split by time (`oos_fraction`, default
  30% at the end). Grid combinations are compared on the in-sample part
  only; the chosen set runs once on the out-of-sample part, which is what
  the headline reports. `degradation` = OOS expectancy R ÷ IS expectancy R.
- **Walk-forward** — rolling (or anchored) windows of `wf_train_days`
  then `wf_test_days`. Each window selects on its training days and is
  scored on the following test days; the headline is the stitched test
  results. `efficiency` = mean test expectancy R ÷ mean train expectancy R.
- **No-edge control** (optional, any mode) — the same rules and settings
  on synthetic data with every source of predictability removed (no drift,
  mean reversion or breakout legs; same volatility, gaps and volume
  shapes). Rules that profit there point to a bias or luck.

## Results

Headline metrics: total trades, wins/losses, win rate, profit factor,
expectancy (₹ and R per trade), net P&L and return, maximum drawdown (₹
and % of peak), average win and loss, realised risk/reward (payoff) next
to the planned reward:risk, longest win and loss streaks, charges, average
holding time, exit reasons. **Sharpe and Sortino** are computed from daily
returns (×√252, risk-free 0) only with at least 20 trading days and some
variation; otherwise they are `n/a` with the reason.

R multiple = net P&L ÷ the risk the position was sized for (planned entry
to stop × quantity). R weights every trade equally; rupee P&L weights by
position size, which the max-position-value cap can shrink, so the two can
disagree in sign on small samples.

Robustness report: mean R with its standard deviation, 95% interval and
t-statistic; a seeded 1,000-sample bootstrap of the trades (5th / 50th /
95th percentile of net P&L and of max drawdown, probability of a net
loss); net P&L with charges doubled and with +5 bps slippage; the share of
gross profit from the three best trades and the result without the best
trade; breakdowns by regime, strategy and exit reason; and plain-English
**flags** (too few trades, interval includes zero, profit gone under cost
stress, concentration, selection at the edge of the grid, many
combinations tried, OOS or walk-forward deterioration, profit on no-edge
data).

## Storage

`strategy_versions` (semver + config hash and the parameters that produce
it), `parameter_sets` (hash-identified), `backtest_runs` (request, mode,
date range, universe, data source fingerprint, status, metrics,
robustness, full result), `backtest_trades`, `backtest_equity_points`
(per trade and per day). Runs queue on the server and execute one at a
time in a worker thread (the replay uses a process pool), so the API and
paper trading keep running; a restart marks interrupted runs FAILED.

## Your own data (CSV)

Set `BACKTEST_DATA_DIR` to a folder of `SYMBOL.csv` files with columns
`timestamp,open,high,low,close,volume`; `timestamp` is the 5-minute bar
start, IST if it has no offset. `NIFTY50.csv` (the index) is required: it provides the market context.
Invalid, duplicate and out-of-session bars are dropped and reported. An
optional `universe.csv` (`symbol,start_date,end_date`) gives point-in-time
membership. Without it, every run warns about survivorship bias.

## Limitations

- One position at a time, intraday only, cash equity costs; no partial
  fills, queue position or market impact beyond the slippage setting.
- The cross-symbol relative-strength rank is not replayed (it only
  changes explanation text, not scores).
- Exchange holidays are only those configured (`TRADING_HOLIDAYS` / the
  CSV source); the synthetic calendar is Monday–Friday.
- The signal replay costs roughly 15–40 ms per symbol per decision, so a
  first run over 5 symbols × 65 days takes several minutes on 2 CPUs;
  re-runs with the same strategy parameters take seconds to a few minutes.

## Sample backtests

Synthetic data (seed 42), RELIANCE, HDFCBANK, INFY, TCS, ICICIBANK,
2026-06-01 → 2026-08-28 (65 trading days), 5m entries, ₹1,00,000, 1% risk
per trade, 5 bps slippage, default costs. **These numbers come from
simulated prices and say nothing about real markets.**

| Run | Headline | Trades | Win rate | PF | Exp. R | Net P&L | Max DD | Sharpe | Flags |
|---|---|---|---|---|---|---|---|---|---|
| 1. Historical, all strategies (defaults) | full range | 48 | 60.4% | 2.50 | +0.52R | +₹9,581 | −₹1,495 | 4.68 | none |
| 2. Out-of-sample, trend continuation, 9-combo grid | last 30% (19 days) | 38 | 47.4% | 1.95 | +0.17R | +₹6,073 | −₹2,030 | n/a (<20 days) | 4 |
| 3. Walk-forward 20/10 days, 6-combo grid | 5 stitched test windows | 102 | 40.2% | 1.39 | +0.01R | +₹7,695 | −₹3,727 | 3.57 | 4 |
| 4. Historical, trend continuation, min score 65 + no-edge control | full range | 192 | 42.2% | 1.39 | +0.10R | +₹14,531 | −₹8,090 | 3.87 | 1 |
| 4 (control). Same rules on no-edge data | full range | 145 | 24.1% | 0.36 | −0.55R | −₹29,480 | | | |

What they show:

- **Run 1** passes every automatic check (95% interval of mean R
  +0.16R … +0.89R, t = 2.8, profit survives doubled charges). On
  synthetic data with built-in trending regimes that is expected, which
  is why the data warning stays on every synthetic run. The high Sharpe
  is a property of the simulator, not a target.
- **Run 2** chose min score 75 / no trailing on in-sample data by the
  penalised objective (in-sample +0.12R over 84 trades; lower scores were
  negative). Out-of-sample it made +0.17R over 38 trades, but the interval
  (−0.33R … +0.67R) includes zero, the chosen values sit at the edge of
  the grid, and 9 combinations were tried — all flagged.
- **Run 3** is the most honest of the four: across 5 walk-forward
  windows the test expectancy averaged ~+0.01R (efficiency 0.05; 3 of 5
  windows profitable). The rupee profit comes from position-size
  differences, not a per-trade edge.
- **Run 4** profits on the regime-driven data and loses heavily on the
  no-edge control (PF 0.36), which is what rules without a bias should
  do: the profit depends on the trends in the data, not on an artefact of
  the test.

Reproduce them from the Backtesting page or with `POST /api/v1/backtests`
(see `docs/api.md`).

The full stored results of the four runs (request, metrics, robustness,
segments, grid, versions) are in [`docs/backtest-samples/`](./backtest-samples/).
