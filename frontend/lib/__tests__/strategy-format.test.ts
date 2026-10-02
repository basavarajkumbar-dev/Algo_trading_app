import { describe, expect, it } from "vitest";

import {
  STRATEGY_LABEL,
  STRATEGY_ORDER,
  STRATEGY_STAGES,
  orderedChecks,
  percentFromEntry,
  stageIndex,
  stageLabel,
} from "@/lib/strategy-format";
import type { StrategyResult } from "@/types/strategies";

describe("strategy metadata", () => {
  it("covers each strategy once", () => {
    expect([...STRATEGY_ORDER].sort()).toEqual(Object.keys(STRATEGY_LABEL).sort());
    for (const name of STRATEGY_ORDER) {
      expect(STRATEGY_STAGES[name].at(-1)).toBe("ENTRY_CANDIDATE");
    }
  });

  it("finds the furthest stage", () => {
    expect(stageIndex("TREND_CONTINUATION", "PULLBACK")).toBe(2);
    expect(stageIndex("MEAN_REVERSION", null)).toBe(-1);
    expect(stageIndex("BREAKOUT_MOMENTUM", "ENTRY_CANDIDATE")).toBe(5);
    expect(stageLabel("VOLUME_BUILDUP")).toBe("Volume buildup");
  });
});

describe("candidates", () => {
  it("signs moves in the trade's favour", () => {
    expect(percentFromEntry(100, 110, "LONG")).toBeCloseTo(10);
    expect(percentFromEntry(100, 90, "SHORT")).toBeCloseTo(10);
    expect(percentFromEntry(100, 95, "LONG")).toBeCloseTo(-5);
  });

  it("orders required checks first, then by weight", () => {
    const check = (key: string, required: boolean, weight: number) => ({
      key,
      label: key,
      passed: true,
      required,
      weight,
      detail: "",
    });
    const result = {
      checks: [check("a", false, 5), check("b", true, 0), check("c", false, 15), check("d", true, 10)],
    } as unknown as StrategyResult;
    expect(orderedChecks(result).map((c) => c.key)).toEqual(["d", "b", "c", "a"]);
  });
});
