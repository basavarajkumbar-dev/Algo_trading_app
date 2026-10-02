import { describe, expect, it } from "vitest";

import {
  buildGrid,
  buildRequest,
  changedParams,
  formatSeconds,
  formatShare,
  initialForm,
  matchingVersion,
  parseGridValues,
  weekdaysBack,
  weekdaysBetween,
} from "@/lib/backtest-format";
import type { BacktestOptions, ParamSpec } from "@/types/backtest";

const score: ParamSpec = {
  key: "min_score",
  label: "Min score",
  scope: "execution",
  kind: "float",
  default: 85,
  minimum: 40,
  maximum: 100,
  step: 5,
  choices: null,
  path: null,
  description: "",
};
const trail: ParamSpec = {
  ...score,
  key: "trailing_mode",
  label: "Trailing",
  kind: "choice",
  default: "NONE",
  minimum: null,
  maximum: null,
  choices: ["NONE", "BREAKEVEN", "ATR"],
};
const pull: ParamSpec = { ...score, key: "min_pullback_percent", label: "Pullback", scope: "strategy", default: 30, minimum: 10, maximum: 60 };

const options = {
  strategies: [
    { value: "ALL", label: "All", parameters: [score, trail], defaults: { min_score: 85, trailing_mode: "NONE" } },
    {
      value: "TREND_CONTINUATION",
      label: "TC",
      parameters: [pull, score, trail],
      defaults: { min_pullback_percent: 30, min_score: 85, trailing_mode: "NONE" },
    },
  ],
  versions: [
    { strategy_choice: "ALL", label: "a", version_ids: ["1.0.0+a", "1.0.0+b"], params: {}, current_default: true, stored: false },
    {
      strategy_choice: "TREND_CONTINUATION",
      label: "tc",
      version_ids: ["1.0.0+567cf21b"],
      params: { min_pullback_percent: 30 },
      current_default: true,
      stored: false,
    },
  ],
  data_sources: [
    {
      kind: "synthetic",
      label: "Synthetic",
      available: true,
      synthetic: true,
      first_day: "2024-01-01",
      last_day: "2026-10-01",
      symbols: ["RELIANCE", "TCS", "INFY", "SBIN"],
      note: "",
    },
  ],
  timeframes: ["5m", "15m"],
  modes: ["HISTORICAL", "OUT_OF_SAMPLE", "WALK_FORWARD"],
  limits: { max_symbols: 20, max_days: 250, max_grid_combinations: 27 },
  risk: {
    risk_per_trade_percent: 1,
    daily_loss_limit_percent: 3,
    max_trades_per_day: 5,
    max_consecutive_losses: 3,
    max_position_value: 50000,
    max_exposure: 50000,
    margin_multiplier: 1,
    allow_short: true,
  },
  costs: {
    slippage_bps: 5,
    brokerage_rate: 0.0003,
    brokerage_cap_per_order: 20,
    stt_sell_rate: 0.00025,
    exchange_rate: 0.0000297,
    sebi_per_crore: 10,
    stamp_buy_rate: 0.00003,
    gst_rate: 0.18,
  },
  validation: {
    oos_fraction: 0.3,
    wf_train_days: 20,
    wf_test_days: 5,
    wf_anchored: false,
    min_trades_for_selection: 8,
    null_control: false,
  },
  assumptions: [],
  note: "",
} as unknown as BacktestOptions;

describe("backtest form helpers", () => {
  it("counts weekdays and steps back over weekends", () => {
    expect(weekdaysBetween("2026-09-28", "2026-10-04")).toBe(5); // Mon..Sun
    expect(weekdaysBetween("2026-10-04", "2026-09-28")).toBe(0);
    expect(weekdaysBack("2026-10-05", 2)).toBe("2026-10-02"); // Mon → previous Fri
    expect(weekdaysBetween(weekdaysBack("2026-10-01", 30), "2026-10-01")).toBe(30);
  });

  it("starts from the defaults, ending on the last day of data", () => {
    const f = initialForm(options);
    expect(f.strategy).toBe("ALL");
    expect(f.endDate).toBe("2026-10-01");
    expect(f.mode).toBe("HISTORICAL");
    expect(f.params).toEqual({ min_score: 85, trailing_mode: "NONE" });
    expect(f.symbols).toEqual(["RELIANCE", "TCS", "INFY"]);
  });

  it("parses and validates grid values", () => {
    expect(parseGridValues("55, 65,75 65", score)).toEqual([55, 65, 75]);
    expect(parseGridValues("none, atr", trail)).toEqual(["NONE", "ATR"]);
    expect(() => parseGridValues("30", score)).toThrow(/below 40/);
    expect(() => parseGridValues("abc", score)).toThrow(/not a number/);
    expect(() => parseGridValues("TIGHT", trail)).toThrow(/not one of/);
  });

  it("builds a grid from keys with two or more values and counts combinations", () => {
    expect(buildGrid({ min_score: "55,65,75", trailing_mode: "NONE,ATR" }, [score, trail]).combinations).toBe(6);
    const single = buildGrid({ min_score: "55" }, [score, trail]);
    expect(single.grid).toEqual({});
    expect(single.combinations).toBe(0);
    expect(buildGrid({ min_score: "5" }, [score]).error).toMatch(/below/);
  });

  it("matches a version only while the strategy parameters are unchanged", () => {
    const f = { ...initialForm(options), strategy: "TREND_CONTINUATION" as const, params: { min_pullback_percent: 30, min_score: 60 } };
    const specs = [pull, score, trail];
    expect(matchingVersion(f, specs, options.versions)?.version_ids).toEqual(["1.0.0+567cf21b"]);
    // execution parameters do not define a version
    expect(matchingVersion({ ...f, params: { ...f.params, min_score: 90 } }, specs, options.versions)).not.toBeNull();
    expect(matchingVersion({ ...f, params: { ...f.params, min_pullback_percent: 35 } }, specs, options.versions)).toBeNull();
  });

  it("builds the request body, refusing incomplete forms", () => {
    const f = initialForm(options);
    const specs = [score, trail];
    const ok = buildRequest({ ...f, grid: { min_score: "70, 80" } }, specs, options.versions);
    expect(ok.error).toBeNull();
    expect(ok.body?.grid).toEqual({ min_score: [70, 80] });
    expect(ok.body?.parameters).toEqual({ min_score: 85, trailing_mode: "NONE" });
    expect(ok.body?.version).toBe("1.0.0+a");
    expect(ok.body?.name).toBeNull();
    expect(buildRequest({ ...f, symbols: [] }, specs, options.versions).error).toMatch(/symbol/);
    expect(buildRequest({ ...f, startDate: "2026-10-02" }, specs, options.versions).error).toMatch(/before/);
    expect(buildRequest({ ...f, mode: "WALK_FORWARD" }, specs, options.versions).error).toMatch(/Walk-forward/);
  });

  it("formats durations, shares and parameter differences", () => {
    expect(formatSeconds(42)).toBe("42s");
    expect(formatSeconds(125)).toBe("2m 05s");
    expect(formatShare(0.234)).toBe("23%");
    expect(formatShare(null)).toBe("—");
    expect(changedParams({ a: 1, b: "ATR" }, { a: 1, b: "NONE" })).toBe("b ATR");
    expect(changedParams({ a: 1 }, { a: 1 })).toBe("defaults");
  });
});
