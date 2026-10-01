import { Check, Minus } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { formatIstTime, formatScore, humanize } from "@/lib/intelligence-format";
import { PATTERN_META, PATTERN_ORDER, directionLabel, directionTone } from "@/lib/price-action-format";
import { cn } from "@/lib/utils";
import type { PatternResult } from "@/types/price-action";

function Meter({ label, value, tone }: { label: string; value: number; tone: string }) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <span className="w-16 shrink-0 text-muted">{label}</span>
      <div className="h-1 flex-1 overflow-hidden rounded-sm bg-surface-alt">
        <div className={cn("h-full", tone)} style={{ width: `${value}%` }} />
      </div>
      <span className="tabular w-6 text-right">{formatScore(value)}</span>
    </div>
  );
}

function ContextChips({ result }: { result: PatternResult }) {
  const ctx = result.context;
  const chips: string[] = [`Prior trend ${humanize(ctx.prior_trend).toLowerCase()}`];
  if (ctx.location === "AT_SUPPORT" || ctx.location === "AT_RESISTANCE") {
    chips.push(
      `${ctx.location === "AT_SUPPORT" ? "At support" : "At resistance"}${
        ctx.nearest_level ? `: ${ctx.nearest_level.label}` : ""
      }`
    );
  } else if (ctx.location === "THROUGH_LEVEL" && ctx.nearest_level) {
    chips.push(`Crossed ${ctx.nearest_level.label}`);
  } else if (ctx.nearest_level) {
    chips.push(`Nearest level ${ctx.nearest_level.label} (${ctx.nearest_level.distance_atr} ATR)`);
  }
  if (ctx.relative_volume !== null) chips.push(`Volume ${ctx.relative_volume.toFixed(1)}×`);
  if (ctx.range_vs_average !== null) chips.push(`Range ${ctx.range_vs_average.toFixed(2)}× avg`);
  return (
    <div className="flex flex-wrap gap-1">
      {chips.map((chip) => (
        <span key={chip} className="rounded-sm bg-surface-alt px-1.5 py-0.5 text-[10px] text-muted">
          {chip}
        </span>
      ))}
    </div>
  );
}

/**
 * All six detectors evaluated on the latest closed candle, detected or
 * not, with the reasons either way.
 */
export function PatternGrid({ patterns }: { patterns: PatternResult[] }) {
  const byType = new Map(patterns.map((p) => [p.pattern, p]));
  const ordered = PATTERN_ORDER.map((type) => byType.get(type)).filter(
    (p): p is PatternResult => p !== undefined
  );
  if (ordered.length === 0) return <p className="text-sm text-muted">No pattern data yet.</p>;

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-2 2xl:grid-cols-3">
      {ordered.map((result) => {
        const meta = PATTERN_META[result.pattern];
        return (
          <div
            key={result.pattern}
            className={cn(
              "flex flex-col gap-2.5 rounded border p-3",
              result.detected ? "border-border bg-surface" : "border-border/60 bg-surface/40"
            )}
          >
            <div className="flex items-start justify-between gap-2">
              <div className="flex flex-col">
                <span className="text-sm font-medium">
                  {meta.name} <span className="text-muted">({result.pattern})</span>
                </span>
                <span className="text-[11px] text-muted">{meta.meaning}</span>
              </div>
              {result.detected ? (
                <Badge
                  variant={
                    result.direction === "BULLISH"
                      ? "connected"
                      : result.direction === "BEARISH"
                        ? "disconnected"
                        : "accent"
                  }
                  className="shrink-0"
                >
                  {directionLabel(result.direction)}
                </Badge>
              ) : (
                <Badge variant="neutral" className="shrink-0">
                  Not present
                </Badge>
              )}
            </div>

            {result.detected && (
              <>
                <Meter label="Confidence" value={result.confidence} tone="bg-foreground/80" />
                <Meter
                  label="Strength"
                  value={result.strength}
                  tone={
                    result.direction === "BULLISH"
                      ? "bg-profit"
                      : result.direction === "BEARISH"
                        ? "bg-loss"
                        : "bg-accent"
                  }
                />
                <ContextChips result={result} />
              </>
            )}

            <ul className={cn("flex flex-col gap-1 text-xs", !result.detected && "text-muted")}>
              {(result.detected ? result.reasons : result.reasons.slice(0, 2)).map((reason) => (
                <li key={reason} className="flex items-start gap-1.5">
                  {result.detected ? (
                    <Check className="mt-0.5 h-3 w-3 shrink-0 text-profit" aria-hidden />
                  ) : (
                    <Minus className="mt-0.5 h-3 w-3 shrink-0" aria-hidden />
                  )}
                  <span>{reason}</span>
                </li>
              ))}
            </ul>
            {result.detected && (
              <span className={cn("text-[10px]", directionTone(result.direction))}>
                Candle {formatIstTime(result.timestamp)}
              </span>
            )}
          </div>
        );
      })}
    </div>
  );
}
