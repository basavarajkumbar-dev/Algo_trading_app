import { describe, expect, it } from "vitest";

import {
  MAX_REGIME_CHANGES,
  MAX_TRADING_EVENTS,
  applyMarketMessage,
  initialMarketState,
} from "@/lib/stores/market-store";
import type { WsMessage } from "@/types/market";

const snap = (symbol: string, price: number, change = 0): WsMessage => ({
  type: "market_snapshot",
  symbol,
  price,
  change,
  change_percent: price - change ? (change / (price - change)) * 100 : 0,
  volume: 10,
  timestamp: "2026-10-02T05:00:00Z",
});

describe("market store reducer", () => {
  it("stores snapshots and derives tick changes from the day open", () => {
    let s = applyMarketMessage(initialMarketState, snap("TCS", 1010, 10), 1);
    s = applyMarketMessage(
      s,
      { type: "market_tick", symbol: "TCS", price: 1020, volume: 5, timestamp: "2026-10-02T05:00:01Z" },
      2
    );
    expect(s.snapshots.TCS!.price).toBe(1020);
    expect(s.snapshots.TCS!.change).toBe(20); // open 1000
    expect(s.snapshots.TCS!.change_percent).toBeCloseTo(2);
    expect(s.messagesReceived).toBe(2);
    expect(s.lastMessageAt).toBe(2);
  });

  it("keeps changed regimes only, newest first, capped", () => {
    let s = initialMarketState;
    for (let i = 0; i < MAX_REGIME_CHANGES + 3; i++) {
      s = applyMarketMessage(
        s,
        {
          type: "market_regime",
          symbol: `S${i}`,
          regime: "RANGE",
          previous_regime: "BREAKOUT",
          changed: i % 2 === 0 || i > 2,
          confidence: 60,
          reasons: [],
          timestamp: "2026-10-02T05:00:00Z",
        },
        i
      );
    }
    expect(s.regimeChanges).toHaveLength(MAX_REGIME_CHANGES);
    expect(s.regimeChanges[0]!.symbol).toBe(`S${MAX_REGIME_CHANGES + 2}`);
    const unchanged = applyMarketMessage(
      initialMarketState,
      { ...(s.regimeChanges[0] as object), changed: false } as WsMessage,
      0
    );
    expect(unchanged.regimeChanges).toHaveLength(0);
  });

  it("keeps a capped feed of trading events", () => {
    let s = initialMarketState;
    for (let i = 0; i < MAX_TRADING_EVENTS + 5; i++) {
      s = applyMarketMessage(
        s,
        { type: "trading_event", event_type: "ORDER_PLACED", severity: "INFO", message: `#${i}`, at: "", metadata: {} },
        i
      );
    }
    expect(s.tradingEvents).toHaveLength(MAX_TRADING_EVENTS);
    expect(s.tradingEvents[0]!.message).toBe(`#${MAX_TRADING_EVENTS + 4}`);
  });

  it("ignores unknown messages without losing state", () => {
    const s = applyMarketMessage(initialMarketState, snap("A", 1), 1);
    const next = applyMarketMessage(s, { type: "connected", message: "hi", watchlist: [] }, 2);
    expect(next.snapshots).toBe(s.snapshots);
  });
});
