"use client";

import { DataNoun } from "@/components/market/data-source-badge";
import { useEffect, useMemo, useState } from "react";

import { LevelsTable } from "@/components/price-action/levels-table";
import { MultiTimeframePanel } from "@/components/price-action/mtf-panel";
import { PatternGrid } from "@/components/price-action/pattern-grid";
import { PriceChart, type ChartLayers } from "@/components/price-action/price-chart";
import { RetracementPanel } from "@/components/price-action/retracement-panel";
import { SymbolList } from "@/components/price-action/symbol-list";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatIstTime, formatPrice } from "@/lib/intelligence-format";
import { getPriceActionOverviewRequest, getSymbolPriceActionRequest } from "@/lib/market-client";
import { selectChartLevels } from "@/lib/price-action-format";
import { cn } from "@/lib/utils";
import type { PriceActionOverview, SymbolPriceAction } from "@/types/price-action";

const POLL_MS = 15_000;
const DEFAULT_SYMBOL = "NIFTY50";
const CHART_LEVELS = 5;

function usePolling<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    function run() {
      load()
        .then((result) => {
          if (cancelled) return;
          setData(result);
          setError(null);
        })
        .catch((err: unknown) => {
          if (!cancelled) setError(err instanceof Error ? err.message : "Request failed");
        });
    }
    setData(null);
    run();
    const interval = setInterval(run, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}

export default function PriceActionPage() {
  const [symbol, setSymbol] = useState(DEFAULT_SYMBOL);
  const [layers, setLayers] = useState<ChartLayers>({
    levels: true,
    retracement: true,
    patterns: true,
    compression: false,
  });
  const overview = usePolling<PriceActionOverview>(getPriceActionOverviewRequest, []);
  const detail = usePolling<SymbolPriceAction>(() => getSymbolPriceActionRequest(symbol), [symbol]);

  const analysis = detail.data?.analysis ?? null;
  const chartLevels = useMemo(
    () => selectChartLevels(analysis?.support_resistance?.levels ?? [], CHART_LEVELS),
    [analysis]
  );
  const status = overview.data?.status;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">Price Action</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            Candle patterns, pullbacks, support/resistance and timeframe alignment on <DataNoun />
            NIFTY 50 data. These are contextual features, not trade signals; levels are reference
            zones, not guaranteed turning points.
          </p>
        </div>
        <div className="flex shrink-0 flex-col gap-1 text-[11px] text-muted md:items-end">
          <span>
            {status
              ? `Context ${status.context_timeframe} · setup ${status.setup_timeframe} · entry ${status.entry_timeframe}`
              : "—"}
          </span>
          <span>
            Closed candles · updated {formatIstTime(status?.last_refresh_at, true)}
            {status?.last_compute_ms != null ? ` · ${Math.round(status.last_compute_ms)} ms` : ""}
          </span>
          {status?.last_error && <span className="text-loss">{status.last_error}</span>}
        </div>
      </div>

      {overview.error && !overview.data && (
        <Card>
          <CardContent className="py-6 text-sm text-loss">
            Could not load price action: {overview.error}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 items-stretch gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <Card className="flex max-h-[560px] flex-col overflow-hidden">
          <CardHeader>
            <CardTitle>Symbols</CardTitle>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 p-0">
            {overview.data ? (
              <SymbolList rows={overview.data.symbols} selected={symbol} onSelect={setSymbol} />
            ) : (
              <p className="px-4 py-6 text-sm text-muted">Loading…</p>
            )}
          </CardContent>
        </Card>

        <Card className="min-w-0">
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
            <CardTitle>
              {symbol}
              {detail.data?.name && (
                <span className="ml-2 text-xs font-normal text-muted">{detail.data.name}</span>
              )}
              {analysis?.price != null && (
                <span className="tabular ml-3 text-sm font-normal">{formatPrice(analysis.price)}</span>
              )}
            </CardTitle>
            <div className="flex gap-1.5">
              {(
                [
                  ["levels", "Levels"],
                  ["retracement", "Retracement"],
                  ["patterns", "Patterns"],
                  ["compression", "NR / NRB"],
                ] as const
              ).map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setLayers((l) => ({ ...l, [key]: !l[key] }))}
                  aria-pressed={layers[key]}
                  className={cn(
                    "rounded-sm border px-2 py-0.5 text-xs",
                    layers[key]
                      ? "border-accent/40 bg-accent/10 text-foreground"
                      : "border-border text-muted hover:text-foreground"
                  )}
                >
                  {label}
                </button>
              ))}
            </div>
          </CardHeader>
          <CardContent>
            {detail.error && !detail.data && <p className="text-sm text-loss">{detail.error}</p>}
            {!detail.error && !detail.data && <p className="text-sm text-muted">Loading {symbol}…</p>}
            {detail.data && analysis && (
              <div className="flex flex-col gap-2">
                <PriceChart
                  symbol={symbol}
                  candles={detail.data.candles}
                  levels={chartLevels}
                  retracement={analysis.retracement}
                  markers={analysis.recent_patterns}
                  layers={layers}
                />
                <p className="text-[11px] text-muted">
                  {analysis.timeframe} candles in IST. Lines: the {CHART_LEVELS} strongest levels plus
                  the nearest support (green) and resistance (red); solid = day levels, dotted =
                  VWAP/averages, dashed = swings and ranges; amber = retracement levels. Markers
                  show COG, WRB, BT and TT on past candles (NR / NRB are frequent, so off by
                  default), judged without today&apos;s levels.
                </p>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {analysis?.multi_timeframe && (
        <Card>
          <CardHeader>
            <CardTitle>Multi-timeframe</CardTitle>
          </CardHeader>
          <CardContent>
            <MultiTimeframePanel result={analysis.multi_timeframe} />
          </CardContent>
        </Card>
      )}

      {analysis && (
        <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <Card>
            <CardHeader>
              <CardTitle>
                Patterns on the latest candle
                <span className="ml-2 text-xs font-normal text-muted">
                  {formatIstTime(analysis.data_as_of)}
                </span>
              </CardTitle>
            </CardHeader>
            <CardContent>
              <PatternGrid patterns={analysis.patterns} />
              <p className="mt-3 text-[11px] text-muted">{analysis.note}</p>
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Retracement</CardTitle>
            </CardHeader>
            <CardContent>
              <RetracementPanel result={analysis.retracement} />
            </CardContent>
          </Card>
        </div>
      )}

      {analysis?.support_resistance && (
        <Card>
          <CardHeader>
            <CardTitle>Support &amp; resistance</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <LevelsTable result={analysis.support_resistance} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}
