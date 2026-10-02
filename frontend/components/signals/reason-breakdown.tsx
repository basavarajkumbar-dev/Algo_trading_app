import { Check, Minus, X } from "lucide-react";

import { reasonMark, stripMark } from "@/lib/signal-format";
import { cn } from "@/lib/utils";
import type { StrategyCheck } from "@/types/strategies";

/**
 * "Why this signal?": the scoring explanation (one line per component,
 * then the final score) and the strategy's own checks that made the
 * setup eligible.
 */
export function ReasonBreakdown({
  reasons,
  checks,
}: {
  reasons: string[];
  checks: StrategyCheck[];
}) {
  const required = checks.filter((c) => c.required);
  const weighted = checks.filter((c) => !c.required);
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <div className="flex flex-col gap-2">
        <span className="text-[11px] uppercase tracking-wide text-muted">Why this signal</span>
        <ul className="flex flex-col gap-1.5 text-sm">
          {reasons.map((line) => {
            const mark = reasonMark(line);
            if (mark === "total") {
              return (
                <li key={line} className="mt-1 border-t border-border pt-2 font-semibold">
                  {line}
                </li>
              );
            }
            return (
              <li key={line} className={cn("flex items-start gap-2", mark !== "pass" && "text-muted")}>
                {mark === "pass" && <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-profit" aria-label="met" />}
                {mark === "fail" && <X className="mt-0.5 h-3.5 w-3.5 shrink-0 text-loss" aria-label="not met" />}
                {mark === "na" && <Minus className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-label="not applicable" />}
                <span>{stripMark(line)}</span>
              </li>
            );
          })}
        </ul>
      </div>
      <div className="flex flex-col gap-2">
        <span className="text-[11px] uppercase tracking-wide text-muted">Strategy checks</span>
        <ul className="flex flex-col gap-1 text-xs">
          {[...required, ...weighted].map((c) => (
            <li key={c.key} className={cn("flex items-start gap-1.5", !c.passed && "text-muted")}>
              {c.passed ? (
                <Check className="mt-0.5 h-3 w-3 shrink-0 text-profit" aria-hidden />
              ) : (
                <Minus className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
              )}
              <span>
                <span className="font-medium">{c.label}</span>
                <span className="text-muted">{c.required ? " · required" : ` · weight ${c.weight}`}</span>: {c.detail}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
