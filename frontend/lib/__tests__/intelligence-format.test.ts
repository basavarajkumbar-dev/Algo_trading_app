import { describe, expect, it } from "vitest";

import {
  REGIME_META,
  REGIME_ORDER,
  changeTone,
  factorTone,
  formatIstTime,
  formatPercent,
  formatPrice,
  formatScore,
  humanize,
} from "@/lib/intelligence-format";

describe("formatIstTime", () => {
  it("converts UTC to Indian Standard Time (UTC+05:30)", () => {
    expect(formatIstTime("2026-10-01T04:00:00Z")).toBe("09:30 IST");
    expect(formatIstTime("2026-10-01T09:59:30Z", true)).toBe("15:29:30 IST");
  });

  it("renders a dash for missing or invalid input", () => {
    expect(formatIstTime(null)).toBe("—");
    expect(formatIstTime(undefined)).toBe("—");
    expect(formatIstTime("not a date")).toBe("—");
  });
});

describe("number formatting", () => {
  it("formats prices with two decimals and Indian grouping", () => {
    expect(formatPrice(2456.5)).toBe("2,456.50");
    expect(formatPrice(1234567.891)).toBe("12,34,567.89");
    expect(formatPrice(null)).toBe("—");
  });

  it("signs positive percentages only when asked", () => {
    expect(formatPercent(1.234)).toBe("+1.23%");
    expect(formatPercent(-0.5)).toBe("-0.50%");
    expect(formatPercent(0)).toBe("0.00%");
    expect(formatPercent(1.5, 1, false)).toBe("1.5%");
    expect(formatPercent(null)).toBe("—");
  });

  it("rounds scores and keeps zero distinct from missing", () => {
    expect(formatScore(67.6)).toBe("68");
    expect(formatScore(0)).toBe("0");
    expect(formatScore(null)).toBe("—");
  });
});

describe("factorTone", () => {
  it("centres directional factors on 50 with a neutral band", () => {
    expect(factorTone(70, "directional")).toBe("positive");
    expect(factorTone(30, "directional")).toBe("negative");
    expect(factorTone(52, "directional")).toBe("neutral");
    expect(factorTone(55, "directional")).toBe("positive");
    expect(factorTone(45, "directional")).toBe("negative");
    expect(factorTone(null, "directional")).toBe("neutral");
  });

  it("never gives intensity factors a direction", () => {
    expect(factorTone(95, "intensity")).toBe("intensity");
    expect(factorTone(5, "intensity")).toBe("intensity");
  });
});

describe("changeTone and humanize", () => {
  it("colours changes by sign", () => {
    expect(changeTone(0.1)).toBe("text-profit");
    expect(changeTone(-0.1)).toBe("text-loss");
    expect(changeTone(0)).toBe("text-muted");
    expect(changeTone(null)).toBe("text-muted");
  });

  it("turns enum values into sentence case", () => {
    expect(humanize("BREAK_OF_STRUCTURE")).toBe("Break of structure");
    expect(humanize("UP")).toBe("Up");
    expect(humanize(null)).toBe("—");
  });
});

describe("regime metadata", () => {
  it("covers every regime exactly once in display order", () => {
    expect(new Set(REGIME_ORDER).size).toBe(REGIME_ORDER.length);
    expect([...REGIME_ORDER].sort()).toEqual(Object.keys(REGIME_META).sort());
  });

  it("describes regimes without predictive language", () => {
    const forbidden = /\b(will|guarantee|guaranteed|expect|predict|forecast|sure)\b/i;
    for (const meta of Object.values(REGIME_META)) {
      expect(meta.description).not.toMatch(forbidden);
    }
  });
});
