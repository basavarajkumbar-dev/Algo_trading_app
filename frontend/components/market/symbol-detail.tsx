"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { FactorList } from "@/components/market/factor-list";
import { RegimeExplainer } from "@/components/market/regime-explainer";
import {
  changeTone,
  formatIstTime,
  formatPercent,
  formatPrice,
  humanize,
} from "@/lib/intelligence-format";
import { getSymbolIntelligenceRequest } from "@/lib/market-client";
import { cn } from "@/lib/utils";
import type { SwingPoint, SymbolIntelligence } from "@/types/intelligence";

const REFRESH_MS = 10_000;
const SWINGS_SHOWN = 6;

/**
 * Full breakdown for one symbol: regime explanation, factor scores
 * and the raw engine numbers behind them. Refreshes on its own every
 * few seconds; analysis only changes when a 5m candle closes, so this
 * is deliberately unhurried.
 */
export function SymbolDetail({ symbol }: { symbol: string }) {
  const [data, setData] = useState<SymbolIntelligence | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setData(null);
    setError(null);

    function load() {
      getSymbolIntelligenceRequest(symbol)
        .then((result) => {
          if (cancelled) return;
          setData(result);
          setError(null);
        })
        .catch((err: unknown) => {
          if (cancelled) return;
          setError(err instanceof Error ? err.message : "Could not load analysis.");
        });
    }

    load();
    const interval = setInterval(load, REFRESH_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [symbol]);

  if (error && !data) return <p className="text-sm text-loss">{error}</p>;
  if (!data) return <p className="text-sm text-muted">Loading {symbol}…</p>;

  const a = data.analysis;
  const unavailable = Object.entries(a.unavailable);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-baseline justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold">{data.symbol}</h3>
          {data.name && <p className="text-xs text-muted">{data.name}</p>}
        </div>
        <div className="flex flex-col items-end">
          <span className="tabular text-xl font-semibold">{formatPrice(data.price)}</span>
          <span className={cn("tabular text-xs", changeTone(data.change_percent))}>
            {formatPercent(data.change_percent)}
          </span>
        </div>
      </div>

      <RegimeExplainer result={data.regime} />

      <Section title="Factor scores">
        <FactorList factors={data.factors} />
      </Section>

      <Section title="Engine readings">
        <div className="grid grid-cols-2 gap-x-6 gap-y-3 text-sm">
          {a.vwap && (
            <Group label="VWAP">
              <Row k="VWAP" v={formatPrice(a.vwap.vwap)} />
              <Row k="Price vs VWAP" v={humanize(a.vwap.price_vs_vwap)} />
              <Row k="Distance" v={formatPercent(a.vwap.distance_from_vwap_percent)} />
              <Row k="Slope / candle" v={formatPercent(a.vwap.vwap_slope_percent, 3)} />
              <Row k="Recent crossings" v={String(a.vwap.recent_crossings)} />
            </Group>
          )}
          {a.moving_averages && (
            <Group label="Moving averages">
              <Row k={`EMA ${a.moving_averages.ema_fast_period}`} v={formatPrice(a.moving_averages.ema_fast)} />
              <Row k={`EMA ${a.moving_averages.ema_slow_period}`} v={formatPrice(a.moving_averages.ema_slow)} />
              <Row k={`MA ${a.moving_averages.ma_period}`} v={formatPrice(a.moving_averages.ma)} />
              <Row k="Alignment" v={humanize(a.moving_averages.alignment)} />
              <Row k="Separation" v={formatPercent(a.moving_averages.separation_percent, 3)} />
              <Row k="Fast EMA slope" v={formatPercent(a.moving_averages.ema_fast_slope_percent, 3)} />
            </Group>
          )}
          {a.volatility && (
            <Group label="Volatility">
              <Row k="ATR" v={`${formatPrice(a.volatility.atr)} (${a.volatility.atr_percent.toFixed(2)}%)`} />
              <Row k="ATR vs baseline" v={`${a.volatility.atr_ratio.toFixed(2)}×`} />
              <Row k="Level" v={humanize(a.volatility.volatility_level)} />
              <Row k="Expanding" v={a.volatility.is_expanding ? "Yes" : "No"} />
              <Row k="Wick share" v={`${Math.round(a.volatility.average_wick_ratio * 100)}%`} />
              <Row
                k="Reversals"
                v={`${a.volatility.direction_changes} in ${a.volatility.character_window}`}
              />
            </Group>
          )}
          {a.volume && (
            <Group label="Volume">
              <Row k="Relative volume" v={`${a.volume.relative_volume.toFixed(2)}×`} />
              <Row k="Spike" v={a.volume.is_spike ? "Yes" : "No"} />
              <Row k="Trend" v={humanize(a.volume.volume_trend)} />
            </Group>
          )}
          {a.momentum && (
            <Group label="Momentum">
              <Row k="Direction" v={humanize(a.momentum.direction)} />
              {Object.entries(a.momentum.rate_of_change_percent).map(([period, roc]) => (
                <Row key={period} k={`ROC ${period}`} v={formatPercent(roc, 3)} />
              ))}
            </Group>
          )}
          {a.range && (
            <Group label="Range">
              <Row k="High / low" v={`${formatPrice(a.range.range_high)} / ${formatPrice(a.range.range_low)}`} />
              <Row k="Compression" v={`${a.range.compression_ratio.toFixed(2)}×`} />
              <Row k="Efficiency" v={a.range.efficiency_ratio.toFixed(2)} />
              <Row k="Breakout" v={humanize(a.range.breakout_direction)} />
            </Group>
          )}
          {a.relative_strength && (
            <Group label={`Relative strength (${a.relative_strength.lookback} candles)`}>
              <Row k="Symbol return" v={formatPercent(a.relative_strength.symbol_return_percent)} />
              <Row k="Benchmark" v={formatPercent(a.relative_strength.benchmark_return_percent)} />
              <Row k="Relative" v={formatPercent(a.relative_strength.relative_return_percent)} />
            </Group>
          )}
        </div>
      </Section>

      {a.structure && (
        <Section title="Market structure">
          <div className="flex flex-col gap-3 text-sm">
            <div className="grid grid-cols-2 gap-x-6 gap-y-1">
              <Row k="Structure trend" v={humanize(a.structure.trend)} />
              <Row k="State" v={humanize(a.structure.structure_state)} />
              <Row k="Last event" v={humanize(a.structure.last_event)} />
              <Row k="Swing H / L" v={`${formatPrice(a.structure.latest_swing_high)} / ${formatPrice(a.structure.latest_swing_low)}`} />
            </div>
            <SwingList swings={a.structure.swings.slice(-SWINGS_SHOWN).reverse()} />
          </div>
        </Section>
      )}

      {unavailable.length > 0 && (
        <Section title="Not enough data">
          <ul className="flex flex-col gap-1 text-xs text-muted">
            {unavailable.map(([engine, reason]) => (
              <li key={engine}>
                <span className="text-foreground">{humanize(engine)}:</span> {reason}
              </li>
            ))}
          </ul>
        </Section>
      )}

      <p className="text-[11px] text-muted">
        {a.candles_analyzed} closed {a.timeframe} candles · last candle {formatIstTime(a.data_as_of)}
      </p>
    </div>
  );
}

