"use client";

import { ActivePosition } from "@/components/dashboard/active-position";
import { CommandCenter } from "@/components/dashboard/command-center";
import { LiveScanner } from "@/components/dashboard/live-scanner";
import { MarketBrain } from "@/components/dashboard/market-brain";
import { Panel, errorText } from "@/components/dashboard/panel";
import { RiskSummary } from "@/components/dashboard/risk-summary";
import { SignalPanel } from "@/components/dashboard/signal-panel";
import { SystemHealthList } from "@/components/dashboard/system-health-list";
import { TradeTable } from "@/components/trading/trade-table";
import { useTrades } from "@/lib/queries";

export default function DashboardPage() {
  const trades = useTrades(200);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Command Center</h1>
          <p className="mt-0.5 text-xs text-muted">
            PAPER mode on simulated NIFTY 50 data. Live over the WebSocket; no broker connected.
          </p>
        </div>
      </div>

      <CommandCenter />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,8fr)]">
        <MarketBrain />
        <LiveScanner />
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <SignalPanel />
        <div className="flex flex-col gap-4">
          <ActivePosition />
          <RiskSummary />
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,8fr)_minmax(0,5fr)]">
        <Panel
          title="Trade History"
          href="/trades"
          linkLabel="All trades"
          loading={trades.isLoading}
          error={errorText(trades.error)}
          bodyClassName="p-0"
          testId="trade-history"
        >
          <TradeTable trades={(trades.data?.items ?? []).slice(0, 8)} compact />
        </Panel>
        <SystemHealthList />
      </div>
    </div>
  );
}
