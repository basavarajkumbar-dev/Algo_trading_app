import { Fragment } from "react";

import { formatPrice, formatScore } from "@/lib/intelligence-format";
import { cn } from "@/lib/utils";
import type { PriceLevel, SupportResistanceResult } from "@/types/price-action";

const COMPONENT_ORDER = ["type", "touches", "confluence", "recency", "timeframe"];

function StrengthBar({ level }: { level: PriceLevel }) {
  const title = COMPONENT_ORDER.filter((k) => level.components[k] !== undefined)
    .map((k) => `${k}: ${level.components[k]}`)
    .join(" · ");
  return (
    <div className="flex items-center gap-2" title={title}>
      <div className="h-1 w-16 overflow-hidden rounded-sm bg-surface-alt">
        <div
          className={cn("h-full", level.role === "SUPPORT" ? "bg-profit" : "bg-loss")}
          style={{ width: `${level.strength}%` }}
        />
      </div>
      <span className="tabular w-6 text-right">{formatScore(level.strength)}</span>
    </div>
  );
}

/**
 * Every support/resistance level, highest first, with a marker row at
 * the current price. Hover a strength bar to see its score components.
 */
export function LevelsTable({ result }: { result: SupportResistanceResult }) {
  const firstSupport = result.levels.findIndex((l) => l.role === "SUPPORT");
  const priceRowAt = firstSupport === -1 ? result.levels.length : firstSupport;

  return (
    <div className="flex flex-col">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-muted">
              <th className="px-3 py-2 font-medium">Level</th>
              <th className="px-3 py-2 text-right font-medium">Price</th>
              <th className="px-3 py-2 text-right font-medium">Distance</th>
              <th className="px-3 py-2 font-medium">Strength</th>
              <th className="px-3 py-2 text-right font-medium">Touches</th>
              <th className="px-3 py-2 font-medium">TF</th>
              <th className="px-3 py-2 text-right font-medium">Last touch</th>
              <th className="px-3 py-2 font-medium">Confluence</th>
            </tr>
          </thead>
          <tbody>
            {result.levels.map((level, i) => (
              <Fragment key={`${level.label}-${level.price}-${i}`}>
                {i === priceRowAt && <PriceRow price={result.price} />}
                <tr className="border-b border-border last:border-0">
                  <td className="px-3 py-1.5">
                    <span className={level.role === "SUPPORT" ? "text-profit" : "text-loss"}>●</span>{" "}
                    {level.label}
                    {level.dynamic && <span className="ml-1 text-[10px] text-muted">moving</span>}
                  </td>
                  <td className="tabular px-3 py-1.5 text-right">{formatPrice(level.price)}</td>
                  <td className="tabular px-3 py-1.5 text-right text-muted">
                    {level.distance_atr > 0 ? "+" : ""}
                    {level.distance_atr.toFixed(1)} ATR
                  </td>
                  <td className="px-3 py-1.5">
                    <StrengthBar level={level} />
                  </td>
                  <td className="tabular px-3 py-1.5 text-right">{level.touch_count}</td>
                  <td className="px-3 py-1.5 text-muted">{level.timeframe}</td>
                  <td className="tabular px-3 py-1.5 text-right text-muted">
                    {level.recency === null ? "—" : level.recency === 0 ? "now" : `${level.recency} bars`}
                  </td>
                  <td className="px-3 py-1.5 text-muted">{level.confluence.join(", ") || "—"}</td>
                </tr>
              </Fragment>
            ))}
            {priceRowAt === result.levels.length && <PriceRow price={result.price} />}
          </tbody>
        </table>
      </div>
      <div className="flex flex-col gap-1 border-t border-border px-3 py-2 text-[11px] text-muted">
        <span>
          ATR {formatPrice(result.atr)} · day {result.session_date ?? "—"} (previous{" "}
          {result.previous_session_date ?? "unavailable"}, IST calendar days). {result.note}
        </span>
        {Object.entries(result.unavailable).map(([key, reason]) => (
          <span key={key}>· {reason}</span>
        ))}
      </div>
    </div>
  );
}

function PriceRow({ price }: { price: number }) {
  return (
    <tr className="bg-surface-alt">
      <td className="px-3 py-1 text-[11px] font-medium" colSpan={2}>
        Price <span className="tabular ml-2">{formatPrice(price)}</span>
      </td>
      <td colSpan={6} />
    </tr>
  );
}
