"use client";

import { Panel, errorText } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { strategyLabel } from "@/lib/dashboard-format";
import { formatIstTime, formatPrice, formatScore } from "@/lib/intelligence-format";
import { useScanner } from "@/lib/queries";
import { SCAN_STATUS, sideTone } from "@/lib/signal-format";
import { useMarketStore } from "@/lib/stores/market-store";
import { useUiStore } from "@/lib/stores/ui-store";
import { cn } from "@/lib/utils";
import type { ScanRow } from "@/types/signals";

export function signalKey(row: Pick<ScanRow, "symbol" | "signal_strategy" | "signal_direction">): string {
  return `${row.symbol}:${row.signal_strategy}:${row.signal_direction}`;
}

/**
 * The NIFTY 50 scan, ranked rows first. Prices and changes update live
 * from the WebSocket; signal, score, strategy and status come from the
 * latest scan (every price action refresh). Click a row with a signal to
 * show it in the signal panel.
 */
export function LiveScanner({ limit = 14 }: { limit?: number }) {
  const scanner = useScanner();
  const snapshots = useMarketStore((s) => s.snapshots);
  const selected = useUiStore((s) => s.dashboardSignalKey);
  const select = useUiStore((s) => s.setDashboardSignalKey);
  const scan = scanner.data?.scan;
  const rows = (scan?.rows ?? []).slice(0, limit);

  return (
    <Panel
      title="Live Scanner"
      href="/scanner"
      linkLabel="Full scan"
      aside={
        scan && (
          <span className="text-[11px] text-muted">
            {scan.status_counts.READY ?? 0} ready · {scan.status_counts.WATCH ?? 0} watch · scan{" "}
            {formatIstTime(scan.scanned_at, true)}
          </span>
        )
      }
      loading={scanner.isLoading}
      error={errorText(scanner.error)}
      bodyClassName="p-0"
      testId="live-scanner"
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-10">Rank</TableHead>
            <TableHead>Stock</TableHead>
            <TableHead className="text-right">Price</TableHead>
            <TableHead className="text-right">Change</TableHead>
            <TableHead>Signal</TableHead>
            <TableHead className="text-right">Score</TableHead>
            <TableHead>Strategy</TableHead>
            <TableHead>Status</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((row) => {
            const live = snapshots[row.symbol];
            const price = live?.price ?? row.price;
            const change = live?.change_percent ?? row.change_percent;
            const key = row.signal_direction ? signalKey(row) : null;
            const meta = SCAN_STATUS[row.status];
            return (
              <TableRow
                key={row.symbol}
                data-state={key && key === selected ? "selected" : undefined}
                onClick={key ? () => select(key) : undefined}
                className={cn(key && "cursor-pointer hover:bg-surface-alt/70")}
                data-testid="scanner-row"
              >
                <TableCell className="tabular text-muted">{row.rank ?? "—"}</TableCell>
                <TableCell className="font-medium">{row.symbol}</TableCell>
                <TableCell className="tabular text-right">{formatPrice(price)}</TableCell>
                <TableCell
                  className={cn(
                    "tabular text-right",
                    change === null ? "text-muted" : change >= 0 ? "text-profit" : "text-loss"
                  )}
                >
                  {change === null ? "—" : `${change >= 0 ? "+" : ""}${change.toFixed(2)}%`}
                </TableCell>
                <TableCell className={cn("font-semibold", sideTone(row.signal_direction))}>
                  {row.signal_direction ?? "—"}
                </TableCell>
                <TableCell className="tabular text-right">{row.score === null ? "—" : formatScore(row.score)}</TableCell>
                <TableCell className="text-muted">{strategyLabel(row.signal_strategy)}</TableCell>
                <TableCell>
                  <Badge variant={meta.variant} className="text-[10px]" title={meta.hint}>
                    {meta.label}
                  </Badge>
                </TableCell>
              </TableRow>
            );
          })}
          {scan && rows.length === 0 && (
            <TableRow>
              <TableCell colSpan={8} className="py-6 text-center text-muted">
                No rows in the latest scan.
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </Panel>
  );
}
