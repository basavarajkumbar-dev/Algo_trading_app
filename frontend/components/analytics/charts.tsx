"use client";

import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { formatIstTime } from "@/lib/intelligence-format";
import { formatInr } from "@/lib/trading-format";
import { useThemeColors, type ChartColors } from "@/lib/use-theme-colors";
import type { DailyPnl, EquityPoint } from "@/types/dashboard";

const inrAxis = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
/** Axis ticks: whole rupees with Indian grouping (₹1,00,000 / ₹-250). */
export const axisInr = (v: number) => `${v < 0 ? "−" : ""}₹${inrAxis.format(Math.abs(v))}`;

/** Bars must grow from zero, so the value axis always includes it. */
export const ZERO_DOMAIN: [(min: number) => number, (max: number) => number] = [
  (min) => Math.min(0, min),
  (max) => Math.max(0, max),
];

function axisProps(c: ChartColors) {
  return {
    stroke: c.border,
    tick: { fill: c.muted, fontSize: 11 },
    tickLine: false,
    axisLine: { stroke: c.border },
  } as const;
}

function TooltipBox({ lines }: { lines: [string, string, string?][] }) {
  return (
    <div className="rounded border border-border bg-surface-alt px-2.5 py-1.5 text-xs shadow-none">
      {lines.map(([label, value, tone], i) => (
        <div key={i} className="flex justify-between gap-4">
          <span className="text-muted">{label}</span>
          <span className={`tabular ${tone ?? ""}`}>{value}</span>
        </div>
      ))}
    </div>
  );
}

interface Indexed extends EquityPoint {
  n: number;
}

function withIndex(points: EquityPoint[]): Indexed[] {
  return points.map((p, i) => ({ ...p, n: i + 1 }));
}

/** Equity after each closed trade (one series: no legend; the title names it). */
export function EquityChart({ points, start }: { points: EquityPoint[]; start: number }) {
  const c = useThemeColors();
  const data = [{ n: 0, equity: start, drawdown: 0 } as unknown as Indexed, ...withIndex(points)];
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={c.border} strokeWidth={1} vertical={false} />
        <XAxis dataKey="n" {...axisProps(c)} label={{ value: "trade #", position: "insideBottomRight", fill: c.muted, fontSize: 10, dy: 6 }} />
        <YAxis {...axisProps(c)} width={72} tickFormatter={axisInr} domain={["auto", "auto"]} allowDecimals={false} />
        <ReferenceLine y={start} stroke={c.muted} strokeWidth={1} />
        <Tooltip
          cursor={{ stroke: c.muted, strokeWidth: 1 }}
          content={({ active, payload }) => {
            const p = payload?.[0]?.payload as Indexed | undefined;
            if (!active || !p) return null;
            if (!p.trade_id) return <TooltipBox lines={[["Starting capital", formatInr(start)]]} />;
            return (
              <TooltipBox
                lines={[
                  [`#${p.n} ${p.symbol}`, formatIstTime(p.closed_at, true)],
                  ["Trade", formatInr(p.net_pnl, true), p.net_pnl >= 0 ? "text-profit" : "text-loss"],
                  ["Equity", formatInr(p.equity)],
                ]}
              />
            );
          }}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke={c.accent}
          strokeWidth={2}
          fill={c.accent}
          fillOpacity={0.1}
          activeDot={{ r: 4, stroke: c.surface, strokeWidth: 2, fill: c.accent }}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

/** Distance below the running equity peak, after each trade. */
export function DrawdownChart({ points }: { points: EquityPoint[] }) {
  const c = useThemeColors();
  const data = [{ n: 0, drawdown: 0, drawdown_percent: 0 } as unknown as Indexed, ...withIndex(points)];
  return (
    <ResponsiveContainer width="100%" height={180}>
      <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={c.border} strokeWidth={1} vertical={false} />
        <XAxis dataKey="n" {...axisProps(c)} />
        <YAxis {...axisProps(c)} width={72} tickFormatter={axisInr} domain={["auto", 0]} allowDecimals={false} />
        <Tooltip
          cursor={{ stroke: c.muted, strokeWidth: 1 }}
          content={({ active, payload }) => {
            const p = payload?.[0]?.payload as Indexed | undefined;
            if (!active || !p) return null;
            return (
              <TooltipBox
                lines={[
                  [p.trade_id ? `#${p.n} ${p.symbol}` : "Start", p.closed_at ? formatIstTime(p.closed_at, true) : ""],
                  ["Drawdown", formatInr(p.drawdown), p.drawdown < 0 ? "text-loss" : ""],
                  ["From peak", `${(p.drawdown_percent ?? 0).toFixed(2)}%`],
                ]}
              />
            );
          }}
        />
        <Area
          type="stepAfter"
          dataKey="drawdown"
          stroke={c.loss}
          strokeWidth={2}
          fill={c.loss}
          fillOpacity={0.1}
          dot={false}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

interface BarDatum {
  label: string;
  value: number;
  detail: string;
}

/** Net P&L per trade or per day; colour = sign (profit / loss). */
export function PnlBars({ data }: { data: BarDatum[] }) {
  const c = useThemeColors();
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 4 }} barCategoryGap={2}>
        <CartesianGrid stroke={c.border} strokeWidth={1} vertical={false} />
        <XAxis dataKey="label" {...axisProps(c)} interval="preserveStartEnd" minTickGap={16} />
        <YAxis {...axisProps(c)} width={72} tickFormatter={axisInr} domain={ZERO_DOMAIN} allowDecimals={false} />
        <ReferenceLine y={0} stroke={c.muted} strokeWidth={1} />
        <Tooltip
          cursor={{ fill: c.border, fillOpacity: 0.4 }}
          content={({ active, payload }) => {
            const p = payload?.[0]?.payload as BarDatum | undefined;
            if (!active || !p) return null;
            return (
              <TooltipBox
                lines={[
                  [p.label, p.detail],
                  ["Net P&L", formatInr(p.value, true), p.value >= 0 ? "text-profit" : "text-loss"],
                ]}
              />
            );
          }}
        />
        <Bar dataKey="value" maxBarSize={24} isAnimationActive={false}>
          {data.map((d, i) => (
            <Cell
              key={i}
              fill={d.value >= 0 ? c.profit : c.loss}
              // rounded data end: top for gains, bottom for losses
              // @ts-expect-error recharts Cell forwards radius to the bar shape
              radius={d.value >= 0 ? [4, 4, 0, 0] : [0, 0, 4, 4]}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

export function tradeBars(points: EquityPoint[]): BarDatum[] {
  return points.map((p, i) => ({
    label: `#${i + 1}`,
    value: p.net_pnl,
    detail: `${p.symbol} · ${formatIstTime(p.closed_at, true)}`,
  }));
}

export function dayBars(days: DailyPnl[]): BarDatum[] {
  return days.map((d) => ({
    label: d.date.slice(5),
    value: d.net_pnl,
    detail: `${d.date} · ${d.trades} trade(s), ${d.wins}W/${d.losses}L`,
  }));
}
