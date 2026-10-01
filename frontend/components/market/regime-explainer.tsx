import { Check, Minus } from "lucide-react";

import { RegimeBadge } from "@/components/market/regime-badge";
import { REGIME_META, REGIME_ORDER, formatIstTime, formatScore } from "@/lib/intelligence-format";
import { cn } from "@/lib/utils";
import type { RegimeResult } from "@/types/intelligence";

/**
 * The "why" behind a regime: confidence, the conditions that were met,
 * the ones that weren't, and every regime's score, so the
 * classification can always be traced back to its evidence.
 */
export function RegimeExplainer({
  result,
  showScores = true,
}: {
  result: RegimeResult;
  showScores?: boolean;
}) {
  const meta = REGIME_META[result.regime];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex flex-col gap-1.5">
          <RegimeBadge regime={result.regime} className="w-fit text-sm" />
          <p className="max-w-prose text-xs text-muted">{meta.description}</p>
        </div>
        <div className="flex shrink-0 flex-col items-end">
          <span className="tabular text-2xl font-semibold leading-none">
            {formatScore(result.confidence)}
            <span className="text-sm font-normal text-muted">%</span>
          </span>
          <span className="mt-1 text-[11px] text-muted">confidence</span>
        </div>
      </div>

      <div className="h-1 w-full overflow-hidden rounded-sm bg-surface-alt">
        <div className="h-full bg-foreground/80" style={{ width: `${result.confidence}%` }} />
      </div>

      {result.reasons.length > 0 && (
        <ul className="flex flex-col gap-1.5 text-sm">
          {result.reasons.map((reason) => (
            <li key={reason} className="flex items-start gap-2">
              <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-profit" aria-label="Met" />
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      )}

      {result.counter_reasons.length > 0 && (
        <ul className="flex flex-col gap-1.5 text-sm text-muted">
          {result.counter_reasons.map((reason) => (
            <li key={reason} className="flex items-start gap-2">
              <Minus className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-label="Not met" />
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      )}

      {showScores && Object.keys(result.scores).length > 0 && (
        <div className="flex flex-col gap-1.5 border-t border-border pt-3">
          <span className="text-xs text-muted">Score by regime</span>
          {REGIME_ORDER.filter((r) => result.scores[r] !== undefined).map((regime) => {
            const score = result.scores[regime] ?? 0;
            const isWinner = regime === result.regime;
            return (
              <div key={regime} className="flex items-center gap-2 text-xs">
                <span className={cn("w-28 shrink-0", isWinner ? "text-foreground" : "text-muted")}>
                  {REGIME_META[regime].label}
                </span>
                <div className="h-1 flex-1 overflow-hidden rounded-sm bg-surface-alt">
                  <div
                    className={cn("h-full", isWinner ? "bg-accent" : "bg-muted/50")}
                    style={{ width: `${score}%` }}
                  />
                </div>
                <span className="tabular w-7 text-right">{formatScore(score)}</span>
              </div>
            );
          })}
        </div>
      )}

      <p className="text-[11px] text-muted">
        {result.candles_analyzed} closed {result.timeframe} candles, last at{" "}
        {formatIstTime(result.data_as_of)}. {result.note}
      </p>
    </div>
  );
}
