import { Check, Minus } from "lucide-react";

import { RegimeBadge } from "@/components/market/regime-badge";
import { formatPrice, formatScore, humanize } from "@/lib/intelligence-format";
import {
  SETUP_LABEL,
  ZONE_META,
  biasLabel,
  biasPosition,
  directionLabel,
  directionTone,
} from "@/lib/price-action-format";
import { cn } from "@/lib/utils";
import type { MultiTimeframeResult, TimeframeAnalysis } from "@/types/price-action";

const ROLE_TITLE = { CONTEXT: "Context", SETUP: "Setup", ENTRY: "Entry" } as const;
const ROLE_FOCUS = {
  CONTEXT: "Major trend, structure, key levels",
  SETUP: "Pullback, structure, nearby levels",
  ENTRY: "Candle, momentum and volume confirmation",
} as const;

function BiasBar({ bias }: { bias: number }) {
  const position = biasPosition(bias);
  return (
    <div className="relative h-1.5 w-full overflow-hidden rounded-sm bg-surface-alt">
      <div
        className={cn("absolute inset-y-0", bias >= 0 ? "bg-profit" : "bg-loss")}
        style={bias >= 0 ? { left: "50%", width: `${position - 50}%` } : { left: `${position}%`, width: `${50 - position}%` }}
      />
      <div className="absolute inset-y-0 left-1/2 w-px bg-border" aria-hidden />
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex justify-between gap-3 text-xs">
      <span className="text-muted">{k}</span>
      <span className="tabular text-right">{v}</span>
    </div>
  );
}

function Check3({ ok, label }: { ok: boolean; label: string }) {
  return (
    <li className={cn("flex items-center gap-1.5 text-xs", !ok && "text-muted")}>
      {ok ? <Check className="h-3 w-3 text-profit" aria-hidden /> : <Minus className="h-3 w-3" aria-hidden />}
      {label}
    </li>
  );
}

function TimeframeColumn({ tf }: { tf: TimeframeAnalysis }) {
  return (
    <div className="flex flex-col gap-3 rounded border border-border p-3">
      <div className="flex items-baseline justify-between">
        <span className="text-sm font-medium">
          {ROLE_TITLE[tf.role]} <span className="text-muted">· {tf.timeframe}</span>
        </span>
        {tf.available && (
          <span className={cn("text-xs", tf.bias >= 20 ? "text-profit" : tf.bias <= -20 ? "text-loss" : "text-muted")}>
            {biasLabel(tf.bias)} {tf.bias > 0 ? "+" : ""}
            {Math.round(tf.bias)}
          </span>
        )}
      </div>
      <span className="-mt-2 text-[11px] text-muted">{ROLE_FOCUS[tf.role]}</span>

      {!tf.available ? (
        <p className="text-xs text-muted">{tf.unavailable_reason}</p>
      ) : (
        <>
          <BiasBar bias={tf.bias} />
          <div className="flex flex-col gap-1">
            {tf.regime && (
              <Row k="Regime" v={<RegimeBadge regime={tf.regime} short className="text-[10px]" />} />
            )}
            <Row k="Structure" v={`${humanize(tf.structure_trend)} · ${humanize(tf.structure_state)}`} />
            <Row k="Trend quality" v={formatScore(tf.trend_quality)} />
            <Row k="Momentum" v={formatScore(tf.momentum_score)} />
            <Row k="MA alignment" v={humanize(tf.ma_alignment)} />
          </div>

          {tf.role === "CONTEXT" && tf.key_levels.length > 0 && (
            <div className="flex flex-col gap-1 border-t border-border pt-2">
              <span className="text-[11px] text-muted">Key levels</span>
              {tf.key_levels.map((level) => (
                <Row
                  key={`${level.label}-${level.price}`}
                  k={level.label}
                  v={
                    <span>
                      {formatPrice(level.price)}{" "}
                      <span className="text-muted">({Math.round(level.strength)})</span>
                    </span>
                  }
                />
              ))}
            </div>
          )}

          {tf.role === "SETUP" && (
            <div className="flex flex-col gap-1 border-t border-border pt-2">
              {tf.setup_state && <Row k="Setup" v={SETUP_LABEL[tf.setup_state]} />}
              {tf.retracement?.detected && tf.retracement.zone && (
                <Row
                  k="Pullback"
                  v={`${Math.round(tf.retracement.retracement_percentage ?? 0)}% · ${ZONE_META[tf.retracement.zone].label}`}
                />
              )}
              {tf.nearest_support && (
                <Row k="Support" v={`${tf.nearest_support.label} ${formatPrice(tf.nearest_support.price)}`} />
              )}
              {tf.nearest_resistance && (
                <Row
                  k="Resistance"
                  v={`${tf.nearest_resistance.label} ${formatPrice(tf.nearest_resistance.price)}`}
                />
              )}
            </div>
          )}

          {tf.role === "ENTRY" && tf.confirmation && (
            <div className="flex flex-col gap-1.5 border-t border-border pt-2">
              <span className="text-[11px] text-muted">
                Agreement with the {directionLabel(tf.confirmation.reference_bias).toLowerCase()} higher-timeframe bias
              </span>
              <ul className="flex flex-col gap-1">
                <Check3
                  ok={tf.confirmation.candle_confirmation}
                  label={`Candle pattern${tf.confirmation.aligned_patterns.length ? `: ${tf.confirmation.aligned_patterns.join(", ")}` : ""}`}
                />
                <Check3 ok={tf.confirmation.momentum_returning} label="Momentum in the bias direction" />
                <Check3
                  ok={tf.confirmation.volume_confirmed}
                  label={`Volume${tf.relative_volume !== null ? ` ${tf.relative_volume.toFixed(1)}×` : ""}`}
                />
              </ul>
              {tf.confirmation.opposing_patterns.length > 0 && (
                <span className="text-[11px] text-loss">
                  Opposing: {tf.confirmation.opposing_patterns.join(", ")}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** Context / setup / entry timeframes with the overall bias and alignment. */
export function MultiTimeframePanel({ result }: { result: MultiTimeframeResult }) {
  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-[1fr_1fr_2fr]">
        <div className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wide text-muted">Overall bias</span>
          <span className={cn("text-lg font-semibold", directionTone(result.overall_bias))}>
            {directionLabel(result.overall_bias)}{" "}
            <span className="tabular text-sm font-normal text-muted">
              {result.bias_score > 0 ? "+" : ""}
              {Math.round(result.bias_score)}
            </span>
          </span>
          <BiasBar bias={result.bias_score} />
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-[11px] uppercase tracking-wide text-muted">Alignment</span>
          <span className="tabular text-lg font-semibold">
            {Math.round(result.alignment_score)}
            <span className="text-sm font-normal text-muted">/100</span>
          </span>
          <div className="h-1.5 w-full overflow-hidden rounded-sm bg-surface-alt">
            <div className="h-full bg-accent" style={{ width: `${result.alignment_score}%` }} />
          </div>
        </div>
        <div className="flex flex-col gap-1 text-xs">
          {result.conflicts.length > 0 ? (
            result.conflicts.map((c) => (
              <span key={c} className="text-warning">
                ⚠ {c}
              </span>
            ))
          ) : (
            <span className="text-muted">No timeframe opposes the higher-timeframe direction.</span>
          )}
          <span className="text-[11px] text-muted">{result.note}</span>
        </div>
      </div>
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-3">
        <TimeframeColumn tf={result.context_analysis} />
        <TimeframeColumn tf={result.setup_analysis} />
        <TimeframeColumn tf={result.entry_analysis} />
      </div>
    </div>
  );
}
