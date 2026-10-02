"use client";

import { AlertTriangle, Info } from "lucide-react";
import { Fragment, useMemo, useState, type ReactNode } from "react";

import { GroupTable } from "@/components/analytics/group-table";
import { BacktestDrawdownChart, BacktestEquityChart, equityRows } from "@/components/backtest/backtest-charts";
import { Figure, Panel, errorText } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  MODE_LABEL,
  SEGMENT_LABEL,
  STATUS_VARIANT,
  formatSeconds,
  formatShare,
  isActive,
  paramDisplay,
} from "@/lib/backtest-format";
import { formatPct, formatRatio, strategyLabel } from "@/lib/dashboard-format";
import { useBacktestEquity, useBacktestRun, useBacktestTrades } from "@/lib/queries";
import { EXIT_REASON_LABEL, formatInr, formatR, pnlTone } from "@/lib/trading-format";
import { cn } from "@/lib/utils";
import type { BacktestMetrics, BacktestTrade, RunDetail } from "@/types/backtest";
import type { ExitReason } from "@/types/trading";

const IST_DT = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});
const dt = (iso: string | null) => (iso ? IST_DT.format(new Date(iso)) : "—");
const exitLabel = (r: string | null) => (r ? (EXIT_REASON_LABEL[r as ExitReason] ?? strategyLabel(r)) : "—");

function MetricsGrid({ m, testId }: { m: BacktestMetrics; testId?: string }) {
  return (
    <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4 xl:grid-cols-6" data-testid={testId}>
      <Figure label="Total trades" hint={`${m.wins}W / ${m.losses}L · ${m.trading_days} days`} testId="bt-m-trades">
        {m.total_trades}
      </Figure>
      <Figure label="Win rate" testId="bt-m-winrate">
        {formatPct(m.win_rate)}
      </Figure>
      <Figure label="Profit factor" hint={m.profit_factor === null && m.total_trades ? "no losing trades" : "gross profit ÷ gross loss"} testId="bt-m-pf">
        {formatRatio(m.profit_factor)}
      </Figure>
      <Figure label="Expectancy" hint={`${formatR(m.expectancy_r)} per trade`} testId="bt-m-exp">
        <span className={pnlTone(m.expectancy)}>{formatInr(m.expectancy, true)}</span>
      </Figure>
      <Figure label="Net P&L" hint={`${m.return_percent >= 0 ? "+" : ""}${m.return_percent.toFixed(2)}% of capital`} testId="bt-m-net">
        <span className={pnlTone(m.net_pnl)}>{formatInr(m.net_pnl, true)}</span>
      </Figure>
      <Figure label="Max drawdown" hint={`${m.max_drawdown_percent.toFixed(2)}% from peak`} testId="bt-m-dd">
        <span className={m.max_drawdown < 0 ? "text-loss" : ""}>{formatInr(m.max_drawdown)}</span>
      </Figure>
      <Figure label="Average win">{formatInr(m.average_win)}</Figure>
      <Figure label="Average loss">{formatInr(m.average_loss)}</Figure>
      <Figure label="Risk / reward" hint={`realised; planned ${formatRatio(m.average_planned_rr)}`} testId="bt-m-rr">
        {formatRatio(m.payoff_ratio)}
      </Figure>
      <Figure label="Consecutive" hint="longest win / loss streak" testId="bt-m-streaks">
        <span className="text-profit">{m.max_consecutive_wins}W</span> / <span className="text-loss">{m.max_consecutive_losses}L</span>
      </Figure>
      <Figure label="Sharpe" hint={m.sharpe === null ? m.sharpe_note : `Sortino ${formatRatio(m.sortino)} · daily, ×√252`} testId="bt-m-sharpe">
        {m.sharpe === null ? <span className="text-muted">n/a</span> : formatRatio(m.sharpe)}
      </Figure>
      <Figure label="Charges" hint={`avg hold ${m.average_holding_minutes === null ? "—" : `${Math.round(m.average_holding_minutes)} min`}`}>
        {formatInr(m.charges)}
      </Figure>
    </div>
  );
}

