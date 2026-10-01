import { cn } from "@/lib/utils";
import { factorTone, formatScore } from "@/lib/intelligence-format";
import type { FactorScore } from "@/types/intelligence";

const TONE_BAR: Record<ReturnType<typeof factorTone>, string> = {
  positive: "bg-profit",
  negative: "bg-loss",
  neutral: "bg-muted",
  intensity: "bg-accent",
};

const TONE_TEXT: Record<ReturnType<typeof factorTone>, string> = {
  positive: "text-profit",
  negative: "text-loss",
  neutral: "text-muted",
  intensity: "text-foreground",
};

/**
 * Factor scores as horizontal 0-100 bars. Directional factors show a
 * tick at 50 (neutral) and fill green/red by side; intensity factors
 * fill from zero in the accent colour, so a high volatility score is
 * never mistaken for a bullish one.
 */
export function FactorList({ factors }: { factors: FactorScore[] }) {
  if (factors.length === 0) {
    return <p className="text-sm text-muted">No factor data yet.</p>;
  }
  return (
    <ul className="flex flex-col gap-3">
      {factors.map((factor) => {
        const tone = factorTone(factor.score, factor.scale);
        const score = factor.score ?? 0;
        return (
          <li key={factor.key} className="flex flex-col gap-1" title={factor.detail ?? undefined}>
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="text-foreground">{factor.label}</span>
              <span className="flex items-baseline gap-2">
                <span className={cn("text-xs", TONE_TEXT[tone])}>{factor.interpretation}</span>
                <span className="tabular w-7 text-right font-medium">{formatScore(factor.score)}</span>
              </span>
            </div>
            <div className="relative h-1.5 w-full overflow-hidden rounded-sm bg-surface-alt">
              {factor.scale === "directional" ? (
                <>
                  <div
                    className={cn("absolute inset-y-0", TONE_BAR[tone])}
                    style={
                      score >= 50
                        ? { left: "50%", width: `${score - 50}%` }
                        : { left: `${score}%`, width: `${50 - score}%` }
                    }
                  />
                  <div className="absolute inset-y-0 left-1/2 w-px bg-border" aria-hidden />
                </>
              ) : (
                <div
                  className={cn("absolute inset-y-0 left-0", TONE_BAR[tone])}
                  style={{ width: `${score}%` }}
                />
              )}
            </div>
            {factor.detail && <span className="text-[11px] text-muted">{factor.detail}</span>}
          </li>
        );
      })}
    </ul>
  );
}