const SWING_LABEL_SHORT: Record<NonNullable<SwingPoint["label"]>, string> = {
  HIGHER_HIGH: "HH",
  HIGHER_LOW: "HL",
  LOWER_HIGH: "LH",
  LOWER_LOW: "LL",
};

function SwingList({ swings }: { swings: SwingPoint[] }) {
  if (swings.length === 0) return <p className="text-xs text-muted">No confirmed swings yet.</p>;
  return (
    <table className="w-full text-xs">
      <thead>
        <tr className="text-left text-muted">
          <th className="py-1 font-medium">Swing</th>
          <th className="py-1 font-medium">Label</th>
          <th className="py-1 text-right font-medium">Price</th>
          <th className="py-1 text-right font-medium">Time</th>
        </tr>
      </thead>
      <tbody>
        {swings.map((s) => {
          const bullish = s.label === "HIGHER_HIGH" || s.label === "HIGHER_LOW";
          const bearish = s.label === "LOWER_HIGH" || s.label === "LOWER_LOW";
          return (
            <tr key={`${s.type}-${s.timestamp}`} className="border-t border-border">
              <td className="py-1">{s.type === "HIGH" ? "High" : "Low"}</td>
              <td className={cn("py-1", bullish && "text-profit", bearish && "text-loss", !s.label && "text-muted")}>
                {s.label ? SWING_LABEL_SHORT[s.label] : "—"}
              </td>
              <td className="tabular py-1 text-right">{formatPrice(s.price)}</td>
              <td className="tabular py-1 text-right text-muted">{formatIstTime(s.timestamp)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-border pt-4">
      <h4 className="text-xs font-medium uppercase tracking-wide text-muted">{title}</h4>
      {children}
    </section>
  );
}

function Group({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs font-medium text-foreground">{label}</span>
      {children}
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex justify-between gap-3 text-xs">
      <span className="text-muted">{k}</span>
      <span className="tabular text-right">{v}</span>
    </div>
  );
}
