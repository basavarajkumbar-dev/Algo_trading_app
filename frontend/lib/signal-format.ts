/** Pure presentation helpers for the Scanner and Signals pages (unit-tested). */

import type { BadgeProps } from "@/components/ui/badge";
import type { ScanStatus, SignalCategory, SignalDirection } from "@/types/signals";

export const CATEGORY_VARIANT: Record<SignalCategory, NonNullable<BadgeProps["variant"]>> = {
  ELITE: "accent",
  STRONG: "connected",
  WATCH: "pending",
  IGNORE: "neutral",
};

export const SCAN_STATUS: Record<ScanStatus, { label: string; variant: NonNullable<BadgeProps["variant"]>; hint: string }> = {
  READY: { label: "Ready", variant: "accent", hint: "Signal at or above the executable threshold" },
  WATCH: { label: "Watch", variant: "pending", hint: "Signal below the executable threshold" },
  IGNORE: { label: "Ignore", variant: "neutral", hint: "Signal scored in the ignore band" },
  NO_SETUP: { label: "No setup", variant: "neutral", hint: "Strategies are routed but none has a setup" },
  NO_ROUTE: { label: "No route", variant: "neutral", hint: "The regime routes no strategy (defensive)" },
  NO_DATA: { label: "No data", variant: "disconnected", hint: "Not enough data to analyse yet" },
};

export const SCAN_STATUS_ORDER: ScanStatus[] = ["READY", "WATCH", "IGNORE", "NO_SETUP", "NO_ROUTE", "NO_DATA"];

export function sideTone(direction: SignalDirection | string | null | undefined): string {
  if (direction === "BUY" || direction === "UP" || direction === "BULLISH") return "text-profit";
  if (direction === "SELL" || direction === "DOWN" || direction === "BEARISH") return "text-loss";
  return "text-muted";
}

/** "UP" -> "↑", "DOWN" -> "↓", otherwise "→"; null -> "—". */
export function arrow(direction: string | null | undefined): string {
  if (!direction) return "—";
  if (direction === "UP") return "↑";
  if (direction === "DOWN") return "↓";
  return "→";
}

/** Score bar colour by category band. */
export function scoreTone(score: number, bands = { elite: 90, strong: 80, watch: 70 }): string {
  if (score >= bands.elite) return "bg-accent";
  if (score >= bands.strong) return "bg-profit";
  if (score >= bands.watch) return "bg-warning";
  return "bg-muted";
}

/** Indian-style compact value: 12,34,567 -> "12.3 L", 1,23,45,678 -> "1.2 Cr". */
export function formatTurnover(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  if (value >= 1e7) return `${(value / 1e7).toFixed(1)} Cr`;
  if (value >= 1e5) return `${(value / 1e5).toFixed(1)} L`;
  return value.toLocaleString("en-IN", { maximumFractionDigits: 0 });
}

/** The reason line's leading mark from the backend: ✓ passed, ✗ failed, – not applicable. */
export function reasonMark(line: string): "pass" | "fail" | "na" | "total" {
  if (line.startsWith("✓")) return "pass";
  if (line.startsWith("✗")) return "fail";
  if (line.startsWith("–")) return "na";
  return "total";
}

export function stripMark(line: string): string {
  return line.replace(/^[✓✗–]\s*/, "");
}
