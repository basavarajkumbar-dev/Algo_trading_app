/**
 * Pure presentation helpers for the Market Intelligence dashboard.
 * Kept free of React so they can be unit-tested directly.
 */

import type { FactorScale, MarketRegime } from "@/types/intelligence";

export type RegimeBadgeVariant = "connected" | "disconnected" | "neutral" | "accent" | "caution";

export interface RegimeMeta {
  label: string;
  short: string;
  variant: RegimeBadgeVariant;
  /** Plain description of what the regime means. Descriptive, never predictive. */
  description: string;
}

export const REGIME_META: Record<MarketRegime, RegimeMeta> = {
  STRONG_BULLISH: {
    label: "Strong bullish",
    short: "Bullish",
    variant: "connected",
    description: "Price has been trending up: above VWAP, rising averages, higher highs.",
  },
  STRONG_BEARISH: {
    label: "Strong bearish",
    short: "Bearish",
    variant: "disconnected",
    description: "Price has been trending down: below VWAP, falling averages, lower lows.",
  },
  RANGE: {
    label: "Range",
    short: "Range",
    variant: "neutral",
    description: "Price has been moving sideways, repeatedly crossing VWAP without direction.",
  },
  BREAKOUT: {
    label: "Breakout",
    short: "Breakout",
    variant: "accent",
    description: "Price has just left a quiet, compressed range on expanding volume.",
  },
  HIGH_VOLATILITY: {
    label: "High volatility",
    short: "High vol",
    variant: "caution",
    description: "Candles are unusually large with long wicks and frequent reversals.",
  },
  UNKNOWN: {
    label: "Unclear",
    short: "Unclear",
    variant: "neutral",
    description: "No regime is clearly supported by the data yet.",
  },
};

export const REGIME_ORDER: MarketRegime[] = [
  "STRONG_BULLISH",
  "STRONG_BEARISH",
  "BREAKOUT",
  "RANGE",
  "HIGH_VOLATILITY",
  "UNKNOWN",
];

const IST_TIME = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const IST_TIME_SECONDS = new Intl.DateTimeFormat("en-IN", {
  timeZone: "Asia/Kolkata",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hour12: false,
});

/** "14:35 IST". The backend works in UTC; the dashboard shows Indian time. */
export function formatIstTime(iso: string | null | undefined, withSeconds = false): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return `${(withSeconds ? IST_TIME_SECONDS : IST_TIME).format(date)} IST`;
}

export function formatPrice(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

export function formatPercent(value: number | null | undefined, digits = 2, signed = true): string {
  if (value === null || value === undefined) return "—";
  const sign = signed && value > 0 ? "+" : "";
  return `${sign}${value.toFixed(digits)}%`;
}

export function formatScore(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return Math.round(value).toString();
}

export type FactorTone = "positive" | "negative" | "neutral" | "intensity";

/**
 * Directional factors are centred on 50: clearly above reads positive,
 * clearly below negative, near 50 neutral. Intensity factors (trend
 * quality, volume, volatility, structure confidence) have no direction.
 */
export function factorTone(score: number | null, scale: FactorScale, band = 5): FactorTone {
  if (scale === "intensity") return "intensity";
  if (score === null) return "neutral";
  if (score >= 50 + band) return "positive";
  if (score <= 50 - band) return "negative";
  return "neutral";
}

export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  const text = value.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** Signed change classes: green up, red down, muted flat. */
export function changeTone(value: number | null | undefined): "text-profit" | "text-loss" | "text-muted" {
  if (value === null || value === undefined || value === 0) return "text-muted";
  return value > 0 ? "text-profit" : "text-loss";
}
