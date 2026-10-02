"use client";

import { RiskChecks, TradeFigures, TradeHeader } from "@/components/trading/trading-parts";
import { formatIstTime, formatPrice } from "@/lib/intelligence-format";
import { formatInr } from "@/lib/trading-format";
import type { OrderList, PaperTrade } from "@/types/trading";

/** One paper trade: figures, every order and fill, and the risk checks that approved it. */
export function TradeDetail({ trade, orders }: { trade: PaperTrade; orders: OrderList | undefined }) {
  return (
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
  );
}
