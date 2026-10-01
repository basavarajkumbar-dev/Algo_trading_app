/**
 * Pure presentation helpers for the Price Action page. Kept free of
 * React so they can be unit-tested directly.
 */

import type {
  LevelType,
  PatternDirection,
  PatternType,
  PriceLevel,
  RetracementZone,
  SetupState,
} from "@/types/price-action";

export const PATTERN_META: Record<PatternType, { name: string; meaning: string }> = {
  COG: {
    name: "Changing of the Guard",
    meaning: "After a run of candles one way, a candle closed beyond the previous candle's extreme the other way.",
  },
  NR: {
    name: "Narrow Range",
    meaning: "Narrowest high-low range of recent candles: a pause in volatility, no direction.",
  },
  NRB: {
    name: "Narrow Range Body",
    meaning: "Small real body relative to its range and to recent bodies: indecision.",
  },
  WRB: {
    name: "Wide Range Body",
    meaning: "Body much larger than recent bodies, closing near its extreme: one side in control.",
  },
  BT: {
    name: "Bottoming Tail",
    meaning: "Long lower wick with a close in the upper part: lower prices were rejected.",
  },
  TT: {
    name: "Topping Tail",
    meaning: "Long upper wick with a close in the lower part: higher prices were rejected.",
  },
};

export const PATTERN_ORDER: PatternType[] = ["COG", "WRB", "BT", "TT", "NR", "NRB"];

export function directionTone(direction: PatternDirection | null | undefined): string {
  if (direction === "BULLISH") return "text-profit";
  if (direction === "BEARISH") return "text-loss";
  return "text-muted";
}

export function directionLabel(direction: PatternDirection | null | undefined): string {
  if (direction === "BULLISH") return "Bullish";
  if (direction === "BEARISH") return "Bearish";
  return "Neutral";
}

export interface ZoneMeta {
  label: string;
  range: string;
  from: number;
  to: number;
  /** Tailwind background class for the gauge band. */
  band: string;
}

/** Bands match backend RetracementConfig.zone_bounds defaults. */
export const ZONE_META: Record<RetracementZone, ZoneMeta> = {
  SHALLOW: { label: "Shallow", range: "< 40%", from: 0, to: 40, band: "bg-profit/40" },
  HEALTHY_PULLBACK: { label: "Healthy pullback", range: "40–50%", from: 40, to: 50, band: "bg-profit/25" },
  DEEP_PULLBACK: { label: "Deep pullback", range: "50–60%", from: 50, to: 60, band: "bg-warning/30" },
  WEAKENING: { label: "Weakening", range: "60–80%", from: 60, to: 80, band: "bg-warning/50" },
  MAJOR_WEAKNESS: { label: "Major weakness", range: "80–100%", from: 80, to: 100, band: "bg-loss/40" },
  INVALIDATION_CONTEXT: { label: "Invalidation context", range: "≥ 100%", from: 100, to: 120, band: "bg-loss/70" },
};

export const ZONE_ORDER: RetracementZone[] = [
  "SHALLOW",
  "HEALTHY_PULLBACK",
  "DEEP_PULLBACK",
  "WEAKENING",
  "MAJOR_WEAKNESS",
  "INVALIDATION_CONTEXT",
];

/** The gauge spans 0-120%; anything deeper pins to the end. */
export const GAUGE_MAX = 120;

export function gaugePosition(percentage: number | null | undefined): number {
  if (percentage === null || percentage === undefined || Number.isNaN(percentage)) return 0;
  return (Math.min(Math.max(percentage, 0), GAUGE_MAX) / GAUGE_MAX) * 100;
}

export const SETUP_LABEL: Record<SetupState, string> = {
  WITH_TREND: "With the trend",
  PULLBACK_IN_TREND: "Pullback in trend",
  WEAKENING_PULLBACK: "Weakening pullback",
  IMPULSE_INVALIDATED: "Impulse invalidated",
  COUNTER_TREND: "Counter-trend",
  NO_DIRECTIONAL_CONTEXT: "No directional context",
  UNCLEAR: "Unclear",
};

