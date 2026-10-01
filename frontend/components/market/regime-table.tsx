"use client";

import { useMemo, useState } from "react";

import { RegimeBadge } from "@/components/market/regime-badge";
import {
  REGIME_META,
  REGIME_ORDER,
  changeTone,
  formatPercent,
  formatPrice,
  formatScore,
  humanize,
} from "@/lib/intelligence-format";
import { cn } from "@/lib/utils";
import type { MarketRegime, SymbolRegimeSummary } from "@/types/intelligence";

type SortKey = "symbol" | "change" | "confidence" | "trend" | "momentum" | "rs";

const SORTERS: Record<SortKey, (s: SymbolRegimeSummary) => number | string> = {
  symbol: (s) => s.symbol,
  change: (s) => s.change_percent ?? -Infinity,
  confidence: (s) => s.confidence,
  trend: (s) => s.trend_quality_score ?? -Infinity,
  momentum: (s) => s.momentum_score ?? -Infinity,
  // Rank 1 is strongest, so sort by negative rank for "descending = strongest first".
  rs: (s) => (s.relative_strength_rank === null ? -Infinity : -s.relative_strength_rank),
};

/**
 * Dense, sortable table of every analysed symbol's regime. Rows are
 * clickable and select the symbol for the detail panel; regime chips
 * filter the list.
 */
