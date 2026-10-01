import { formatIstTime, formatPrice, formatScore } from "@/lib/intelligence-format";
import { GAUGE_MAX, ZONE_META, ZONE_ORDER, gaugePosition } from "@/lib/price-action-format";
import { cn } from "@/lib/utils";
import type { RetracementResult } from "@/types/price-action";

/**
 * Retracement of the latest significant impulse: a 0-120% gauge with
 * the zone bands, the current position and the deepest pullback so far.
 */
export function RetracementPanel({ result }: { result: RetracementResult | null }) {
  if (!result || !result.detected) {
    return (
      <div className="flex flex-col gap-2 text-sm text-muted">
        <p>No significant impulse to measure.</p>
        {result?.reasons.map((r) => (
          <p key={r} className="text-xs">
            {r}
          </p>
        ))}
      </div>
    );
  }

  const up = result.direction === "UP";
  const current = result.retracement_percentage ?? 0;
  const deepest = result.max_retracement_percentage ?? current;
  const zone = result.zone ? ZONE_META[result.zone] : null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm">
            <span className={up ? "text-profit" : "text-loss"}>{up ? "Up" : "Down"} impulse</span>{" "}
            <span className="text-muted">
              {result.impulse_size_atr?.toFixed(1)} ATR over {result.impulse_candles} candles
            </span>
          </span>
          <span className="tabular text-xs text-muted">
            {formatPrice(result.impulse_start)} → {formatPrice(result.impulse_end)}
          </span>
        </div>
        <div className="flex flex-col items-end">
          <span className="tabular text-2xl font-semibold leading-none">{current.toFixed(0)}%</span>
          <span className="mt-1 text-[11px] text-muted">{zone?.label ?? "—"}</span>
        </div>
      </div>

      <div className="flex flex-col gap-1">
        <div className="relative h-3 w-full overflow-hidden rounded-sm">
          {ZONE_ORDER.map((z) => {
            const meta = ZONE_META[z];
            return (
              <div
                key={z}
                className={cn("absolute inset-y-0", meta.band)}
                style={{
                  left: `${(meta.from / GAUGE_MAX) * 100}%`,
                  width: `${((meta.to - meta.from) / GAUGE_MAX) * 100}%`,
                }}
                title={`${meta.label} ${meta.range}`}
              />
            );
          })}
          {deepest > current + 1 && (
            <div
              className="absolute inset-y-0 w-0.5 bg-muted"
              style={{ left: `${gaugePosition(deepest)}%` }}
              title={`Deepest: ${deepest.toFixed(0)}%`}
            />
          )}
          <div
            className="absolute inset-y-0 w-1 -translate-x-1/2 bg-foreground"
            style={{ left: `${gaugePosition(current)}%` }}
            title={`Now: ${current.toFixed(0)}%`}
          />
        </div>
        <div className="tabular relative h-3 text-[10px] text-muted">
          {[0, 40, 50, 60, 80, 100].map((tick) => (
            <span
              key={tick}
              className="absolute -translate-x-1/2"
              style={{ left: `${gaugePosition(tick)}%` }}
            >
              {tick}
            </span>
          ))}
        </div>
        {deepest > current + 1 && (
          <span className="text-[11px] text-muted">
            Deepest pullback so far {deepest.toFixed(0)}%
            {result.max_zone ? ` (${ZONE_META[result.max_zone].label.toLowerCase()})` : ""}
          </span>
        )}
      </div>

      <table className="w-full text-xs">
        <thead>
          <tr className="text-left text-muted">
            <th className="py-1 font-medium">Level</th>
            <th className="py-1 text-right font-medium">Price</th>
          </tr>
        </thead>
        <tbody>
          {result.levels.map((level) => (
            <tr key={level.percent} className="border-t border-border">
              <td className="py-1">
                {level.percent}%{level.percent === 100 ? " (impulse start)" : ""}
              </td>
              <td className="tabular py-1 text-right">{formatPrice(level.price)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="grid grid-cols-2 gap-3 text-xs">
        <div className="flex flex-col">
          <span className="text-muted">Swing high</span>
          <span className="tabular">
            {formatPrice(result.swing_high)}{" "}
            <span className="text-muted">{formatIstTime(result.swing_high_time)}</span>
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-muted">Swing low</span>
          <span className="tabular">
            {formatPrice(result.swing_low)}{" "}
            <span className="text-muted">{formatIstTime(result.swing_low_time)}</span>
          </span>
        </div>
        <div className="flex flex-col">
          <span className="text-muted">Impulse strength</span>
          <span className="tabular">{formatScore(result.strength)}</span>
        </div>
        <div className="flex flex-col">
          <span className="text-muted">Confidence</span>
          <span className="tabular">{formatScore(result.confidence)}</span>
        </div>
      </div>

      <ul className="flex flex-col gap-1 text-xs text-muted">
        {result.reasons.map((r) => (
          <li key={r}>· {r}</li>
        ))}
      </ul>
      <p className="text-[11px] text-muted">{result.note}</p>
    </div>
  );
}
