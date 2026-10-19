/** Pure presentation helpers for the Strategies page (unit-tested). */

import type { StrategyName, StrategyResult, TradeDirection } from "@/types/strategies";

export const STRATEGY_LABEL: Record<StrategyName, string> = {
  TREND_CONTINUATION: "Trend continuation",
  BREAKOUT_MOMENTUM: "Breakout momentum",
  MEAN_REVERSION: "Mean reversion",
  RANGE_TRADING: "Range trading",
  BREAKOUT_TRADING: "Breakout trading (confirmed)",
  VOLUME_TRADING: "Volume-based trading",
  MA_CROSSOVER: "Moving-average crossover",
};

export const STRATEGY_ORDER: StrategyName[] = [
  "TREND_CONTINUATION",
  "BREAKOUT_MOMENTUM",
  "MEAN_REVERSION",
  "RANGE_TRADING",
  "BREAKOUT_TRADING",
  "VOLUME_TRADING",
  "MA_CROSSOVER",
];

/** Each strategy's concept from the brief, as the stage names the backend reports. */
export const STRATEGY_STAGES: Record<StrategyName, string[]> = {
  TREND_CONTINUATION: ["TREND", "IMPULSE", "PULLBACK", "SUPPORT", "CONFIRMATION", "ENTRY_CANDIDATE"],
  BREAKOUT_MOMENTUM: ["CONSOLIDATION", "COMPRESSION", "VOLUME_BUILDUP", "BREAKOUT", "CONFIRMATION", "ENTRY_CANDIDATE"],
  MEAN_REVERSION: ["RANGE", "RANGE_EDGE", "EXTREME", "REVERSAL", "ENTRY_CANDIDATE"],
  RANGE_TRADING: ["RANGE", "NOT_TRENDING", "AT_BOUNDARY", "CONFIRMATION", "ENTRY_CANDIDATE"],
  BREAKOUT_TRADING: ["LEVEL", "BREAKOUT", "VOLUME", "CONFIRMATION", "ENTRY_CANDIDATE"],
  VOLUME_TRADING: ["VOLUME", "SETUP", "CONFIRMATION", "ENTRY_CANDIDATE"],
  MA_CROSSOVER: ["CROSSOVER", "TREND_STRENGTH", "NOT_CHOPPY", "ENTRY_CANDIDATE"],
};

/** Index of the furthest stage reached, -1 for none. */
export function stageIndex(strategy: StrategyName, stage: string | null): number {
  if (!stage) return -1;
  return STRATEGY_STAGES[strategy].indexOf(stage);
}

export function stageLabel(stage: string): string {
  const text = stage.replace(/_/g, " ").toLowerCase();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function directionTone(direction: TradeDirection): string {
  if (direction === "LONG") return "text-profit";
  if (direction === "SHORT") return "text-loss";
  return "text-muted";
}

/** Required checks first, then weighted ones by weight. */
export function orderedChecks(result: StrategyResult) {
  return [...result.checks].sort((a, b) => {
    if (a.required !== b.required) return a.required ? -1 : 1;
    return b.weight - a.weight;
  });
}

/** Percent move from entry to a price, signed in the trade's favour. */
export function percentFromEntry(entry: number, price: number, direction: TradeDirection): number {
  const sign = direction === "SHORT" ? -1 : 1;
  return ((price - entry) / entry) * 100 * sign;
}
