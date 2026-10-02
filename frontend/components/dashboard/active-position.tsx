"use client";

import { Figure, Panel, errorText } from "@/components/dashboard/panel";
import { PositionBar } from "@/components/dashboard/position-bar";
import { TradeHeader } from "@/components/trading/trading-parts";
import { formatDuration } from "@/lib/dashboard-format";
import { formatPrice } from "@/lib/intelligence-format";
import { useTradingStatus } from "@/lib/queries";
import { useMarketStore } from "@/lib/stores/market-store";
import { formatInr, pnlTone } from "@/lib/trading-format";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";
import type { PaperTrade } from "@/types/trading";

/** Unrealized P&L of the open quantity at `price`. */
export function livePnl(trade: PaperTrade, price: number | null): number | null {
  if (price === null || trade.entry_price === null) return null;
  const dir = trade.side === "LONG" ? 1 : -1;
  return (price - trade.entry_price) * trade.open_quantity * dir;
}

/**
 * The one active paper trade: entry, live price (WebSocket), stop,
 * target, P&L and duration, and the SL ── ENTRY ── CURRENT ── TARGET bar.
 */
export function ActivePosition() {
  const status = useTradingStatus();
  const trade = status.data?.active_trade ?? null;
  const live = useMarketStore((s) => (trade ? s.snapshots[trade.symbol] : undefined));
  const now = useNow();
  const current = live?.price ?? trade?.last_price ?? null;
  const unrealized = trade ? livePnl(trade, current) ?? trade.unrealized_pnl : null;
  const net = trade && unrealized !== null ? trade.net_pnl + unrealized : null;

  return (
    <Panel
      title="Active Position"
      href="/positions"
      linkLabel="Positions"
      loading={status.isLoading}
      error={errorText(status.error)}
      testId="active-position"
    >
      {!trade ? (
        <div className="flex flex-col gap-1 text-sm text-muted" data-testid="no-position">
          <span>No active position.</span>
          <span className="text-xs">
            {status.data?.auto_trading
              ? "Automatic paper trading is scanning for a READY signal."
              : "Automatic paper trading is off. Execute a signal or turn it on from Positions."}
          </span>
        </div>
      ) : (
        <div className="flex flex-col gap-5">
          <TradeHeader trade={trade} />
          <div className="grid grid-cols-3 gap-4 lg:grid-cols-6">
            <Figure label="Entry" hint={`${trade.filled_quantity} filled`}>
              {formatPrice(trade.entry_price)}
            </Figure>
            <Figure label="Current" hint={live ? "live" : "last known"} testId="position-current">
              {formatPrice(current)}
            </Figure>
            <Figure label="SL">{formatPrice(trade.stop_loss)}</Figure>
            <Figure label="Target">{formatPrice(trade.target)}</Figure>
            <Figure label="P&L" hint={`charges ${formatInr(trade.charges)}`} testId="position-pnl">
              <span className={cn("font-semibold", pnlTone(net))}>{formatInr(net, true)}</span>
            </Figure>
            <Figure label="Duration" testId="position-duration">
              {formatDuration(trade.opened_at ?? trade.created_at, now)}
            </Figure>
          </div>
          {trade.entry_price !== null && (
            <PositionBar stop={trade.stop_loss} entry={trade.entry_price} target={trade.target} current={current} />
          )}
        </div>
      )}
    </Panel>
  );
}