export function RegimeTable({
  symbols,
  selected,
  onSelect,
}: {
  symbols: SymbolRegimeSummary[];
  selected: string | null;
  onSelect: (symbol: string) => void;
}) {
  const [filter, setFilter] = useState<MarketRegime | null>(null);
  const [sortKey, setSortKey] = useState<SortKey>("rs");
  const [descending, setDescending] = useState(true);

  const counts = useMemo(() => {
    const result = {} as Record<MarketRegime, number>;
    for (const regime of REGIME_ORDER) result[regime] = 0;
    for (const s of symbols) result[s.regime] += 1;
    return result;
  }, [symbols]);

  const rows = useMemo(() => {
    const filtered = filter ? symbols.filter((s) => s.regime === filter) : symbols;
    const getter = SORTERS[sortKey];
    return [...filtered].sort((a, b) => {
      const av = getter(a);
      const bv = getter(b);
      const cmp = av < bv ? -1 : av > bv ? 1 : 0;
      return descending ? -cmp : cmp;
    });
  }, [symbols, filter, sortKey, descending]);

  function toggleSort(key: SortKey) {
    if (key === sortKey) {
      setDescending((d) => !d);
    } else {
      setSortKey(key);
      setDescending(key !== "symbol");
    }
  }

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap gap-1.5 border-b border-border px-4 py-3">
        <FilterChip active={filter === null} onClick={() => setFilter(null)}>
          All <span className="tabular text-muted">{symbols.length}</span>
        </FilterChip>
        {REGIME_ORDER.map((regime) => (
          <FilterChip
            key={regime}
            active={filter === regime}
            disabled={counts[regime] === 0}
            onClick={() => setFilter(filter === regime ? null : regime)}
          >
            {REGIME_META[regime].short} <span className="tabular text-muted">{counts[regime]}</span>
          </FilterChip>
        ))}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-muted">
              <SortHeader label="Symbol" k="symbol" {...{ sortKey, descending, toggleSort }} />
              <th className="px-2 py-2 text-right font-medium">Price</th>
              <SortHeader label="Change" k="change" align="right" {...{ sortKey, descending, toggleSort }} />
              <th className="px-2 py-2 font-medium">Regime</th>
              <SortHeader label="Conf." k="confidence" align="right" {...{ sortKey, descending, toggleSort }} />
              <SortHeader label="Trend Q." k="trend" align="right" {...{ sortKey, descending, toggleSort }} />
              <SortHeader label="Mom." k="momentum" align="right" {...{ sortKey, descending, toggleSort }} />
              <SortHeader label="RS rank" k="rs" align="right" {...{ sortKey, descending, toggleSort }} />
              <th className="px-2 py-2 font-medium">VWAP</th>
              <th className="px-2 py-2 font-medium">Volatility</th>
              <th className="px-2 py-2 text-right font-medium">Rel. vol</th>
            </tr>
          </thead>
          <tbody>
            {rows.length === 0 && (
              <tr>
                <td colSpan={11} className="px-4 py-6 text-center text-muted">
                  {symbols.length === 0 ? "Waiting for the first analysis…" : "No symbols in this regime."}
                </td>
              </tr>
            )}
            {rows.map((s) => (
              <tr
                key={s.symbol}
                onClick={() => onSelect(s.symbol)}
                className={cn(
                  "cursor-pointer border-b border-border last:border-0 hover:bg-surface-alt",
                  selected === s.symbol && "bg-surface-alt"
                )}
                title={s.top_reason ?? undefined}
              >
                <td className="px-2 py-1.5">
                  <div className="flex flex-col">
                    <span className="font-medium">{s.symbol}</span>
                    {s.name && <span className="max-w-28 truncate text-[11px] text-muted">{s.name}</span>}
                  </div>
                </td>
                <td className="tabular px-2 py-1.5 text-right">{formatPrice(s.price)}</td>
                <td className={cn("tabular px-2 py-1.5 text-right", changeTone(s.change_percent))}>
                  {formatPercent(s.change_percent)}
                </td>
                <td className="px-2 py-1.5">
                  <RegimeBadge regime={s.regime} short />
                </td>
                <td className="tabular px-2 py-1.5 text-right">{formatScore(s.confidence)}</td>
                <td className="tabular px-2 py-1.5 text-right">
                  {formatScore(s.trend_quality_score)}
                  {s.trend_direction && s.trend_direction !== "SIDEWAYS" && (
                    <span className={cn("ml-1", s.trend_direction === "UP" ? "text-profit" : "text-loss")}>
                      {s.trend_direction === "UP" ? "↑" : "↓"}
                    </span>
                  )}
                </td>
                <td className="tabular px-2 py-1.5 text-right">{formatScore(s.momentum_score)}</td>
                <td className="tabular px-2 py-1.5 text-right">{s.relative_strength_rank ?? "—"}</td>
                <td
                  className={cn(
                    "px-2 py-1.5 text-xs",
                    s.price_vs_vwap === "ABOVE" && "text-profit",
                    s.price_vs_vwap === "BELOW" && "text-loss",
                    (s.price_vs_vwap === "AT" || !s.price_vs_vwap) && "text-muted"
                  )}
                >
                  {humanize(s.price_vs_vwap)}
                </td>
                <td
                  className={cn(
                    "px-2 py-1.5 text-xs",
                    s.volatility_level === "HIGH" || s.volatility_level === "EXTREME" ? "text-warning" : "text-muted"
                  )}
                >
                  {humanize(s.volatility_level)}
                </td>
                <td className="tabular px-2 py-1.5 text-right text-muted">
                  {s.relative_volume === null ? "—" : `${s.relative_volume.toFixed(2)}×`}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FilterChip({
  active,
  disabled = false,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled && !active}
      className={cn(
        "rounded-sm border px-2 py-0.5 text-xs transition-colors",
        active
          ? "border-accent/40 bg-accent/10 text-foreground"
          : "border-border text-muted hover:text-foreground",
        disabled && !active && "cursor-not-allowed opacity-40 hover:text-muted"
      )}
    >
      {children}
    </button>
  );
}

function SortHeader({
  label,
  k,
  align = "left",
  sortKey,
  descending,
  toggleSort,
}: {
  label: string;
  k: SortKey;
  align?: "left" | "right";
  sortKey: SortKey;
  descending: boolean;
  toggleSort: (key: SortKey) => void;
}) {
  const active = sortKey === k;
  return (
    <th
      className={cn("px-2 py-2 font-medium", align === "right" && "text-right")}
      aria-sort={active ? (descending ? "descending" : "ascending") : "none"}
    >
      <button
        type="button"
        onClick={() => toggleSort(k)}
        className={cn("uppercase tracking-wide hover:text-foreground", active && "text-foreground")}
      >
        {label}
        {active ? (descending ? " ↓" : " ↑") : ""}
      </button>
    </th>
  );
}
