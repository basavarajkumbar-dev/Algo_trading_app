"use client";

import { useMemo, useState } from "react";

import { formatPrice } from "@/lib/intelligence-format";
import { ZONE_META, directionTone } from "@/lib/price-action-format";
import { cn } from "@/lib/utils";
import type { PriceActionSummary } from "@/types/price-action";

type Filter = "ALL" | "PATTERN" | "BULLISH" | "BEARISH";

const FILTERS: { key: Filter; label: string }[] = [
  { key: "ALL", label: "All" },
  { key: "PATTERN", label: "Pattern now" },
  { key: "BULLISH", label: "Bullish bias" },
  { key: "BEARISH", label: "Bearish bias" },
];

function matches(row: PriceActionSummary, filter: Filter): boolean {
  if (filter === "PATTERN") return row.patterns.length > 0;
  if (filter === "BULLISH") return row.overall_bias === "BULLISH";
  if (filter === "BEARISH") return row.overall_bias === "BEARISH";
  return true;
}

/**
 * Compact list of every symbol, sorted by multi-timeframe alignment.
 * Shows patterns on the latest closed candle and the pullback zone.
 */
export function SymbolList({
  rows,
  selected,
  onSelect,
}: {
  rows: PriceActionSummary[];
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  const [filter, setFilter] = useState<Filter>("ALL");
  const visible = useMemo(
    () =>
      rows
        .filter((r) => matches(r, filter))
        .sort((a, b) => (b.alignment_score ?? -1) - (a.alignment_score ?? -1)),
    [rows, filter]
  );

  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap gap-1.5 border-b border-border px-3 py-2.5">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setFilter(f.key)}
            className={cn(
              "rounded-sm border px-2 py-0.5 text-xs",
              filter === f.key
                ? "border-accent/40 bg-accent/10 text-foreground"
                : "border-border text-muted hover:text-foreground"
            )}
          >
            {f.label}{" "}
            <span className="tabular text-muted">{rows.filter((r) => matches(r, f.key)).length}</span>
          </button>
        ))}
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface">
            <tr className="border-b border-border text-left text-[10px] uppercase tracking-wide text-muted">
              <th className="px-3 py-1.5 font-medium">Symbol</th>
              <th className="px-2 py-1.5 font-medium">Now</th>
              <th className="px-2 py-1.5 font-medium">Pullback</th>
              <th className="px-3 py-1.5 text-right font-medium">Align</th>
            </tr>
          </thead>
          <tbody>
            {visible.length === 0 && (
              <tr>
                <td colSpan={4} className="px-3 py-4 text-center text-muted">
                  No symbols match.
                </td>
              </tr>
            )}
            {visible.map((row) => (
              <tr
                key={row.symbol}
                onClick={() => onSelect(row.symbol)}
                className={cn(
                  "cursor-pointer border-b border-border last:border-0 hover:bg-surface-alt",
                  selected === row.symbol && "bg-surface-alt"
                )}
              >
                <td className="px-3 py-1.5">
                  <div className="flex flex-col">
                    <span className="font-medium">{row.symbol}</span>
                    <span className="tabular text-[10px] text-muted">{formatPrice(row.price)}</span>
                  </div>
                </td>
                <td className="px-2 py-1.5">
                  <div className="flex flex-wrap gap-1">
                    {row.patterns.length === 0 && <span className="text-muted">—</span>}
                    {row.patterns.map((p) => (
                      <span key={p.pattern} className={cn("text-[10px] font-medium", directionTone(p.direction))}>
                        {p.pattern}
                      </span>
                    ))}
                  </div>
                </td>
                <td className="px-2 py-1.5 text-[11px] text-muted">
                  {row.retracement_percentage === null || !row.retracement_zone ? (
                    "—"
                  ) : (
                    <span title={ZONE_META[row.retracement_zone].label}>
                      <span className={row.retracement_direction === "UP" ? "text-profit" : "text-loss"}>
                        {row.retracement_direction === "UP" ? "↑" : "↓"}
                      </span>{" "}
                      {Math.round(row.retracement_percentage)}%
                    </span>
                  )}
                </td>
                <td className="px-3 py-1.5 text-right">
                  <span className={cn("tabular", directionTone(row.overall_bias))}>
                    {row.alignment_score === null ? "—" : Math.round(row.alignment_score)}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
