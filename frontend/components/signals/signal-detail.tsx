import type { ReactNode } from "react";

import { RegimeBadge } from "@/components/market/regime-badge";
import { ReasonBreakdown } from "@/components/signals/reason-breakdown";
import { ScoreBreakdown } from "@/components/signals/score-breakdown";
import { Badge } from "@/components/ui/badge";
import { formatIstTime, formatPrice, formatScore } from "@/lib/intelligence-format";
import { CATEGORY_VARIANT, formatTurnover, sideTone } from "@/lib/signal-format";
import { STRATEGY_LABEL, percentFromEntry } from "@/lib/strategy-format";
import { cn } from "@/lib/utils";
import type { StoredSignal, TradingSignal } from "@/types/signals";

function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[10px] uppercase tracking-wide text-muted">{label}</span>
      <span className="tabular text-sm">{children}</span>
      {hint && <span className="text-[10px] text-muted">{hint}</span>}
    </div>
  );
}

/**
 * A signal's object fields, score breakdown and reasons. `record` adds
 * the stored history; without it the signal is shown as produced by the
 * current scan.
 */
export function SignalDetail({
  signal: s,
  record,
}: {
  signal: TradingSignal;
  record?: Omit<StoredSignal, "signal">;
}) {
  const side = s.direction === "SELL" ? "SHORT" : "LONG";
  const stopMove = percentFromEntry(s.entry, s.stop_loss, side);
  const targetMove = percentFromEntry(s.entry, s.target, side);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <div className="flex items-center gap-2">
            <span className="text-lg font-semibold">{s.symbol}</span>
            <span className={cn("text-sm font-semibold", sideTone(s.direction))}>{s.direction}</span>
            <Badge variant={CATEGORY_VARIANT[s.category]}>{s.category}</Badge>
            {s.executable && <Badge variant="accent">READY</Badge>}
            {(record || s.id) && (
              <Badge variant={s.status === "ACTIVE" ? "connected" : "neutral"}>{s.status}</Badge>
            )}
          </div>
          <span className="text-xs text-muted">
            {STRATEGY_LABEL[s.strategy]} · version <code className="tabular">{s.strategy_version}</code> ·{" "}
            {s.timeframe} candle {formatIstTime(s.candle_time)}
          </span>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted">
          <RegimeBadge regime={s.market_regime} />
          <span>{formatScore(s.regime_confidence)}% regime confidence</span>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
        <Field label="Entry">{formatPrice(s.entry)}</Field>
        <Field label="Stop loss" hint={`${stopMove.toFixed(2)}%`}>
          {formatPrice(s.stop_loss)}
        </Field>
        <Field label="Target" hint={`+${targetMove.toFixed(2)}%`}>
          {formatPrice(s.target)}
        </Field>
        <Field label="Reward : risk">{s.risk_reward.toFixed(2)}</Field>
        <Field label="Quantity candidate" hint="not a position size">
          {s.quantity_candidate}
        </Field>
        <Field label="Score">{formatScore(s.score)} / 100</Field>
        <Field label="Strategy confidence">{formatScore(s.confidence)}</Field>
        <Field label="Relative strength">{formatScore(s.relative_strength)}</Field>
        <Field label="Trend quality">{formatScore(s.trend_quality)}</Field>
        <Field label="Relative volume">{s.relative_volume?.toFixed(2) ?? "—"}×</Field>
        <Field label="Liquidity / candle">{formatTurnover(s.liquidity)}</Field>
        <Field label="Generated">{formatIstTime(s.timestamp, true)}</Field>
      </div>

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <section className="flex flex-col gap-3">
          <h3 className="text-[11px] uppercase tracking-wide text-muted">Score breakdown</h3>
          <ScoreBreakdown breakdown={s.breakdown} />
        </section>
        <section className="flex flex-col gap-3">
          <h3 className="text-[11px] uppercase tracking-wide text-muted">Reason breakdown</h3>
          <ReasonBreakdown reasons={s.reasons} checks={s.strategy_checks} />
        </section>
      </div>

      <div className="flex flex-col gap-1 border-t border-border pt-3 text-[11px] text-muted">
        <span>{s.quantity_note}</span>
        {record ? (
          <span>
            Seen in {record.times_seen} scan(s), last {formatIstTime(record.last_seen_at, true)}
            {record.last_rank ? `, rank ${record.last_rank}` : ""}, latest score {formatScore(record.last_score)}.
            {record.expired_at ? ` Expired ${formatIstTime(record.expired_at, true)}.` : ""} Values are as
            generated and are not rewritten.
          </span>
        ) : (
          <span>
            From the current scan.{" "}
            {s.id ? "Stored as a signal." : "Not stored (below the storage score, or storage unavailable)."}
          </span>
        )}
        <span>{s.note}</span>
      </div>
    </div>
  );
}
