"use client";

import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatDuration, strategyLabel } from "@/lib/dashboard-format";
import { formatIstTime, formatPrice } from "@/lib/intelligence-format";
import { EXIT_REASON_LABEL, TRADE_STATUS_VARIANT, formatInr, formatR, pnlTone } from "@/lib/trading-format";
import { cn } from "@/lib/utils";
import type { PaperTrade } from "@/types/trading";

/** Every column of a paper trade. `compact` drops the less-used ones. */
export function TradeTable({
  trades,
  compact = false,
  selectedId,
  onSelect,
}: {
  trades: PaperTrade[];
  compact?: boolean;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Opened</TableHead>
          <TableHead>Stock</TableHead>
          <TableHead>Side</TableHead>
          {!compact && <TableHead>Strategy</TableHead>}
          <TableHead className="text-right">Qty</TableHead>
          <TableHead className="text-right">Entry</TableHead>
          <TableHead className="text-right">Exit</TableHead>
          {!compact && <TableHead className="text-right">SL</TableHead>}
          {!compact && <TableHead className="text-right">Target</TableHead>}
          <TableHead>Result</TableHead>
          {!compact && <TableHead className="text-right">Gross</TableHead>}
          {!compact && <TableHead className="text-right">Charges</TableHead>}
          <TableHead className="text-right">Net P&amp;L</TableHead>
          <TableHead className="text-right">R</TableHead>
          {!compact && <TableHead className="text-right">Held</TableHead>}
        </TableRow>
      </TableHeader>
      <TableBody>
        {trades.length === 0 && (
          <TableRow>
            <TableCell colSpan={compact ? 9 : 15} className="py-6 text-center text-muted">
              No paper trades yet.
            </TableCell>
          </TableRow>
        )}
        {trades.map((t) => {
          const closed = t.status === "CLOSED";
          return (
            <TableRow
              key={t.id}
              data-state={selectedId === t.id ? "selected" : undefined}
              onClick={onSelect ? () => onSelect(t.id) : undefined}
              className={cn(onSelect && "cursor-pointer hover:bg-surface-alt/70")}
              data-testid="trade-row"
            >
              <TableCell className="tabular text-muted">{formatIstTime(t.opened_at ?? t.created_at)}</TableCell>
              <TableCell className="font-medium">{t.symbol}</TableCell>
              <TableCell className={cn("font-semibold", t.side === "LONG" ? "text-profit" : "text-loss")}>{t.side}</TableCell>
              {!compact && <TableCell className="text-muted">{strategyLabel(t.strategy)}</TableCell>}
              <TableCell className="tabular text-right">{t.filled_quantity}</TableCell>
              <TableCell className="tabular text-right">{formatPrice(t.entry_price)}</TableCell>
              <TableCell className="tabular text-right">{formatPrice(t.exit_price)}</TableCell>
              {!compact && <TableCell className="tabular text-right text-muted">{formatPrice(t.stop_loss)}</TableCell>}
              {!compact && <TableCell className="tabular text-right text-muted">{formatPrice(t.target)}</TableCell>}
              <TableCell>
                {t.exit_reason && closed ? (
                  EXIT_REASON_LABEL[t.exit_reason]
                ) : (
                  <Badge variant={TRADE_STATUS_VARIANT[t.status]} className="text-[10px]">
                    {t.status}
                  </Badge>
                )}
              </TableCell>
              {!compact && (
                <TableCell className={cn("tabular text-right", pnlTone(t.gross_pnl))}>
                  {closed ? formatInr(t.gross_pnl, true) : "—"}
                </TableCell>
              )}
              {!compact && <TableCell className="tabular text-right text-muted">{formatInr(t.charges)}</TableCell>}
              <TableCell className={cn("tabular text-right font-medium", pnlTone(closed ? t.net_pnl : null))}>
                {closed ? formatInr(t.net_pnl, true) : "—"}
              </TableCell>
              <TableCell className="tabular text-right">{formatR(t.r_multiple)}</TableCell>
              {!compact && (
                <TableCell className="tabular text-right text-muted">
                  {t.opened_at && t.closed_at ? formatDuration(t.opened_at, Date.parse(t.closed_at)) : "—"}
                </TableCell>
              )}
            </TableRow>
          );
        })}
      </TableBody>
    </Table>
  );
}
