"use client";

import { useEffect, useState } from "react";

import { BacktestFormCard } from "@/components/backtest/backtest-form";
import { BacktestResults } from "@/components/backtest/backtest-results";
import { RunList } from "@/components/backtest/run-list";
import { Panel, errorText } from "@/components/dashboard/panel";
import { Card } from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useBacktestOptions, useBacktestRuns } from "@/lib/queries";

/**
 * Backtesting (Phase 10): configure a run, queue it on the server, follow
 * its progress and read the results with their robustness checks.
 */
export default function BacktestingPage() {
  const options = useBacktestOptions();
  const runs = useBacktestRuns();
  const [tab, setTab] = useState("runs");
  const [selected, setSelected] = useState<string | null>(null);

  // open the newest run by default
  const first = runs.data?.items[0]?.id ?? null;
  useEffect(() => {
    if (selected === null && first) setSelected(first);
  }, [first, selected]);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Backtesting</h1>
        <p className="mt-1 max-w-4xl text-sm text-muted">
          Replays the live strategy, risk and paper-execution code bar by bar on past data, using only bars that had
          closed at each decision. Out-of-sample and walk-forward modes choose parameters on earlier data and score
          them on later, unseen data. Backtests describe the past under stated assumptions; they do not guarantee
          future performance.
        </p>
      </div>

      {options.error && (
        <Card className="px-4 py-4 text-sm text-loss">Could not load backtest options: {errorText(options.error)}</Card>
      )}

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="runs">Runs &amp; results</TabsTrigger>
          <TabsTrigger value="new" data-testid="bt-tab-new">
            New backtest
          </TabsTrigger>
        </TabsList>

        <TabsContent value="runs" className="mt-4 flex flex-col gap-4">
          <Panel
            title="Runs"
            loading={runs.isLoading}
            error={errorText(runs.error)}
            aside={
              runs.data && runs.data.queued > 0 ? (
                <span className="text-[11px] text-muted">{runs.data.queued} waiting</span>
              ) : undefined
            }
          >
            <RunList runs={runs.data?.items ?? []} selected={selected} onSelect={setSelected} />
          </Panel>
          {selected && <BacktestResults key={selected} runId={selected} />}
        </TabsContent>

        <TabsContent value="new" className="mt-4">
          {options.data ? (
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,560px)_minmax(0,1fr)]">
              <BacktestFormCard
                options={options.data}
                onSubmitted={(run) => {
                  setSelected(run.id);
                  setTab("runs");
                }}
              />
              <div className="flex flex-col gap-4">
                <Panel title="How bias is controlled" testId="bt-bias">
                  <ul className="list-disc space-y-1.5 pl-4 text-xs text-muted">
                    <li>
                      <span className="text-foreground">Look-ahead:</span> decisions are made at a bar&apos;s close from
                      closed bars only (enforced in code); orders fill from the next bar.
                    </li>
                    <li>
                      <span className="text-foreground">Data leakage:</span> indicators, regime and higher timeframes
                      are rebuilt incrementally; selection never sees out-of-sample or test days.
                    </li>
                    <li>
                      <span className="text-foreground">Survivorship:</span> universe membership is checked per day
                      when the data source provides it; otherwise the run is flagged.
                    </li>
                    <li>
                      <span className="text-foreground">Overfitting:</span> grids are capped, selection uses a
                      noise-penalised, neighbour-smoothed objective, and results get Monte Carlo, cost-stress and
                      concentration checks.
                    </li>
                  </ul>
                </Panel>
                <Panel title="Assumptions">
                  <ul className="list-disc space-y-1 pl-4 text-xs text-muted">
                    {options.data.assumptions.map((a, i) => (
                      <li key={i}>{a}</li>
                    ))}
                  </ul>
                  <p className="mt-3 text-xs text-muted">{options.data.note}</p>
                </Panel>
              </div>
            </div>
          ) : (
            <Panel title="New backtest" loading={options.isLoading} />
          )}
        </TabsContent>
      </Tabs>
    </div>
  );
}
