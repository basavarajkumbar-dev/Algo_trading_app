import { REGIME_META, REGIME_ORDER } from "@/lib/intelligence-format";
import { cn } from "@/lib/utils";
import type { MarketBreadth, MarketRegime } from "@/types/intelligence";

const REGIME_BAR: Record<MarketRegime, string> = {
  STRONG_BULLISH: "bg-profit",
  STRONG_BEARISH: "bg-loss",
  BREAKOUT: "bg-accent/60",
  RANGE: "bg-muted/60",
  HIGH_VOLATILITY: "bg-warning",
  UNKNOWN: "bg-surface-alt",
};

export function BreadthPanel({
  breadth,
  distribution,
}: {
  breadth: MarketBreadth;
  distribution: Record<MarketRegime, number>;
}) {
  const total = breadth.symbols_counted || 1;
  const advancingPct = (breadth.advancing / total) * 100;
  const decliningPct = (breadth.declining / total) * 100;

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-2">
        <div className="flex justify-between text-sm">
          <span>
            <span className="tabular text-profit">{breadth.advancing}</span>
            <span className="text-muted"> advancing</span>
          </span>
          <span>
            <span className="tabular text-loss">{breadth.declining}</span>
            <span className="text-muted"> declining</span>
          </span>
        </div>
        <div className="flex h-1.5 w-full overflow-hidden rounded-sm bg-surface-alt">
          <div className="bg-profit" style={{ width: `${advancingPct}%` }} />
          <div className="ml-auto bg-loss" style={{ width: `${decliningPct}%` }} />
        </div>
        <span className="text-[11px] text-muted">
          Change vs the session open, across {breadth.symbols_counted} constituents
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 text-sm">
        <div className="flex flex-col">
          <span className="tabular text-lg font-semibold">
            {breadth.percent_above_vwap === null ? "—" : `${Math.round(breadth.percent_above_vwap)}%`}
          </span>
          <span className="text-xs text-muted">above VWAP</span>
        </div>
        <div className="flex flex-col">
          <span className="tabular text-lg font-semibold">
            <span className="text-profit">{breadth.bullish_regimes}</span>
            <span className="text-muted"> / </span>
            <span className="text-loss">{breadth.bearish_regimes}</span>
          </span>
          <span className="text-xs text-muted">bullish / bearish regimes</span>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <span className="text-xs text-muted">Constituents by regime</span>
        <div className="flex h-2 w-full overflow-hidden rounded-sm bg-surface-alt">
          {REGIME_ORDER.map((regime) => {
            const count = distribution[regime] ?? 0;
            if (count === 0) return null;
            return (
              <div
                key={regime}
                className={cn(REGIME_BAR[regime])}
                style={{ width: `${(count / total) * 100}%` }}
                title={`${REGIME_META[regime].label}: ${count}`}
              />
            );
          })}
        </div>
        <ul className="grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          {REGIME_ORDER.filter((r) => (distribution[r] ?? 0) > 0).map((regime) => (
            <li key={regime} className="flex items-center justify-between gap-2">
              <span className="flex items-center gap-1.5 text-muted">
                <span className={cn("h-2 w-2 rounded-sm", REGIME_BAR[regime])} aria-hidden />
                {REGIME_META[regime].label}
              </span>
              <span className="tabular">{distribution[regime]}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