export const LEVEL_LABEL: Record<LevelType, string> = {
  PREVIOUS_DAY_HIGH: "Prev day high",
  PREVIOUS_DAY_LOW: "Prev day low",
  PREVIOUS_CLOSE: "Prev close",
  INTRADAY_HIGH: "Day high",
  INTRADAY_LOW: "Day low",
  SWING_HIGH: "Swing high",
  SWING_LOW: "Swing low",
  RANGE_HIGH: "Range high",
  RANGE_LOW: "Range low",
  BREAKOUT_LEVEL: "Breakout level",
  BREAKDOWN_LEVEL: "Breakdown level",
  VWAP: "VWAP",
  EMA: "EMA",
  MA: "MA",
  RETRACEMENT: "Retracement",
};

export type LevelGroup = "day" | "structure" | "dynamic" | "retracement";

export function levelGroup(type: LevelType): LevelGroup {
  switch (type) {
    case "PREVIOUS_DAY_HIGH":
    case "PREVIOUS_DAY_LOW":
    case "PREVIOUS_CLOSE":
    case "INTRADAY_HIGH":
    case "INTRADAY_LOW":
      return "day";
    case "VWAP":
    case "EMA":
    case "MA":
      return "dynamic";
    case "RETRACEMENT":
      return "retracement";
    default:
      return "structure";
  }
}

/**
 * Levels worth drawing on the chart: the `count` strongest, plus the
 * nearest support and resistance even if weaker, without duplicates.
 * Retracement levels are drawn separately, so they are excluded here.
 */
export function selectChartLevels(levels: PriceLevel[], count: number): PriceLevel[] {
  const candidates = levels.filter((l) => l.type !== "RETRACEMENT");
  const strongest = [...candidates].sort((a, b) => b.strength - a.strength).slice(0, count);
  const supports = candidates.filter((l) => l.role === "SUPPORT");
  const resistances = candidates.filter((l) => l.role === "RESISTANCE");
  const nearestSupport = supports.reduce<PriceLevel | null>(
    (best, l) => (best === null || l.price > best.price ? l : best),
    null
  );
  const nearestResistance = resistances.reduce<PriceLevel | null>(
    (best, l) => (best === null || l.price < best.price ? l : best),
    null
  );
  const chosen = new Map<string, PriceLevel>();
  for (const level of [...strongest, nearestSupport, nearestResistance]) {
    if (level) chosen.set(`${level.label}@${level.price}`, level);
  }
  return [...chosen.values()].sort((a, b) => b.price - a.price);
}

/** IST is UTC+05:30 all year (no daylight saving). */
export const IST_OFFSET_SECONDS = 5.5 * 60 * 60;

/**
 * Chart time in seconds, shifted so the chart's (UTC-based) axis reads
 * Indian Standard Time. The chart library has no time zone support.
 */
export function istChartTime(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000) + IST_OFFSET_SECONDS;
}

export function biasLabel(bias: number, threshold = 20): string {
  if (bias >= threshold) return "Bullish";
  if (bias <= -threshold) return "Bearish";
  return "Sideways";
}

/** Signed bias as a position on a -100..+100 bar, 0-100%. */
export function biasPosition(bias: number): number {
  return (Math.min(Math.max(bias, -100), 100) + 100) / 2;
}

/** "hsl(152 60% 42%)" style CSS variable value -> "#21ad6c" for the chart library. */
export function hslTripletToHex(triplet: string, fallback = "#888888"): string {
  const match = triplet.trim().match(/^([\d.]+)\s+([\d.]+)%\s+([\d.]+)%$/);
  if (!match) return fallback;
  const h = Number(match[1]) / 360;
  const s = Number(match[2]) / 100;
  const l = Number(match[3]) / 100;
  const hue = (p: number, q: number, t: number) => {
    let x = t;
    if (x < 0) x += 1;
    if (x > 1) x -= 1;
    if (x < 1 / 6) return p + (q - p) * 6 * x;
    if (x < 1 / 2) return q;
    if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
    return p;
  };
  let r: number;
  let g: number;
  let b: number;
  if (s === 0) {
    r = g = b = l;
  } else {
    const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
    const p = 2 * l - q;
    r = hue(p, q, h + 1 / 3);
    g = hue(p, q, h);
    b = hue(p, q, h - 1 / 3);
  }
  const toHex = (v: number) => Math.round(v * 255).toString(16).padStart(2, "0");
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`;
}
