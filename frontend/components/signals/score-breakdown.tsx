import { formatScore } from "@/lib/intelligence-format";
import { scoreTone } from "@/lib/signal-format";
import { cn } from "@/lib/utils";
import type { ScoreBreakdown as Breakdown } from "@/types/signals";

/**
 * Each component's points against its weight. Weights are scaled so the
 * applicable ones sum to 100; components that do not apply to the
 * strategy are listed but carry no weight.
 */
export function ScoreBreakdown({ breakdown }: { breakdown: Breakdown }) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end justify-between gap-3">
        <div className="flex items-baseline gap-1">
          <span className="tabular text-3xl font-semibold leading-none">{formatScore(breakdown.total)}</span>
          <span className="text-sm text-muted">/ 100</span>
        </div>
        <span className="text-[11px] text-muted">
          {breakdown.executable ? "At or above" : "Below"} the executable threshold ({breakdown.executable_threshold})
        </span>
      </div>
      <div className="relative h-2 w-full overflow-hidden rounded-sm bg-surface-alt">
        <div className={cn("h-full", scoreTone(breakdown.total))} style={{ width: `${breakdown.total}%` }} />
        <div
          className="absolute inset-y-0 w-px bg-foreground"
          style={{ left: `${breakdown.executable_threshold}%` }}
          title={`Executable threshold ${breakdown.executable_threshold}`}
        />
      </div>

      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-[10px] uppercase tracking-wide text-muted">
            <th className="py-1 font-medium">Component</th>
            <th className="py-1 font-medium">Contribution</th>
            <th className="py-1 text-right font-medium">Points</th>
            <th className="py-1 text-right font-medium">Weight</th>
          </tr>
        </thead>
        <tbody>
          {breakdown.components.map((c) => (
            <tr key={c.key} className={cn("border-t border-border", !c.applicable && "text-muted")}>
              <td className="py-1.5 pr-2">
                <div className="flex flex-col">
                  <span className="font-medium">{c.label}</span>
                  <span className="text-[10px] text-muted">{c.detail}</span>
                </div>
              </td>
              <td className="w-[38%] py-1.5 pr-2">
                {c.applicable ? (
                  <div className="h-1.5 w-full overflow-hidden rounded-sm bg-surface-alt" title={`${Math.round((c.score ?? 0) * 100)}% of its weight`}>
                    <div
                      className={cn("h-full", c.passed ? "bg-profit" : "bg-loss/70")}
                      style={{ width: `${(c.score ?? 0) * 100}%` }}
                    />
                  </div>
                ) : (
                  <span className="text-[10px]">not applicable</span>
                )}
              </td>
              <td className="tabular py-1.5 text-right">{c.applicable ? c.points.toFixed(1) : "—"}</td>
              <td className="tabular py-1.5 text-right text-muted">
                {c.applicable ? c.effective_weight.toFixed(1) : "—"}
                <span className="ml-1 text-[10px]">({c.weight})</span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[11px] text-muted">
        Weight shows the scaled weight, with the configured weight in brackets. The configured
        initial weights are a starting model, not proven optimal.
      </p>
    </div>
  );
}
