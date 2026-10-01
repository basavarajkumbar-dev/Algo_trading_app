"use client";

import { useEffect, useRef, useState } from "react";
import type {
  IChartApi,
  IPriceLine,
  ISeriesApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
} from "lightweight-charts";

import { hslTripletToHex, istChartTime, levelGroup } from "@/lib/price-action-format";
import type { CandleResponse } from "@/types/market";
import type { PatternMarker, PriceLevel, RetracementResult } from "@/types/price-action";

interface Palette {
  background: string;
  text: string;
  grid: string;
  border: string;
  profit: string;
  loss: string;
  accent: string;
  muted: string;
}

function readPalette(): Palette {
  const style = getComputedStyle(document.documentElement);
  const v = (name: string, fallback: string) => hslTripletToHex(style.getPropertyValue(name), fallback);
  return {
    background: v("--surface", "#14171c"),
    text: v("--muted-foreground", "#8f98a3"),
    grid: v("--surface-alt", "#1c2027"),
    border: v("--border", "#2a2f38"),
    profit: v("--profit", "#2bab6f"),
    loss: v("--loss", "#e0525a"),
    accent: v("--accent", "#e0a33d"),
    muted: v("--muted-foreground", "#8f98a3"),
  };
}

function markerFor(marker: PatternMarker, palette: Palette): SeriesMarker<Time> {
  const time = istChartTime(marker.timestamp) as UTCTimestamp;
  const bullish = marker.direction === "BULLISH";
  const bearish = marker.direction === "BEARISH";
  const color = bullish ? palette.profit : bearish ? palette.loss : palette.muted;
  if (marker.pattern === "NR" || marker.pattern === "NRB") {
    return { time, position: "aboveBar", color, shape: "square", text: marker.pattern, size: 0.5 };
  }
  if (marker.pattern === "WRB") {
    return { time, position: bullish ? "belowBar" : "aboveBar", color, shape: "circle", text: "WRB", size: 0.6 };
  }
  return {
    time,
    position: bullish ? "belowBar" : "aboveBar",
    color,
    shape: bullish ? "arrowUp" : "arrowDown",
    text: marker.pattern,
  };
}

export interface ChartLayers {
  levels: boolean;
  retracement: boolean;
  patterns: boolean; // directional patterns: COG, WRB, BT, TT
  compression: boolean; // NR / NRB: frequent, so off by default
}

/** Candles visible when a symbol is first shown; scroll or zoom for more. */
const INITIAL_VISIBLE_CANDLES = 90;

/**
 * Candles with support/resistance levels, retracement levels and
 * pattern markers. The chart is created once per mount; data, lines
 * and markers are swapped in place on each refresh so the user's zoom
 * is kept, and the view is only re-fitted when the symbol changes.
 * Times are shown in IST.
 */
export function PriceChart({
  symbol,
  candles,
  levels,
  retracement,
  markers,
  layers,
  height = 420,
}: {
  symbol: string;
  candles: CandleResponse[];
  levels: PriceLevel[];
  retracement: RetracementResult | null;
  markers: PatternMarker[];
  layers: ChartLayers;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const seriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const linesRef = useRef<IPriceLine[]>([]);
  const paletteRef = useRef<Palette | null>(null);
  const fittedSymbolRef = useRef<string | null>(null);
  const [ready, setReady] = useState(false);
  const [failed, setFailed] = useState(false);

  // Create the chart once.
  useEffect(() => {
    let disposed = false;
    import("lightweight-charts")
      .then(({ createChart, ColorType, CrosshairMode }) => {
        if (disposed || !containerRef.current) return;
        const palette = readPalette();
        paletteRef.current = palette;
        const chart = createChart(containerRef.current, {
          autoSize: true,
          layout: {
            background: { type: ColorType.Solid, color: palette.background },
            textColor: palette.text,
            fontSize: 11,
          },
          grid: {
            vertLines: { color: palette.grid },
            horzLines: { color: palette.grid },
          },
          rightPriceScale: { borderColor: palette.border },
          timeScale: { borderColor: palette.border, timeVisible: true, secondsVisible: false },
          crosshair: { mode: CrosshairMode.Normal },
          // Fixed locale: the library otherwise uses the browser's, and some
          // environments report tags it rejects (e.g. "en-US@posix").
          localization: { locale: "en-IN" },
        });
        seriesRef.current = chart.addCandlestickSeries({
          upColor: palette.profit,
          downColor: palette.loss,
          wickUpColor: palette.profit,
          wickDownColor: palette.loss,
          borderVisible: false,
        });
        chartRef.current = chart;
        setReady(true);
      })
      .catch(() => setFailed(true));
    return () => {
      disposed = true;
      chartRef.current?.remove();
      chartRef.current = null;
      seriesRef.current = null;
      linesRef.current = [];
    };
  }, []);

  // Push data, lines and markers whenever they change.
  useEffect(() => {
    const series = seriesRef.current;
    const chart = chartRef.current;
    const palette = paletteRef.current;
    if (!ready || !series || !chart || !palette) return;

    series.setData(
      candles.map((c) => ({
        time: istChartTime(c.timestamp) as UTCTimestamp,
        open: c.open,
        high: c.high,
        low: c.low,
        close: c.close,
      }))
    );

    for (const line of linesRef.current) series.removePriceLine(line);
    linesRef.current = [];

    if (layers.levels) {
      for (const level of levels) {
        const group = levelGroup(level.type);
        linesRef.current.push(
          series.createPriceLine({
            price: level.price,
            color: level.role === "SUPPORT" ? palette.profit : palette.loss,
            lineWidth: level.strength >= 75 ? 2 : 1,
            // day levels solid, structure dashed, VWAP/averages dotted
            lineStyle: group === "day" ? 0 : group === "dynamic" ? 1 : 2,
            axisLabelVisible: true,
            title: `${level.label} · ${Math.round(level.strength)}`,
          })
        );
      }
    }

    if (layers.retracement && retracement?.detected) {
      for (const level of retracement.levels) {
        linesRef.current.push(
          series.createPriceLine({
            price: level.price,
            color: palette.accent,
            lineWidth: 1,
            lineStyle: 3, // large dashes
            axisLabelVisible: false,
            title: `${level.percent}%`,
          })
        );
      }
    }

    const firstTime = candles.length ? istChartTime(candles[0]!.timestamp) : 0;
    const compression = (m: PatternMarker) => m.pattern === "NR" || m.pattern === "NRB";
    series.setMarkers(
      markers
        .filter((m) => istChartTime(m.timestamp) >= firstTime)
        .filter((m) => (compression(m) ? layers.compression : layers.patterns))
        .map((m) => markerFor(m, palette))
        .sort((a, b) => (a.time as number) - (b.time as number))
    );

    if (fittedSymbolRef.current !== symbol && candles.length) {
      // Start on the recent candles so today's levels are readable; the
      // full history is a scroll or zoom away.
      chart.timeScale().setVisibleLogicalRange({
        from: Math.max(0, candles.length - INITIAL_VISIBLE_CANDLES),
        to: candles.length + 2,
      });
      fittedSymbolRef.current = symbol;
    }
  }, [ready, symbol, candles, levels, retracement, markers, layers]);

  if (failed) {
    return <p className="text-sm text-loss">The chart library could not be loaded.</p>;
  }
  return <div ref={containerRef} style={{ height }} className="w-full" aria-label={`${symbol} price chart`} />;
}