function Row({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-4 border-b border-border/60 py-1.5 last:border-0">
      <span className="text-xs text-muted">
        {label}
        {hint && <span className="block text-[10px] text-muted/80">{hint}</span>}
      </span>
      <span className="tabular text-right text-sm">{children}</span>
    </div>
  );
}

function RobustnessPanel({ run }: { run: RunDetail }) {
  const r = run.robustness!;
  const net = run.metrics!.net_pnl;
  return (
    <Panel title="Robustness" testId="bt-robustness" aside={<span className="text-[11px] text-muted">how fragile is the result?</span>}>
      <div className="grid grid-cols-1 gap-x-8 lg:grid-cols-2">
        <div>
          <Row label="Mean R per trade" hint={`std ${formatRatio(r.r_std)}R over ${r.trades} trades`}>
            {formatR(r.mean_r)}
          </Row>
          <Row label="95% interval of mean R" hint="includes zero → no evidence of an edge">
            <span data-testid="bt-r-ci">{r.r_ci95 ? `${formatR(r.r_ci95[0])} … ${formatR(r.r_ci95[1])}` : "—"}</span>
          </Row>
          <Row label="t-statistic" hint="≈ 2 or more before taking an edge seriously">
            {formatRatio(r.t_stat)}
          </Row>
          <Row label="Enough trades?" hint="30+ closed trades">
            {r.enough_trades ? "yes" : <span className="text-warning">no</span>}
          </Row>
          <Row label="Top 3 trades' share of profit">{formatShare(r.top3_share_of_profit)}</Row>
          <Row label="Net without the best trade">
            <span className={pnlTone(r.net_without_best_trade)}>{formatInr(r.net_without_best_trade, true)}</span>
          </Row>
        </div>
        <div>
          <Row label="Monte Carlo net P&L" hint="1000 resamples of the trades: 5th / 50th / 95th percentile">
            {r.mc_net_pnl ? (
              <span data-testid="bt-mc-net">
                {formatInr(r.mc_net_pnl.p5, true)} / {formatInr(r.mc_net_pnl.p50, true)} / {formatInr(r.mc_net_pnl.p95, true)}
              </span>
            ) : (
              "—"
            )}
          </Row>
          <Row label="Monte Carlo max drawdown" hint="worst 5% / median / best 5%">
            {r.mc_max_drawdown
              ? `${formatInr(r.mc_max_drawdown.p5)} / ${formatInr(r.mc_max_drawdown.p50)} / ${formatInr(r.mc_max_drawdown.p95)}`
              : "—"}
          </Row>
          <Row label="Probability of a net loss" hint="share of resamples that lose">
            {formatShare(r.mc_probability_of_loss)}
          </Row>
          <Row label="Net with charges doubled" hint={`reported ${formatInr(net, true)}`}>
            <span className={pnlTone(r.net_pnl_double_costs)}>{formatInr(r.net_pnl_double_costs, true)}</span>
          </Row>
          <Row label={`Net with +${r.extra_slippage_bps} bps slippage`}>
            <span className={pnlTone(r.net_pnl_extra_slippage)}>{formatInr(r.net_pnl_extra_slippage, true)}</span>
          </Row>
        </div>
      </div>
    </Panel>
  );
}

