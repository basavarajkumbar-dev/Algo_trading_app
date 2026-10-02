"use client";

import { useMemo, useState } from "react";

import { Figure, Panel, errorText } from "@/components/dashboard/panel";
import { TradeDetail } from "@/components/trading/trade-detail";
import { TradeTable } from "@/components/trading/trade-table";
import { Card } from "@/components/ui/card";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useOrders, useTrades } from "@/lib/queries";
import { formatInr, pnlTone } from "@/lib/trading-format";
import type { PaperTrade } from "@/types/trading";

const FILTERS: Record<string, (t: PaperTrade) => boolean> = {
  all: () => true,
  closed: (t) => t.status === "CLOSED",
  open: (t) => ["PENDING", "OPEN", "EXITING"].includes(t.status),
  other: (t) => ["CANCELLED", "REJECTED", "INTERRUPTED"].includes(t.status),
};

export default function TradesPage() {
  const trades = useTrades(500);
  const [filter, setFilter] = useState("all");
  const [selected, setSelected] = useState<string | null>(null);
  const items = useMemo(() => (trades.data?.items ?? []).filter(FILTERS[filter] ?? (() => true)), [trades.data, filter]);
  const activeId = selected ?? items[0]?.id ?? null;
  const trade = trades.data?.items.find((t) => t.id === activeId) ?? null;
  const orders = useOrders(activeId);

  const totals = useMemo(() => {
    const closed = (trades.data?.items ?? []).filter((t) => t.status === "CLOSED");
    return {
      count: closed.length,
      net: closed.reduce((a, t) => a + t.net_pnl, 0),
      charges: closed.reduce((a, t) => a + t.charges, 0),
      wins: closed.filter((t) => t.net_pnl > 0).length,
    };
  }, [trades.data]);

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Trades</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Every paper trade attempt, newest first, with its orders, fills, charges and the risk
          checks that approved it. Simulated results under configurable fill and cost assumptions.
        </p>
      </div>

      <Card className="grid grid-cols-2 gap-4 px-4 py-3 sm:grid-cols-4">
        <Figure label="Closed trades">{totals.count}</Figure>
        <Figure label="Net P&L">
          <span className={pnlTone(totals.net)}>{formatInr(totals.net, true)}</span>
        </Figure>
        <Figure label="Charges">{formatInr(totals.charges)}</Figure>
        <Figure label="Winning trades">
          {totals.wins} of {totals.count}
        </Figure>
      </Card>

      <Panel
        title={`Trade history · ${trades.data?.total ?? 0}`}
        aside={
          <Tabs value={filter} onValueChange={setFilter}>
            <TabsList>
              <TabsTrigger value="all">All</TabsTrigger>
              <TabsTrigger value="closed">Closed</TabsTrigger>
              <TabsTrigger value="open">Open</TabsTrigger>
              <TabsTrigger value="other">Cancelled / other</TabsTrigger>
            </TabsList>
          </Tabs>
        }
        loading={trades.isLoading}
        error={errorText(trades.error)}
        bodyClassName="max-h-[520px] overflow-y-auto p-0"
        testId="trades-table"
      >
        <TradeTable trades={items} selectedId={activeId} onSelect={setSelected} />
      </Panel>

      <Panel title="Trade details">
        {trade ? <TradeDetail trade={trade} orders={orders.data} /> : <p className="text-sm text-muted">Select a trade.</p>}
      </Panel>
    </div>
  );
}
