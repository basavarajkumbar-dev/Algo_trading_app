import { positionGeometry } from "@/lib/dashboard-format";
import { formatPrice } from "@/lib/intelligence-format";
import { cn } from "@/lib/utils";

const pct = (x: number) => `${(Math.min(1, Math.max(0, x)) * 100).toFixed(2)}%`;

/**
 * SL ───── ENTRY ───── CURRENT ───── TARGET on one axis from the stop (left)
 * to the target (right), for longs and shorts alike. The segment between
 * entry and the current price is green when the trade is ahead, red when
 * behind. A current price past the stop or target is pinned to that end
 * and flagged.
 */
export function PositionBar({
  stop,
  entry,
  target,
  current,
}: {
  stop: number;
  entry: number;
  target: number;
  current: number | null;
}) {
  const g = positionGeometry(stop, entry, target, current);
  const ahead = g.current !== null && g.current >= g.entry;
  const lo = g.currentClamped === null ? g.entry : Math.min(g.entry, g.currentClamped);
  const hi = g.currentClamped === null ? g.entry : Math.max(g.entry, g.currentClamped);

  return (
    <div className="flex flex-col gap-1.5" data-testid="position-bar" role="img"
      aria-label={`Stop ${formatPrice(stop)}, entry ${formatPrice(entry)}, current ${formatPrice(current)}, target ${formatPrice(target)}`}
    >
      <div className="flex justify-between text-[10px] uppercase tracking-wide text-muted">
        <span>SL</span>
        <span>Target</span>
      </div>
      <div className="relative h-7">
        {/* track: loss zone left of entry, profit zone right of it */}
        <div className="absolute inset-x-0 top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-surface-alt" />
        <div
          className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-l-full bg-loss/25"
          style={{ left: 0, width: pct(g.entry) }}
        />
        <div
          className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-r-full bg-profit/20"
          style={{ left: pct(g.entry), right: 0 }}
        />
        {g.currentClamped !== null && (
          <div
            className={cn("absolute top-1/2 h-1.5 -translate-y-1/2", ahead ? "bg-profit" : "bg-loss")}
            style={{ left: pct(lo), width: `calc(${pct(hi)} - ${pct(lo)})` }}
            data-testid="position-bar-move"
          />
        )}
        {/* stop and target ends */}
        <span className="absolute left-0 top-1/2 h-4 w-0.5 -translate-y-1/2 bg-loss" />
        <span className="absolute right-0 top-1/2 h-4 w-0.5 -translate-y-1/2 bg-profit" />
        {/* entry tick */}
        <span
          className="absolute top-1/2 h-5 w-0.5 -translate-x-1/2 -translate-y-1/2 bg-foreground/70"
          style={{ left: pct(g.entry) }}
          data-testid="position-bar-entry"
        />
        {/* current marker */}
        {g.currentClamped !== null && (
          <span
            className={cn(
              "absolute top-1/2 h-3.5 w-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-surface",
              ahead ? "bg-profit" : "bg-loss"
            )}
            style={{ left: pct(g.currentClamped) }}
            data-testid="position-bar-current"
          />
        )}
      </div>
      <div className="relative h-8 text-[11px]">
        <span className="tabular absolute left-0 top-0 text-loss">{formatPrice(stop)}</span>
        <span className="tabular absolute right-0 top-0 text-profit">{formatPrice(target)}</span>
        <span className="tabular absolute top-0 -translate-x-1/2 text-muted" style={{ left: pct(g.entry) }}>
          entry {formatPrice(entry)}
        </span>
        {g.currentClamped !== null && (
          <span
            className="tabular absolute top-4 -translate-x-1/2 whitespace-nowrap font-medium"
            style={{ left: `clamp(48px, ${pct(g.currentClamped)}, calc(100% - 48px))` }}
          >
            now {formatPrice(current)}
            {g.rNow !== null && (
              <span className={cn("ml-1", ahead ? "text-profit" : "text-loss")}>
                {g.rNow >= 0 ? "+" : ""}
                {g.rNow.toFixed(2)}R
              </span>
            )}
          </span>
        )}
      </div>
      {(g.beyondStop || g.beyondTarget) && (
        <p className="text-[11px] text-warning">
          Price is past the {g.beyondStop ? "stop" : "target"}; the exit order fills on the next price.
        </p>
      )}
    </div>
  );
}
