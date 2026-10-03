import { describe, expect, it } from "vitest";

import { activeNavHref, NAV_ITEMS } from "@/components/layout/nav-items";
import { axisInr, ZERO_DOMAIN } from "@/components/analytics/charts";
import {
  algoStatus,
  formatDuration,
  marketStatus,
  positionGeometry,
  strategyLabel,
  timeAgo,
} from "@/lib/dashboard-format";
import { countUnread } from "@/lib/stores/ui-store";
import { tokenToHsl } from "@/lib/use-theme-colors";
import type { TradingStatus } from "@/types/trading";

import status from "../../test/fixtures/trading-status.json";

const base = status as unknown as TradingStatus;
const with_ = (patch: Partial<TradingStatus>): TradingStatus => ({ ...base, ...patch });

describe("position geometry: SL (0) -> TARGET (1)", () => {
  it("places a long's entry and current between stop and target", () => {
    const g = positionGeometry(990, 1000, 1020, 1010);
    expect(g.entry).toBeCloseTo(1 / 3);
    expect(g.current).toBeCloseTo(2 / 3);
    expect(g.rNow).toBeCloseTo(1);
    expect(g.beyondStop || g.beyondTarget).toBe(false);
  });

  it("works for a short, whose target is below the stop", () => {
    const g = positionGeometry(1010, 1000, 980, 990);
    expect(g.entry).toBeCloseTo(1 / 3);
    expect(g.current).toBeCloseTo(2 / 3);
    expect(g.rNow).toBeCloseTo(1); // 10 points in favour = +1R
    const losing = positionGeometry(1010, 1000, 980, 1005);
    expect(losing.rNow).toBeCloseTo(-0.5);
    expect(losing.current!).toBeLessThan(losing.entry);
  });

  it("clamps and flags a price past the stop or target", () => {
    const past = positionGeometry(990, 1000, 1020, 985);
    expect(past.beyondStop).toBe(true);
    expect(past.currentClamped).toBe(0);
    const over = positionGeometry(990, 1000, 1020, 1030);
    expect(over.beyondTarget).toBe(true);
    expect(over.currentClamped).toBe(1);
    expect(positionGeometry(990, 1000, 1020, null).current).toBeNull();
  });
});

describe("status labels", () => {
  it("derives the algo badge from the engine state", () => {
    expect(algoStatus(undefined).label).toBe("Unknown");
    expect(algoStatus(with_({ is_running: false })).label).toBe("Offline");
    expect(algoStatus(with_({ emergency_stop: true })).label).toBe("Emergency stop");
    expect(algoStatus(with_({ state: "POSITION_ACTIVE" })).label).toBe("In position");
    expect(algoStatus(with_({ state: "SCANNING", auto_trading: true })).label).toBe("Auto · scanning");
    expect(algoStatus(with_({ state: "IDLE", auto_trading: false, emergency_stop: false })).label).toBe("Stopped");
  });

  it("describes the market session honestly", () => {
    expect(marketStatus(with_({ session_mode: "SIMULATED_24H" })).label).toBe("Simulated 24h");
    expect(marketStatus(with_({ session_mode: "EXCHANGE", market_open: false })).label).toBe("Closed");
    expect(
      marketStatus(with_({ session_mode: "EXCHANGE", market_open: true, entries_allowed: false })).label
    ).toBe("No new entries");
    expect(
      marketStatus(with_({ session_mode: "EXCHANGE", market_open: true, entries_allowed: true })).label
    ).toBe("Open");
  });
});

describe("small formatters", () => {
  it("formats durations and ages", () => {
    const t0 = Date.parse("2026-10-02T05:00:00Z");
    expect(formatDuration("2026-10-02T05:00:00Z", t0 + 75_000)).toBe("1m 15s");
    expect(formatDuration("2026-10-02T05:00:00Z", t0 + 3_900_000)).toBe("1h 05m");
    expect(formatDuration(null)).toBe("—");
    expect(timeAgo("2026-10-02T05:00:00Z", t0 + 10_000)).toBe("now");
    expect(timeAgo("2026-10-02T05:00:00Z", t0 + 600_000)).toBe("10m");
    expect(timeAgo("2026-10-02T05:00:00Z", t0 + 7_200_000)).toBe("2h");
  });

  it("counts unread notifications from the last open", () => {
    const times = ["2026-10-02T05:03:00Z", "2026-10-02T05:02:00Z", "2026-10-02T05:01:00Z"];
    expect(countUnread(times, null)).toBe(3);
    expect(countUnread(times, "2026-10-02T05:01:30Z")).toBe(2);
    expect(countUnread(times, "2026-10-02T05:03:00Z")).toBe(0);
  });

  it("labels strategies, tokens and chart axes", () => {
    expect(strategyLabel("TREND_CONTINUATION")).toBe("Trend continuation");
    expect(strategyLabel(null)).toBe("—");
    expect(tokenToHsl("38 75% 55%")).toBe("hsl(38, 75%, 55%)");
    expect(tokenToHsl("")).toBeNull();
    expect(axisInr(100000)).toBe("₹1,00,000");
    expect(axisInr(-250)).toBe("−₹250");
    expect(ZERO_DOMAIN[0](-70)).toBe(-70);
    expect(ZERO_DOMAIN[0](40)).toBe(0); // bars grow from zero
    expect(ZERO_DOMAIN[1](-70)).toBe(0);
  });
});

describe("navigation", () => {
  it("has the fourteen sections in order", () => {
    expect(NAV_ITEMS.map((i) => i.label)).toEqual([
      "Dashboard",
      "Market",
      "Scanner",
      "Signals",
      "Approvals",
      "Positions",
      "Trades",
      "Analytics",
      "Strategies",
      "Backtesting",
      "Risk",
      "Live",
      "System",
      "Settings",
    ]);
  });

  it("highlights the right section", () => {
    expect(activeNavHref("/")).toBe("/");
    expect(activeNavHref("/signals")).toBe("/signals");
    expect(activeNavHref("/price-action")).toBe("/market");
  });
});