function ValidationTab({ run }: { run: RunDetail }) {
  const res = run.result!;
  const nullC = res.null_control;
  // show the grid's parameters (what selection varied); without a grid, what you set
  const shownKeys = res.grid[0] ? Object.keys(res.grid[0].varied) : Object.keys(run.request.parameters ?? {});
  const segParams = (p: Record<string, string | number>) =>
    shownKeys.length ? shownKeys.map((k) => `${k} ${paramDisplay(p[k])}`).join(" · ") : "defaults";
  return (
    <div className="flex flex-col gap-4">
      {res.mode === "OUT_OF_SAMPLE" && res.in_sample_metrics && (
        <Panel title="In-sample vs out-of-sample" testId="bt-oos">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Figure label="In-sample exp. R" hint={`${res.in_sample_metrics.total_trades} trades (selection)`}>
              {formatR(res.in_sample_metrics.expectancy_r)}
            </Figure>
            <Figure label="Out-of-sample exp. R" hint={`${res.metrics.total_trades} trades (unseen)`}>
              {formatR(res.metrics.expectancy_r)}
            </Figure>
            <Figure label="Degradation" hint="OOS exp. R ÷ IS exp. R; 1 = held up" testId="bt-degradation">
              {formatRatio(res.degradation)}
            </Figure>
            <Figure label="Combinations compared" hint="on in-sample data only">
              {res.combinations_tested}
            </Figure>
          </div>
        </Panel>
      )}
      {res.walk_forward && (
        <Panel title="Walk-forward" testId="bt-wf">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Figure label="Windows">{res.walk_forward.windows}</Figure>
            <Figure label="Profitable test windows">
              {res.walk_forward.profitable_windows} / {res.walk_forward.windows}
            </Figure>
            <Figure label="Efficiency" hint="mean test exp. R ÷ mean train exp. R">
              {formatRatio(res.walk_forward.efficiency)}
            </Figure>
            <Figure label="Parameter changes" hint="between consecutive windows">
              {res.walk_forward.parameter_changes}
            </Figure>
          </div>
          <p className="mt-3 text-xs text-muted">{res.walk_forward.note}</p>
        </Panel>
      )}

      <Panel title="Segments" testId="bt-segments">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Segment</TableHead>
              <TableHead>Dates</TableHead>
              <TableHead>{res.grid.length ? "Grid parameters" : "Parameters"}</TableHead>
              <TableHead className="text-right">Trades</TableHead>
              <TableHead className="text-right">Win rate</TableHead>
              <TableHead className="text-right">PF</TableHead>
              <TableHead className="text-right">Exp. R</TableHead>
              <TableHead className="text-right">Net P&amp;L</TableHead>
              <TableHead className="text-right">Max DD</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {res.segments.map((s, i) => (
              <TableRow key={i}>
                <TableCell>
                  <div className="font-medium">{s.label}</div>
                  <div className="text-[10px] text-muted">{SEGMENT_LABEL[s.kind] ?? s.kind}</div>
                </TableCell>
                <TableCell className="tabular whitespace-nowrap text-xs text-muted">
                  {s.start} → {s.end} ({s.days}d)
                </TableCell>
                <TableCell className="max-w-[260px] truncate text-xs" title={segParams(s.params)}>
                  {segParams(s.params)}
                </TableCell>
                <TableCell className="tabular text-right">{s.metrics.total_trades}</TableCell>
                <TableCell className="tabular text-right">{formatPct(s.metrics.win_rate)}</TableCell>
                <TableCell className="tabular text-right">{formatRatio(s.metrics.profit_factor)}</TableCell>
                <TableCell className="tabular text-right">{formatR(s.metrics.expectancy_r)}</TableCell>
                <TableCell className={cn("tabular text-right", pnlTone(s.metrics.net_pnl))}>{formatInr(s.metrics.net_pnl, true)}</TableCell>
                <TableCell className="tabular text-right text-loss">{formatInr(s.metrics.max_drawdown)}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Panel>

      {res.grid.length > 0 && (
        <Panel
          title={res.mode === "HISTORICAL" ? "Parameter sensitivity" : "Parameter selection"}
          testId="bt-grid"
          aside={<span className="text-[11px] text-muted">{res.grid.length} rows</span>}
        >
          <p className="mb-3 text-xs text-muted">
            {res.mode === "HISTORICAL"
              ? "Each grid combination over the full range. Nothing is selected from this table: the headline uses your parameters. A result that only works at one point of the grid is fragile."
              : "Objective = mean R minus one standard error (penalises few or noisy trades), then averaged with neighbouring grid points so an isolated spike is not chosen. Profit alone is not the criterion. Sets with too few training trades have no objective."}
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                {res.mode === "WALK_FORWARD" && <TableHead>Window</TableHead>}
                <TableHead>Grid values</TableHead>
                <TableHead className="text-right">Trades</TableHead>
                <TableHead className="text-right">Net P&amp;L</TableHead>
                <TableHead className="text-right">PF</TableHead>
                <TableHead className="text-right">Exp. R</TableHead>
                <TableHead className="text-right">Objective</TableHead>
                <TableHead className="text-right">Smoothed</TableHead>
                <TableHead>{/* selected */}</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {res.grid.map((g, i) => (
                <TableRow key={i} className={cn(g.selected && "bg-accent/5")} data-selected={g.selected}>
                  {res.mode === "WALK_FORWARD" && <TableCell className="text-xs text-muted">{g.window}</TableCell>}
                  <TableCell className="text-xs">
                    {Object.entries(g.varied)
                      .map(([k, v]) => `${k} ${paramDisplay(v)}`)
                      .join(" · ")}
                  </TableCell>
                  <TableCell className="tabular text-right">{g.trades}</TableCell>
                  <TableCell className={cn("tabular text-right", pnlTone(g.net_pnl))}>{formatInr(g.net_pnl, true)}</TableCell>
                  <TableCell className="tabular text-right">{formatRatio(g.profit_factor)}</TableCell>
                  <TableCell className="tabular text-right">{formatR(g.expectancy_r)}</TableCell>
                  <TableCell className="tabular text-right">{g.objective === null ? <span className="text-muted">too few</span> : formatRatio(g.objective, 3)}</TableCell>
                  <TableCell className="tabular text-right">{formatRatio(g.smoothed, 3)}</TableCell>
                  <TableCell>{g.selected && <Badge variant="accent">selected</Badge>}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>
      )}

      {nullC && (
        <Panel title="No-edge control" testId="bt-null">
          <p className="mb-3 text-xs text-muted">
            The same rules and settings on random-walk prices with the same volatility, where no strategy has a real
            edge. A profit here points to a bias in the test or to luck, not skill.
          </p>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Data</TableHead>
                <TableHead className="text-right">Trades</TableHead>
                <TableHead className="text-right">Win rate</TableHead>
                <TableHead className="text-right">PF</TableHead>
                <TableHead className="text-right">Exp. R</TableHead>
                <TableHead className="text-right">Net P&amp;L</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(
                [
                  ["This run", res.metrics],
                  ["No-edge control", nullC],
                ] as const
              ).map(([label, m]) => (
                <TableRow key={label}>
                  <TableCell className="font-medium">{label}</TableCell>
                  <TableCell className="tabular text-right">{m.total_trades}</TableCell>
                  <TableCell className="tabular text-right">{formatPct(m.win_rate)}</TableCell>
                  <TableCell className="tabular text-right">{formatRatio(m.profit_factor)}</TableCell>
                  <TableCell className="tabular text-right">{formatR(m.expectancy_r)}</TableCell>
                  <TableCell className={cn("tabular text-right", pnlTone(m.net_pnl))}>{formatInr(m.net_pnl, true)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Panel>
      )}
    </div>
  );
}

function TradesTab({ trades, loading, error }: { trades: BacktestTrade[]; loading: boolean; error: string | null }) {
  const [limit, setLimit] = useState(100);
  const [open, setOpen] = useState<string | null>(null);
  return (
    <Panel title="Trades" loading={loading} error={error} testId="bt-trades" aside={<span className="text-[11px] text-muted">{trades.length} closed</span>}>
      {trades.length === 0 ? (
        <p className="py-4 text-center text-sm text-muted">No trades in the reported segment.</p>
      ) : (
        <>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Opened</TableHead>
                <TableHead>Symbol</TableHead>
                <TableHead>Side</TableHead>
                <TableHead>Strategy</TableHead>
                <TableHead>Regime</TableHead>
                <TableHead className="text-right">Score</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Entry</TableHead>
                <TableHead className="text-right">Exit</TableHead>
                <TableHead>Exit reason</TableHead>
                <TableHead className="text-right">Net</TableHead>
                <TableHead className="text-right">R</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {trades.slice(0, limit).map((t) => (
                <Fragment key={t.id}>
                  <TableRow className="cursor-pointer" onClick={() => setOpen(open === t.id ? null : t.id)} data-testid="bt-trade-row">
                    <TableCell className="tabular whitespace-nowrap text-xs">
                      {dt(t.opened_at)}
                      {t.segment !== "FULL" && <span className="ml-1 text-[10px] text-muted">{SEGMENT_LABEL[t.segment] ?? t.segment}</span>}
                    </TableCell>
                    <TableCell className="font-medium">{t.symbol}</TableCell>
                    <TableCell className={t.side === "BUY" ? "text-profit" : "text-loss"}>{t.side}</TableCell>
                    <TableCell className="text-xs">{strategyLabel(t.strategy)}</TableCell>
                    <TableCell className="text-xs text-muted">{strategyLabel(t.regime)}</TableCell>
                    <TableCell className="tabular text-right">{t.score === null ? "—" : t.score.toFixed(0)}</TableCell>
                    <TableCell className="tabular text-right">{t.quantity}</TableCell>
                    <TableCell className="tabular text-right">{t.entry_price?.toFixed(2) ?? "—"}</TableCell>
                    <TableCell className="tabular text-right">{t.exit_price?.toFixed(2) ?? "—"}</TableCell>
                    <TableCell className="text-xs">{exitLabel(t.exit_reason)}</TableCell>
                    <TableCell className={cn("tabular text-right", pnlTone(t.net_pnl))}>{formatInr(t.net_pnl, true)}</TableCell>
                    <TableCell className="tabular text-right">{formatR(t.r_multiple)}</TableCell>
                  </TableRow>
                  {open === t.id && (
                    <TableRow className="bg-surface-alt/50 hover:bg-surface-alt/50">
                      <TableCell colSpan={12} className="text-xs">
                        <div className="grid grid-cols-2 gap-x-6 gap-y-1 sm:grid-cols-4">
                          <span className="text-muted">Decision {dt(t.decision_time)}</span>
                          <span className="text-muted">Closed {dt(t.closed_at)}</span>
                          <span className="text-muted">
                            Stop {t.stop_loss.toFixed(2)}
                            {t.trailing_stop !== null && ` → trailed ${t.trailing_stop.toFixed(2)}`}
                          </span>
                          <span className="text-muted">
                            Target {t.target.toFixed(2)} · planned {formatRatio(t.planned_rr)}R
                          </span>
                          <span className="text-muted">Gross {formatInr(t.gross_pnl, true)}</span>
                          <span className="text-muted">Charges {formatInr(t.charges)}</span>
                          <span className="col-span-2 truncate text-muted">Version {t.strategy_version ?? "—"}</span>
                        </div>
                        {t.reasons.length > 0 && (
                          <ul className="mt-2 list-disc pl-4 text-muted">
                            {t.reasons.map((r, i) => (
                              <li key={i}>{r}</li>
                            ))}
                          </ul>
                        )}
                      </TableCell>
                    </TableRow>
                  )}
                </Fragment>
              ))}
            </TableBody>
          </Table>
          {trades.length > limit && (
            <button type="button" className="mt-2 text-xs text-accent hover:underline" onClick={() => setLimit((l) => l + 200)}>
              Show more ({trades.length - limit} left)
            </button>
          )}
        </>
      )}
    </Panel>
  );
}

function DetailsTab({ run }: { run: RunDetail }) {
  const res = run.result!;
  const req = run.request;
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
      <Panel title="Strategy versions &amp; parameters" testId="bt-versions">
        <div className="flex flex-col gap-1 text-xs">
          {res.strategy_versions.map((v) => (
            <div key={v.version_id} className="flex justify-between gap-4">
              <span>{strategyLabel(v.strategy)}</span>
              <span className="tabular text-muted">{v.version_id}</span>
            </div>
          ))}
        </div>
        <div className="mt-3 text-[10px] uppercase tracking-wide text-muted">
          Parameters reported ({res.selected_params_hash})
        </div>
        <div className="mt-1 grid grid-cols-2 gap-x-6 gap-y-0.5 text-xs">
          {Object.entries(res.selected_params).map(([k, v]) => (
            <div key={k} className="flex justify-between gap-2">
              <span className="truncate text-muted">{k}</span>
              <span className={cn("tabular", String(req.parameters[k]) !== String(v) && "text-accent")}>{paramDisplay(v)}</span>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Run setup">
        <div className="flex flex-col text-xs">
          <Row label="Universe">{run.symbols.join(", ")}</Row>
          <Row label="Range">
            {run.start_date} → {run.end_date} · {String(res.data.trading_days ?? "—")} trading days
          </Row>
          <Row label="Warm-up from">{String(res.data.warmup_from ?? "—")}</Row>
          <Row label="Data">{String(res.data.fingerprint ?? run.data_source.fingerprint)}</Row>
          <Row label="Universe membership">
            {String(res.data.universe ?? "—")}
            {res.data.point_in_time_universe ? " (point-in-time)" : " (not point-in-time)"}
          </Row>
          <Row label="Capital · risk / trade">
            {formatInr(req.capital)} · {req.risk.risk_per_trade_percent}%
          </Row>
          <Row label="Daily loss limit · max trades / day">
            {req.risk.daily_loss_limit_percent}% · {req.risk.max_trades_per_day}
          </Row>
          <Row label="Slippage · brokerage cap">
            {req.costs.slippage_bps} bps · {formatInr(req.costs.brokerage_cap_per_order)}
          </Row>
          <Row label="Decisions replayed">
            {String(res.replay.decisions ?? "—")} (signal cache hits {String(res.replay.cache_hits ?? 0)})
          </Row>
          <Row label="Run time">{formatSeconds(run.duration_ms === null ? null : run.duration_ms / 1000)}</Row>
        </div>
      </Panel>
      <Panel title="Assumptions" className="xl:col-span-2" testId="bt-assumptions">
        <ul className="list-disc space-y-1 pl-4 text-xs text-muted">
          {res.assumptions.map((a, i) => (
            <li key={i}>{a}</li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}

export function BacktestResults({ runId }: { runId: string }) {
  const run = useBacktestRun(runId);
  const d = run.data;
  const completed = d?.status === "COMPLETED";
  const trades = useBacktestTrades(runId, completed);
  const equity = useBacktestEquity(runId, completed);
  const [perDay, setPerDay] = useState(false);

  const rows = useMemo(() => {
    if (!equity.data || !d) return [];
    return equityRows(perDay ? equity.data.days : equity.data.trades, d.capital, perDay);
  }, [equity.data, perDay, d]);

  if (run.error) return <Card className="px-4 py-4 text-sm text-loss">Could not load the run: {errorText(run.error)}</Card>;
  if (!d) return <Panel title="Backtest" loading />;

  const header = (
    <div className="flex flex-wrap items-center gap-2">
      <h2 className="text-base font-semibold tracking-tight" data-testid="bt-result-title">
        {d.name || strategyLabel(d.strategy)}
      </h2>
      <Badge variant={STATUS_VARIANT[d.status]}>{d.status.toLowerCase()}</Badge>
      <Badge variant="neutral">{MODE_LABEL[d.mode]}</Badge>
      {d.data_source.synthetic && <Badge variant="pending">synthetic data</Badge>}
    </div>
  );

  if (isActive(d.status)) {
    return (
      <Card className="flex flex-col gap-3 px-4 py-4" data-testid="bt-progress">
        {header}
        <Progress value={d.progress * 100} />
        <p className="text-xs text-muted">
          {d.message} · {Math.round(d.progress * 100)}%
        </p>
      </Card>
    );
  }
  if (d.status !== "COMPLETED" || !d.result || !d.metrics || !d.robustness) {
    return (
      <Card className="flex flex-col gap-2 px-4 py-4">
        {header}
        <p className="text-sm text-muted">{d.error ?? d.message ?? "No results."}</p>
      </Card>
    );
  }

  const res = d.result;
  const flags = d.robustness.flags;

  return (
    <div className="flex flex-col gap-4" data-testid="bt-results">
      <Card className="flex flex-col gap-3 px-4 py-4">
        {header}
        <p className="text-xs text-muted">
          Headline: <span className="text-foreground" data-testid="bt-primary-label">{res.primary_label}</span>
        </p>
        <div className="flex items-start gap-2 rounded border border-border bg-surface-alt px-3 py-2 text-xs text-muted" data-testid="bt-note">
          <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          <span>{d.note}</span>
        </div>
        {res.warnings.length > 0 && (
          <ul className="flex flex-col gap-1 text-xs text-warning" data-testid="bt-warnings">
            {res.warnings.map((w, i) => (
              <li key={i} className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                {w}
              </li>
            ))}
          </ul>
        )}
      </Card>

      <Card
        className={cn("px-4 py-3", flags.length ? "border-warning/40" : "")}
        data-testid="bt-flags"
      >
        <div className="mb-2 text-[10px] uppercase tracking-wide text-muted">Robustness checks</div>
        {flags.length ? (
          <ul className="flex flex-col gap-1 text-sm">
            {flags.map((f, i) => (
              <li key={i} className="flex items-start gap-2">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-warning" aria-hidden />
                {f}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">
            None of the automatic checks raised a concern. That makes the result less likely to be an artefact; it still
            does not make it predictive.
          </p>
        )}
      </Card>

      <Tabs defaultValue="overview">
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="validation">Validation</TabsTrigger>
          <TabsTrigger value="breakdown">Breakdown</TabsTrigger>
          <TabsTrigger value="trades">Trades</TabsTrigger>
          <TabsTrigger value="details">Details</TabsTrigger>
        </TabsList>

        <TabsContent value="overview" className="mt-4 flex flex-col gap-4">
          <Card className="px-4 py-3">
            <MetricsGrid m={d.metrics} testId="bt-metrics" />
          </Card>
          <RobustnessPanel run={d} />
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Panel
              title="Equity curve"
              loading={equity.isLoading}
              error={errorText(equity.error)}
              testId="bt-equity"
              aside={
                <div className="flex gap-1 text-[11px]">
                  {(["per trade", "per day"] as const).map((l, i) => (
                    <button
                      key={l}
                      type="button"
                      onClick={() => setPerDay(i === 1)}
                      className={cn("rounded-sm px-1.5 py-0.5", perDay === (i === 1) ? "bg-surface-alt text-foreground" : "text-muted")}
                    >
                      {l}
                    </button>
                  ))}
                </div>
              }
            >
              {rows.length > 1 ? (
                <BacktestEquityChart rows={rows} start={d.capital} perDay={perDay} />
              ) : (
                <p className="py-6 text-center text-sm text-muted">No trades to chart.</p>
              )}
            </Panel>
            <Panel title="Drawdown" loading={equity.isLoading} aside={<span className="text-[11px] text-muted">below the running peak</span>}>
              {rows.length > 1 ? <BacktestDrawdownChart rows={rows} perDay={perDay} /> : <p className="py-6 text-center text-sm text-muted">—</p>}
            </Panel>
          </div>
        </TabsContent>

        <TabsContent value="validation" className="mt-4">
          <ValidationTab run={d} />
        </TabsContent>

        <TabsContent value="breakdown" className="mt-4 flex flex-col gap-4">
          <Panel title="By regime" testId="bt-by-regime">
            <GroupTable groups={d.robustness.by_regime} label={strategyLabel} />
          </Panel>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Panel title="By strategy">
              <GroupTable groups={d.robustness.by_strategy} label={strategyLabel} />
            </Panel>
            <Panel title="By exit reason">
              <GroupTable groups={d.robustness.by_exit_reason} label={exitLabel} />
            </Panel>
          </div>
        </TabsContent>

        <TabsContent value="trades" className="mt-4">
          <TradesTab trades={trades.data?.items ?? []} loading={trades.isLoading} error={errorText(trades.error)} />
        </TabsContent>

        <TabsContent value="details" className="mt-4">
          <DetailsTab run={d} />
        </TabsContent>
      </Tabs>
    </div>
  );
}
