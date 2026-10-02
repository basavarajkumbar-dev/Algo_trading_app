"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Square, Trash2 } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { cancelBacktestRequest, deleteBacktestRequest } from "@/lib/backtest-client";
import { MODE_LABEL, STATUS_VARIANT, formatSeconds, isActive } from "@/lib/backtest-format";
import { formatPct, formatRatio, strategyLabel } from "@/lib/dashboard-format";
import { qk } from "@/lib/query-keys";
import { formatInr, formatR, pnlTone } from "@/lib/trading-format";
import { cn } from "@/lib/utils";
import type { RunList as RunListData, RunSummary } from "@/types/backtest";

export function RunList({
  runs,
  selected,
  onSelect,
}: {
  runs: RunSummary[];
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const { accessToken, user } = useAuth();
  const canAct = user?.role === "ADMIN" || user?.role === "TRADER";
  const queryClient = useQueryClient();
  const refresh = () => void queryClient.invalidateQueries({ queryKey: ["backtests"] });

  const cancel = useMutation({
    mutationFn: (id: string) => cancelBacktestRequest(id, accessToken ?? ""),
    onSettled: refresh,
  });
  const remove = useMutation({
    mutationFn: (id: string) => deleteBacktestRequest(id, accessToken ?? ""),
    onSuccess: (_, id) => {
      // drop it from the cached list first, so nothing re-selects (and re-fetches) a deleted run
      queryClient.setQueryData<RunListData>(qk.backtestRuns, (old) =>
        old ? { ...old, items: old.items.filter((r) => r.id !== id), total: old.total - 1 } : old
      );
      if (selected === id) onSelect(null);
      for (const key of [qk.backtestRun(id), qk.backtestTrades(id), qk.backtestEquity(id)]) {
        queryClient.removeQueries({ queryKey: key });
      }
    },
    onSettled: () => void queryClient.invalidateQueries({ queryKey: qk.backtestRuns }),
  });

  if (runs.length === 0) {
    return (
      <p className="py-6 text-center text-sm text-muted" data-testid="bt-runs-empty">
        No backtests yet. Configure one under “New backtest”.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {(cancel.error || remove.error) && (
        <p className="text-xs text-loss">{((cancel.error ?? remove.error) as Error).message}</p>
      )}
      <Table data-testid="bt-runs">
        <TableHeader>
          <TableRow>
            <TableHead>Run</TableHead>
            <TableHead>Mode</TableHead>
            <TableHead>Range</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Trades</TableHead>
            <TableHead className="text-right">Win rate</TableHead>
            <TableHead className="text-right">PF</TableHead>
            <TableHead className="text-right">Exp. R</TableHead>
            <TableHead className="text-right">Net P&amp;L</TableHead>
            <TableHead className="text-right">Flags</TableHead>
            <TableHead>{/* actions */}</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {runs.map((r) => {
            const h = r.headline;
            const active = isActive(r.status);
            return (
              <TableRow
                key={r.id}
                onClick={() => onSelect(r.id)}
                className={cn("cursor-pointer", selected === r.id && "bg-surface-alt")}
                data-testid="bt-run-row"
                data-status={r.status}
              >
                <TableCell className="max-w-[240px]">
                  <div className="truncate font-medium">{r.name || strategyLabel(r.strategy)}</div>
                  <div className="truncate text-[11px] text-muted">
                    {strategyLabel(r.strategy)} · {r.symbols.length} symbol{r.symbols.length === 1 ? "" : "s"} · {r.timeframe}
                    {r.data_source.synthetic ? " · synthetic" : ""}
                  </div>
                </TableCell>
                <TableCell className="text-xs">{MODE_LABEL[r.mode]}</TableCell>
                <TableCell className="tabular whitespace-nowrap text-xs text-muted">
                  {r.start_date} → {r.end_date}
                </TableCell>
                <TableCell className="min-w-[150px]">
                  <div className="flex items-center gap-2">
                    <Badge variant={STATUS_VARIANT[r.status]}>{r.status.toLowerCase()}</Badge>
                    {r.duration_ms !== null && !active && (
                      <span className="tabular text-[11px] text-muted">{formatSeconds(r.duration_ms / 1000)}</span>
                    )}
                  </div>
                  {active && (
                    <div className="mt-1 flex flex-col gap-0.5">
                      <Progress value={r.progress * 100} aria-label="progress" />
                      <span className="truncate text-[10px] text-muted">{r.message}</span>
                    </div>
                  )}
                  {r.status === "FAILED" && r.error && (
                    <div className="mt-0.5 max-w-[220px] truncate text-[10px] text-loss" title={r.error}>
                      {r.error}
                    </div>
                  )}
                </TableCell>
                <TableCell className="tabular text-right">{h ? h.total_trades : "—"}</TableCell>
                <TableCell className="tabular text-right">{h ? formatPct(h.win_rate) : "—"}</TableCell>
                <TableCell className="tabular text-right">{h ? formatRatio(h.profit_factor) : "—"}</TableCell>
                <TableCell className="tabular text-right">{h ? formatR(h.expectancy_r) : "—"}</TableCell>
                <TableCell className={cn("tabular text-right", pnlTone(h?.net_pnl))}>{h ? formatInr(h.net_pnl, true) : "—"}</TableCell>
                <TableCell className="tabular text-right">
                  {r.flags === null ? "—" : r.flags > 0 ? <span className="text-warning">{r.flags}</span> : "0"}
                </TableCell>
                <TableCell className="text-right" onClick={(e) => e.stopPropagation()}>
                  {canAct &&
                    (active ? (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => cancel.mutate(r.id)}
                        disabled={cancel.isPending}
                        aria-label="Cancel run"
                        title="Cancel"
                      >
                        <Square className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        onClick={() => remove.mutate(r.id)}
                        disabled={remove.isPending}
                        aria-label="Delete run"
                        title="Delete"
                      >
                        <Trash2 className="h-3.5 w-3.5" aria-hidden />
                      </Button>
                    ))}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}
