import { describe, expect, it } from "vitest";

import {
  GAUGE_MAX,
  IST_OFFSET_SECONDS,
  PATTERN_META,
  PATTERN_ORDER,
  ZONE_META,
  ZONE_ORDER,
  biasLabel,
  biasPosition,
  directionTone,
  gaugePosition,
  hslTripletToHex,
  istChartTime,
  levelGroup,
  selectChartLevels,
} from "@/lib/price-action-format";
import type { PriceLevel } from "@/types/price-action";

function level(partial: Partial<PriceLevel>): PriceLevel {
  return {
    label: "Swing high",
    type: "SWING_HIGH",
    price: 100,
    role: "RESISTANCE",
    distance_percent: 0,
    distance_atr: 0,
    strength: 50,
    touch_count: 1,
    timeframe: "5m",
    recency: 0,
    confluence: [],
    components: {},
    dynamic: false,
    ...partial,
  };
}

describe("pattern metadata", () => {
  it("covers all six detectors once", () => {
    expect([...PATTERN_ORDER].sort()).toEqual(Object.keys(PATTERN_META).sort());
    expect(PATTERN_ORDER).toHaveLength(6);
  });

  it("describes patterns without predicting", () => {
    const forbidden = /\b(will|guarantee|guaranteed|predict|forecast|buy|sell)\b/i;
    for (const meta of Object.values(PATTERN_META)) {
      expect(meta.meaning).not.toMatch(forbidden);
    }
  });

  it("colours direction", () => {
    expect(directionTone("BULLISH")).toBe("text-profit");
    expect(directionTone("BEARISH")).toBe("text-loss");
    expect(directionTone("NEUTRAL")).toBe("text-muted");
  });
});

describe("retracement gauge", () => {
  it("has contiguous zones matching the backend bounds", () => {
    for (let i = 1; i < ZONE_ORDER.length; i++) {
      expect(ZONE_META[ZONE_ORDER[i]!].from).toBe(ZONE_META[ZONE_ORDER[i - 1]!].to);
    }
    expect(ZONE_META.HEALTHY_PULLBACK.from).toBe(40);
    expect(ZONE_META.INVALIDATION_CONTEXT.from).toBe(100);
    expect(ZONE_META.INVALIDATION_CONTEXT.to).toBe(GAUGE_MAX);
  });

  it("positions percentages on a 0-120% scale and clamps", () => {
    expect(gaugePosition(0)).toBe(0);
    expect(gaugePosition(60)).toBe(50);
    expect(gaugePosition(120)).toBe(100);
    expect(gaugePosition(250)).toBe(100);
    expect(gaugePosition(-5)).toBe(0);
    expect(gaugePosition(null)).toBe(0);
  });
});

describe("bias", () => {
  it("labels with the backend threshold", () => {
    expect(biasLabel(20)).toBe("Bullish");
    expect(biasLabel(19.9)).toBe("Sideways");
    expect(biasLabel(-20)).toBe("Bearish");
  });

  it("maps -100..+100 to 0..100%", () => {
    expect(biasPosition(-100)).toBe(0);
    expect(biasPosition(0)).toBe(50);
    expect(biasPosition(100)).toBe(100);
    expect(biasPosition(300)).toBe(100);
  });
});

describe("chart helpers", () => {
  it("shifts UTC to IST for the chart axis", () => {
    const utc = Date.UTC(2026, 9, 1, 4, 0, 0) / 1000; // 09:30 IST
    expect(istChartTime("2026-10-01T04:00:00Z")).toBe(utc + IST_OFFSET_SECONDS);
    expect(IST_OFFSET_SECONDS).toBe(19800);
  });

  it("converts theme HSL triplets to hex", () => {
    expect(hslTripletToHex("0 0% 100%")).toBe("#ffffff");
    expect(hslTripletToHex("0 100% 50%")).toBe("#ff0000");
    expect(hslTripletToHex("120 100% 25%")).toBe("#008000");
    expect(hslTripletToHex("not a colour", "#123456")).toBe("#123456");
  });

  it("groups level types", () => {
    expect(levelGroup("PREVIOUS_DAY_HIGH")).toBe("day");
    expect(levelGroup("VWAP")).toBe("dynamic");
    expect(levelGroup("RETRACEMENT")).toBe("retracement");
    expect(levelGroup("SWING_LOW")).toBe("structure");
  });

  it("selects the strongest levels plus the nearest on each side", () => {
    const levels = [
      level({ label: "A", price: 110, strength: 90 }),
      level({ label: "B", price: 105, strength: 20 }), // nearest resistance, weak
      level({ label: "C", price: 99, role: "SUPPORT", strength: 15 }), // nearest support, weak
      level({ label: "D", price: 95, role: "SUPPORT", strength: 80 }),
      level({ label: "E", price: 90, role: "SUPPORT", strength: 30 }),
      level({ label: "Retracement 50%", type: "RETRACEMENT", price: 101, strength: 99 }),
    ];
    const chosen = selectChartLevels(levels, 2).map((l) => l.label);
    expect(chosen).toEqual(["A", "B", "C", "D"]); // sorted by price, no retracement, no duplicates
  });
});
