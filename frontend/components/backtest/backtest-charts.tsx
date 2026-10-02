"use client";

import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { axisInr } from "@/components/analytics/charts";
import { formatInr } from "@/lib/trading-format";
import { useThemeColors, type ChartColors } from "@/lib/use-theme-colors";
import type { BacktestEquityPoint } from "@/types/backtest";

const IST_DAY = new Intl.DateTimeFormat("en-IN", { timeZone: "Asia/Kolkata", day: "2-digit", month: "short" });
const IST_DAY_TIME = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  day: "2-digit",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

function axisProps(c: ChartColors) {
  return {
    stroke: c.border,
    tick: { fill: c.muted, fontSize: 11 },
    tickLine: false,
    axisLine: { stroke: c.border },
  } as const;
}

interface Row {
  n: number;
  label: string;
  at: string | null;
  equity: number;
  drawdown: number;
  drawdown_percent: number;
  net_pnl: number | null;
}

/** Equity and drawdown rows; drawdown is recomputed so per-day series get one too. */
export function equityRows(points: BacktestEquityPoint[], start: number, perDay: boolean): Row[] {
  let peak = start;
  const rows: Row[] = [{ n: 0, label: "start", at: null, equity: start, drawdown: 0, drawdown_percent: 0, net_pnl: null }];
  points.forEach((p, i) => {
    peak = Math.max(peak, p.equity);
    const dd = p.equity - peak;
    rows.push({
      n: i + 1,
      label: perDay ? IST_DAY.format(new Date(p.at)) : `#${i + 1}`,
      at: p.at,
      equity: p.equity,
      drawdown: Math.round(dd * 100) / 100,
      drawdown_percent: peak > 0 ? (dd / peak) * 100 : 0,
      net_pnl: p.net_pnl,
    });
  });
  return rows;
}

function Box({ row, perDay }: { row: Row; perDay: boolean }) {
  return (
    <div className="rounded border border-border bg-surface-alt px-2.5 py-1.5 text-xs">
      <div className="text-muted">
        {row.at ? (perDay ? IST_DAY.format(new Date(row.at)) : `${row.label} · ${IST_DAY_TIME.format(new Date(row.at))} IST`) : "Starting capital"}
      </div>
      {row.net_pnl !== null && (
        <div className="flex justify-between gap-4">
          <span className="text-muted">Trade</span>
          <span className={`tabular ${row.net_pnl >= 0 ? "text-profit" : "text-loss"}`}>{formatInr(row.net_pnl, true)}</span>
        </div>
      )}
      <div className="flex justify-between gap-4">
        <span className="text-muted">Equity</span>
        <span className="tabular">{formatInr(row.equity)}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span className="text-muted">Drawdown</span>
        <span className={`tabular ${row.drawdown < 0 ? "text-loss" : ""}`}>
          {formatInr(row.drawdown)} ({row.drawdown_percent.toFixed(2)}%)
        </span>
      </div>
    </div>
  );
}

export function BacktestEquityChart({ rows, start, perDay }: { rows: Row[]; start: number; perDay: boolean }) {
  const c = useThemeColors();
  return (
    <ResponsiveContainer width="100%" height={240}>
      <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={c.border} strokeWidth={1} vertical={false} />
        <XAxis dataKey="label" {...axisProps(c)} interval="preserveStartEnd" minTickGap={24} />
        <YAxis {...axisProps(c)} width={76} tickFormatter={axisInr} domain={["auto", "auto"]} allowDecimals={false} />
        <ReferenceLine y={start} stroke={c.muted} strokeWidth={1} />
        <Tooltip
          cursor={{ stroke: c.muted, strokeWidth: 1 }}
          content={({ active, payload }) => {
            const r = payload?.[0]?.payload as Row | undefined;
            return active && r ? <Box row={r} perDay={perDay} /> : null;
          }}
        />
        <Area
          type="monotone"
          dataKey="equity"
          stroke={c.accent}
          strokeWidth={2}
          fill={c.accent}
          fillOpacity={0.1}
          dot={false}
          activeDot={{ r: 4, stroke: c.surface, strokeWidth: 2, fill: c.accent }}
          isAnimationActive={false}
        />
      </AreaChart>
    </ResponsiveContainer>
  );
}

export function BacktestDrawdownChart({ rows, perDay }: { rows: Row[]; perDay: boolean }) {
  const c = useThemeColors();
  return (
    <ResponsiveContainer width="100%" height={160}>
      <AreaChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 4 }}>
        <CartesianGrid stroke={c.border} strokeWidth={1} vertical={false} />
        <XAxis dataKey="label" {...axisProps(c)} interval="preserveStartEnd" minTickGap={24} />
        <YAxis {...axisProps(c)} width={76} tickFormatter={axisInr} domain={["auto", 0]} allowDecimals={false} />
        <Tooltip
          cursor={{ stroke: c.muted, strokeWidth: 1 }}
          content={({ active, payload }) => {
            const r = payload?.[0]?.payload as Row | undefined;
            return active && r ? <Box row={r} perDay={perDay} /> : null;
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
