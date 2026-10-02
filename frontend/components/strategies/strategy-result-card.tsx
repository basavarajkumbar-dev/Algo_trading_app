import { Check, Minus, X } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatPrice, formatScore } from "@/lib/intelligence-format";
import {
  STRATEGY_LABEL,
  STRATEGY_STAGES,
  directionTone,
  orderedChecks,
  percentFromEntry,
  stageIndex,
  stageLabel,
} from "@/lib/strategy-format";
import { cn } from "@/lib/utils";
import type { StrategyResult } from "@/types/strategies";

function StageChain({ result }: { result: StrategyResult }) {
  const stages = STRATEGY_STAGES[result.strategy];
  const reached = stageIndex(result.strategy, result.stage);
  return (
    <ol className="flex flex-wrap items-center gap-1 text-[10px]">
      {stages.map((stage, i) => (
        <li key={stage} className="flex items-center gap-1">
          <span
            className={cn(
              "rounded-sm border px-1.5 py-0.5",
              i <= reached ? "border-accent/40 bg-accent/10 text-foreground" : "border-border text-muted"
            )}
          >
            {stageLabel(stage)}
          </span>
          {i < stages.length - 1 && <span className="text-muted">→</span>}
        </li>
      ))}
    </ol>
  );
}

function Candidate({ label, price, basis, entry, result }: {
  label: string;
  price: number | null;
  basis: string | null;
  entry: number | null;
  result: StrategyResult;
}) {
  const move = price !== null && entry !== null && label !== "Entry" ? percentFromEntry(entry, price, result.direction) : null;
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wide text-muted">{label}</span>
      <span className="tabular text-base font-semibold">{formatPrice(price)}</span>
      {move !== null && (
        <span className={cn("tabular text-[11px]", move >= 0 ? "text-profit" : "text-loss")}>
          {move >= 0 ? "+" : ""}
          {move.toFixed(2)}%
        </span>
      )}
      {basis && <span className="text-[11px] text-muted">{basis}</span>}
    </div>
  );
}

/**
 * One strategy's evaluation: candidates when eligible, otherwise what
 * blocks it, plus every check it made and how far its concept got.
 */
export function StrategyResultCard({ result, routed = true }: { result: StrategyResult; routed?: boolean }) {
  return (
    <div className={cn("flex flex-col gap-4 rounded border p-4", result.eligible ? "border-accent/40" : "border-border")}>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <span className="text-sm font-medium">{STRATEGY_LABEL[result.strategy]}</span>
          {!routed && <span className="text-[11px] text-warning">Not routed for this regime (research view)</span>}
        </div>
        <div className="flex items-center gap-2">
          {result.direction !== "NONE" && (
            <span className={cn("text-xs font-medium", directionTone(result.direction))}>{result.direction}</span>
          )}
          <Badge variant={result.eligible ? "accent" : "neutral"}>
            {result.eligible ? "Candidate" : "No setup"}
          </Badge>
        </div>
      </div>

      <StageChain result={result} />

      {result.eligible ? (
        <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
          <Candidate label="Entry" price={result.entry_candidate} basis={result.entry_basis} entry={result.entry_candidate} result={result} />
          <Candidate label="Stop" price={result.stop_candidate} basis={result.stop_basis} entry={result.entry_candidate} result={result} />
          <Candidate label="Target" price={result.target_candidate} basis={result.target_basis} entry={result.entry_candidate} result={result} />
          <div className="flex flex-col gap-0.5">
            <span className="text-[11px] uppercase tracking-wide text-muted">Reward : risk</span>
            <span className="tabular text-base font-semibold">{result.reward_risk?.toFixed(2) ?? "—"}</span>
            <span className="text-[11px] text-muted">confidence {formatScore(result.confidence)}</span>
          </div>
        </div>
      ) : (
        <ul className="flex flex-col gap-1 text-xs">
          {result.blockers.map((b) => (
            <li key={b} className="flex items-start gap-1.5 text-muted">
              <X className="mt-0.5 h-3 w-3 shrink-0 text-loss" aria-hidden />
              <span>{b}</span>
            </li>
          ))}
        </ul>
      )}

      <details className="text-xs">
        <summary className="cursor-pointer text-muted hover:text-foreground">
          All checks · setup score {formatScore(result.setup_score)}
        </summary>
        <ul className="mt-2 flex flex-col gap-1">
          {orderedChecks(result).map((c) => (
            <li key={c.key} className="flex items-start gap-1.5">
              {c.passed ? (
                <Check className="mt-0.5 h-3 w-3 shrink-0 text-profit" aria-hidden />
              ) : (
                <Minus className="mt-0.5 h-3 w-3 shrink-0 text-muted" aria-hidden />
              )}
              <span className={cn(!c.passed && "text-muted")}>
                <span className="font-medium">{c.label}</span>
                <span className="text-muted">{c.required ? " · required" : ` · weight ${c.weight}`}</span>: {c.detail}
              </span>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
