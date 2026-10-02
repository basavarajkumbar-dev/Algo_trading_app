import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { formatPct, formatRatio } from "@/lib/dashboard-format";
import { formatInr, formatR, pnlTone } from "@/lib/trading-format";
import { cn } from "@/lib/utils";
import type { GroupStats } from "@/types/dashboard";

/** Trades / win rate / net / profit factor / average R per group (strategy, exit reason, regime ...). */
export function GroupTable({ groups, label }: { groups: GroupStats[]; label: (key: string) => string }) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>{/* group */}</TableHead>
          <TableHead className="text-right">Trades</TableHead>
          <TableHead className="text-right">Win rate</TableHead>
          <TableHead className="text-right">Net P&amp;L</TableHead>
          <TableHead className="text-right">Profit factor</TableHead>
          <TableHead className="text-right">Avg R</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {groups.map((g) => (
          <TableRow key={g.key}>
            <TableCell className="font-medium">{label(g.key)}</TableCell>
            <TableCell className="tabular text-right">
              {g.trades} <span className="text-muted">({g.wins}W/{g.losses}L)</span>
            </TableCell>
            <TableCell className="tabular text-right">{formatPct(g.win_rate)}</TableCell>
            <TableCell className={cn("tabular text-right", pnlTone(g.net_pnl))}>{formatInr(g.net_pnl, true)}</TableCell>
            <TableCell className="tabular text-right">{formatRatio(g.profit_factor)}</TableCell>
            <TableCell className="tabular text-right">{formatR(g.average_r)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
