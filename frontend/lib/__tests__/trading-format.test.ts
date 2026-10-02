import { describe, expect, it } from "vitest";

import {
  EXIT_REASON_LABEL,
  LIFECYCLE,
  REJECTION_LABEL,
  STATE_LABEL,
  formatInr,
  formatR,
  humanCheck,
  lifecycleIndex,
  pnlTone,
  usedPercent,
} from "@/lib/trading-format";

describe("trading formatting", () => {
  it("orders the lifecycle and labels every state", () => {
    expect(LIFECYCLE[0]).toBe("SCANNING");
    expect(LIFECYCLE.at(-1)).toBe("CLOSED");
    expect(Object.keys(STATE_LABEL)).toHaveLength(8);
    expect(lifecycleIndex("IDLE")).toBe(0);
    expect(lifecycleIndex("POSITION_ACTIVE")).toBe(4);
  });

  it("labels every exit and rejection reason", () => {
    expect(Object.keys(EXIT_REASON_LABEL)).toHaveLength(7);
    expect(Object.keys(REJECTION_LABEL)).toHaveLength(18);
    expect(REJECTION_LABEL.ACTIVE_POSITION_EXISTS).toMatch(/already active/);
  });

  it("formats rupees with Indian grouping", () => {
    expect(formatInr(123456.78)).toBe("₹1,23,456.78");
    expect(formatInr(-500)).toBe("−₹500.00");
    expect(formatInr(25, true)).toBe("+₹25.00");
    expect(formatInr(null)).toBe("—");
  });

  it("formats R multiples and P&L tones", () => {
    expect(formatR(1.754)).toBe("+1.75R");
    expect(formatR(-1.1)).toBe("−1.10R");
    expect(formatR(null)).toBe("—");
    expect(pnlTone(10)).toBe("text-profit");
    expect(pnlTone(-0.01)).toBe("text-loss");
    expect(pnlTone(0)).toBe("text-muted");
  });

  it("clamps limit usage", () => {
    expect(usedPercent(1500, 3000)).toBe(50);
    expect(usedPercent(5000, 3000)).toBe(100);
    expect(usedPercent(-1, 3000)).toBe(0);
    expect(usedPercent(1, 0)).toBe(100);
    expect(humanCheck("one_active_trade")).toBe("One active trade");
  });
});
