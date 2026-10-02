import { describe, expect, it } from "vitest";

import {
  SCAN_STATUS,
  SCAN_STATUS_ORDER,
  arrow,
  formatTurnover,
  reasonMark,
  scoreTone,
  sideTone,
  stripMark,
} from "@/lib/signal-format";

describe("signal formatting", () => {
  it("covers every scan status", () => {
    expect([...SCAN_STATUS_ORDER].sort()).toEqual(Object.keys(SCAN_STATUS).sort());
  });

  it("colours scores by the category bands", () => {
    expect(scoreTone(95)).toBe("bg-accent");
    expect(scoreTone(85)).toBe("bg-profit");
    expect(scoreTone(70)).toBe("bg-warning");
    expect(scoreTone(69.9)).toBe("bg-muted");
  });

  it("colours sides and directions", () => {
    expect(sideTone("BUY")).toBe("text-profit");
    expect(sideTone("DOWN")).toBe("text-loss");
    expect(sideTone(null)).toBe("text-muted");
    expect(arrow("UP")).toBe("↑");
    expect(arrow("SIDEWAYS")).toBe("→");
    expect(arrow(null)).toBe("—");
  });

  it("formats turnover in lakh and crore", () => {
    expect(formatTurnover(12_345_678)).toBe("1.2 Cr");
    expect(formatTurnover(1_234_567)).toBe("12.3 L");
    expect(formatTurnover(54_321)).toBe("54,321");
    expect(formatTurnover(null)).toBe("—");
  });

  it("reads the reason marks from the backend explanation", () => {
    expect(reasonMark("✓ VWAP: price above VWAP (10.5/10.5)")).toBe("pass");
    expect(reasonMark("✗ Volume: 0.9x (1.0/10.5)")).toBe("fail");
    expect(reasonMark("– Retracement: not part of this strategy")).toBe("na");
    expect(reasonMark("Final score 92 / 100 (ELITE)")).toBe("total");
    expect(stripMark("✓ VWAP: above")).toBe("VWAP: above");
  });
});
