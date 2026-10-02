"use client";

import { DrawdownChart, EquityChart, PnlBars, dayBars, tradeBars } from "@/components/analytics/charts";
import { GroupTable } from "@/components/analytics/group-table";
import { Figure, Panel, errorText } from "@/components/dashboard/panel";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { formatPct, formatRatio, strategyLabel } from "@/lib/dashboard-format";
import { usePerformance } from "@/lib/queries";
import { EXIT_REASON_LABEL, formatInr, formatR, pnlTone } from "@/lib/trading-format";
import { cn } from "@/lib/utils";
import type { ExitReason } from "@/types/trading";

export default function AnalyticsPage() {
  const perf = usePerformance();
  const r = perf.data;
  const s = r?.summary;
  const empty = s && s.closed_trades === 0;

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Analytics</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Results of closed paper trades, net of charges. Paper fills on simulated data under
          configurable slippage and cost assumptions: past paper performance does not predict
          future results.
        </p>
      </div>

      {perf.error && (
        <Card className="px-4 py-4 text-sm text-loss">Could not load analytics: {errorText(perf.error)}</Card>
      )}

      {s && (
        <Card className="px-4 py-3" data-testid="analytics-summary">
          <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4 xl:grid-cols-8">
            <Figure label="Net P&L" hint={`${formatPct(s.return_percent, 2)} of capital`} testId="an-net">
              <span className={pnlTone(s.net_pnl)}>{formatInr(s.net_pnl, true)}</span>
            </Figure>
            <Figure label="Win rate" hint={`${s.wins}W / ${s.losses}L / ${s.breakeven} flat`} testId="an-winrate">
              {formatPct(s.win_rate)}
            </Figure>
            <Figure label="Profit factor" hint={s.profit_factor === null && s.closed_trades ? "no losing trades" : "gross profit ÷ gross loss"} testId="an-pf">
              {formatRatio(s.profit_factor)}
            </Figure>
            <Figure label="Expectancy" hint="net per trade">
              {formatInr(s.expectancy, true)}
            </Figure>
            <Figure label="Max drawdown" hint={`${s.max_drawdown_percent.toFixed(2)}% from peak`} testId="an-dd">
              <span className={s.max_drawdown < 0 ? "text-loss" : ""}>{formatInr(s.max_drawdown)}</span>
            </Figure>
            <Figure label="Average R">{formatR(s.average_r)}</Figure>
            <Figure label="Closed trades" hint={`streaks ${s.longest_win_streak}W / ${s.longest_loss_streak}L`}>
              {s.closed_trades}
            </Figure>
            <Figure label="Charges paid" hint={`avg win ${formatInr(s.average_win)} · avg loss ${formatInr(s.average_loss)}`}>
              {formatInr(s.charges)}
            </Figure>
          </div>
        </Card>
      )}

      {empty && (
        <Card className="px-4 py-8 text-center text-sm text-muted" data-testid="analytics-empty">
          No closed paper trades yet. Charts appear after the first trade closes.
          {r && Object.keys(r.other_trades).length > 0 && (
            <span className="mt-1 block text-xs">
              Not counted: {Object.entries(r.other_trades).map(([k, v]) => `${v} ${k.toLowerCase()}`).join(", ")}.
            </span>
          )}
        </Card>
      )}

      {r && s && !empty && (
        <>
          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Panel title="Equity curve" aside={<span className="text-[11px] text-muted">starting capital {formatInr(s.starting_capital)}</span>}>
              <EquityChart points={r.equity_curve} start={s.starting_capital} />
            </Panel>
            <Panel title="Drawdown" aside={<span className="text-[11px] text-muted">below the running peak</span>}>
              <DrawdownChart points={r.equity_curve} />
            </Panel>
          </div>

          <Panel title="P&L">
            <Tabs defaultValue="trade">
              <TabsList>
                <TabsTrigger value="trade">Per trade</TabsTrigger>
                <TabsTrigger value="day">Per day</TabsTrigger>
              </TabsList>
              <TabsContent value="trade">
                <PnlBars data={tradeBars(r.equity_curve)} />
              </TabsContent>
              <TabsContent value="day">
                <PnlBars data={dayBars(r.daily)} />
              </TabsContent>
            </Tabs>
          </Panel>

          <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <Panel title="Strategy performance" bodyClassName="p-0" testId="strategy-performance">
              <GroupTable groups={r.by_strategy} label={strategyLabel} />
            </Panel>
            <Panel title="By exit reason" bodyClassName="p-0">
              <GroupTable groups={r.by_exit_reason} label={(k) => EXIT_REASON_LABEL[k as ExitReason] ?? k} />
            </Panel>
          </div>
        </>
      )}

      {r && <p className="text-[11px] text-muted">{r.note}</p>}
    </div>
  );
}
