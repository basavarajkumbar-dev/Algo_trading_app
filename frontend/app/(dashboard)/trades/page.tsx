"use client";

import { useEffect, useMemo, useState } from "react";

import { RiskChecks, Stat, TradeFigures, TradeHeader } from "@/components/trading/trading-parts";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatIstTime, formatPrice } from "@/lib/intelligence-format";
import { getOrdersRequest, getTradesRequest } from "@/lib/trading-client";
import {
  EXIT_REASON_LABEL,
  TRADE_STATUS_VARIANT,
  formatInr,
  formatR,
  pnlTone,
} from "@/lib/trading-format";
import { cn } from "@/lib/utils";
import type { OrderList, TradeList } from "@/types/trading";

const POLL_MS = 5_000;

export default function TradesPage() {
  const [list, setList] = useState<TradeList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [orders, setOrders] = useState<OrderList | null>(null);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      getTradesRequest({ limit: 200 })
        .then((d) => {
          if (cancelled) return;
          setList(d);
          setError(null);
        })
        .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : "Request failed"));
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const activeId = selected ?? list?.items[0]?.id ?? null;
  const trade = list?.items.find((t) => t.id === activeId) ?? null;

  useEffect(() => {
    if (!activeId) return;
    let cancelled = false;
    getOrdersRequest({ tradeId: activeId })
      .then((d) => !cancelled && setOrders(d))
      .catch(() => !cancelled && setOrders(null));
    return () => {
      cancelled = true;
    };
  }, [activeId, list]);

  const totals = useMemo(() => {
    const closed = (list?.items ?? []).filter((t) => t.status === "CLOSED");
    const net = closed.reduce((a, t) => a + t.net_pnl, 0);
    const charges = closed.reduce((a, t) => a + t.charges, 0);
    const wins = closed.filter((t) => t.net_pnl > 0).length;
    return { count: closed.length, net, charges, wins };
  }, [list]);

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Trades</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Every paper trade attempt, newest first, with its orders, fills, charges and the risk
          checks that approved it. Simulated results under configurable fill and cost
          assumptions; past paper results say nothing certain about future results.
        </p>
      </div>

      <Card>
        <CardContent className="grid grid-cols-2 gap-4 py-4 sm:grid-cols-4">
          <Stat label="Closed trades (listed)">{totals.count}</Stat>
          <Stat label="Net P&L">
            <span className={pnlTone(totals.net)}>{formatInr(totals.net, true)}</span>
          </Stat>
          <Stat label="Charges">{formatInr(totals.charges)}</Stat>
          <Stat label="Winning trades">
            {totals.wins} of {totals.count}
          </Stat>
        </CardContent>
      </Card>

      {error && (
        <Card>
          <CardContent className="py-6 text-sm text-loss">Could not load trades: {error}</CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 items-start gap-4 2xl:grid-cols-[560px_minmax(0,1fr)]">
        <Card className="flex max-h-[820px] flex-col overflow-hidden">
          <CardHeader>
            <CardTitle>
              Paper trades <span className="ml-1 text-xs font-normal text-muted">{list?.total ?? 0}</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 overflow-y-auto p-0">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-border text-left text-[10px] uppercase tracking-wide text-muted">
                  <th className="px-3 py-2 font-medium">Opened</th>
                  <th className="px-2 py-2 font-medium">Stock</th>
                  <th className="px-2 py-2 font-medium">Side</th>
                  <th className="px-2 py-2 text-right font-medium">Qty</th>
                  <th className="px-2 py-2 font-medium">Exit</th>
                  <th className="px-2 py-2 text-right font-medium">Net P&amp;L</th>
                  <th className="px-3 py-2 text-right font-medium">R</th>
                </tr>
              </thead>
              <tbody>
                {list && list.items.length === 0 && (
                  <tr>
                    <td colSpan={7} className="px-3 py-6 text-center text-muted">
                      No paper trades yet.
                    </td>
                  </tr>
                )}
                {list?.items.map((t) => (
                  <tr
                    key={t.id}
                    onClick={() => setSelected(t.id)}
                    className={cn(
                      "cursor-pointer border-b border-border last:border-0 hover:bg-surface-alt",
                      activeId === t.id && "bg-surface-alt"
                    )}
                  >
                    <td className="tabular px-3 py-1.5 text-muted">{formatIstTime(t.opened_at ?? t.created_at)}</td>
                    <td className="px-2 py-1.5 font-medium">{t.symbol}</td>
                    <td className={cn("px-2 py-1.5 font-semibold", t.side === "LONG" ? "text-profit" : "text-loss")}>{t.side}</td>
                    <td className="tabular px-2 py-1.5 text-right">{t.filled_quantity}</td>
                    <td className="px-2 py-1.5">
                      {t.exit_reason ? (
                        EXIT_REASON_LABEL[t.exit_reason]
                      ) : (
                        <Badge variant={TRADE_STATUS_VARIANT[t.status]} className="text-[10px]">
                          {t.status}
                        </Badge>
                      )}
                    </td>
                    <td className={cn("tabular px-2 py-1.5 text-right", pnlTone(t.net_pnl))}>
                      {t.status === "CLOSED" ? formatInr(t.net_pnl, true) : "—"}
                    </td>
                    <td className="tabular px-3 py-1.5 text-right">{formatR(t.r_multiple)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Trade details</CardTitle>
          </CardHeader>
          <CardContent>
            {!trade ? (
              <p className="text-sm text-muted">Select a trade.</p>
            ) : (
              <div className="flex flex-col gap-6">
                <TradeHeader trade={trade} />
                <TradeFigures trade={trade} />
                <section className="flex flex-col gap-2">
                  <h3 className="text-[11px] uppercase tracking-wide text-muted">Orders and fills</h3>
                  <table className="w-full text-xs">
                    <thead>
                      <tr className="border-b border-border text-left text-[10px] uppercase tracking-wide text-muted">
                        <th className="py-1.5 font-medium">Purpose</th>
                        <th className="py-1.5 font-medium">Order</th>
                        <th className="py-1.5 text-right font-medium">Filled</th>
                        <th className="py-1.5 text-right font-medium">Avg price</th>
                        <th className="py-1.5 text-right font-medium">Slippage</th>
                        <th className="py-1.5 text-right font-medium">Charges</th>
                        <th className="py-1.5 pl-3 font-medium">Status</th>
                      </tr>
                    </thead>
                    <tbody>
                      {orders?.items
                        .slice()
                        .reverse()
                        .map(({ order: o, purpose }) => (
                          <tr key={o.id} className="border-b border-border last:border-0">
                            <td className="py-1.5">{purpose?.replace("_", " ").toLowerCase()}</td>
                            <td className="py-1.5 text-muted">
                              {o.side} {o.order_type.replace("_", "-")}
                              {o.price ? ` @ ${formatPrice(o.price)}` : ""}
                              {o.trigger_price ? ` trig ${formatPrice(o.trigger_price)}` : ""}
                            </td>
                            <td className="tabular py-1.5 text-right">
                              {o.filled_quantity}/{o.quantity}
                              {o.fills.length > 1 ? ` (${o.fills.length} fills)` : ""}
                            </td>
                            <td className="tabular py-1.5 text-right">{formatPrice(o.average_price)}</td>
                            <td className="tabular py-1.5 text-right">
                              {o.fills.length ? formatPrice(o.fills.reduce((a, f) => a + f.slippage * f.quantity, 0) / Math.max(o.filled_quantity, 1)) : "—"}
                            </td>
                            <td className="tabular py-1.5 text-right">
                              {formatInr(
                                o.charges.brokerage + o.charges.stt + o.charges.exchange + o.charges.sebi + o.charges.stamp_duty + o.charges.gst
                              )}
                            </td>
                            <td className="py-1.5 pl-3 text-muted" title={o.rejection_reason ?? undefined}>
                              {o.status.replace("_", " ").toLowerCase()}
                            </td>
                          </tr>
                        ))}
                    </tbody>
                  </table>
                </section>
                <section className="flex flex-col gap-2">
                  <h3 className="text-[11px] uppercase tracking-wide text-muted">Risk checks at entry</h3>
                  <p className="text-xs">{trade.risk_summary}</p>
                  <RiskChecks checks={trade.risk_checks} />
                </section>
                <p className="text-[11px] text-muted">
                  Created {formatIstTime(trade.created_at, true)}
                  {trade.closed_at ? `, closed ${formatIstTime(trade.closed_at, true)}` : ""}. {trade.note}
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
