import { describe, expect, it } from "vitest";

import {
  authLabel,
  brokerLabel,
  candleDot,
  dataNoun,
  dataSourceLabel,
  executionLabel,
  marketDataLabel,
} from "@/lib/market-data-format";
import type { MarketDataStatus, ZerodhaStatus } from "@/types/market-data";

const md = (over: Partial<MarketDataStatus>): MarketDataStatus => ({
  provider: "ZERODHA",
  mode: "ZERODHA_LIVE",
  simulated: false,
  status: "LIVE",
  websocket: "CONNECTED",
  feed_state: "LIVE",
  rest_api: "OK",
  last_tick: null,
  last_tick_age_seconds: 1,
  latency_ms: 120,
  subscribed_instruments: 51,
  unresolved_symbols: [],
  stale: false,
  stale_threshold_seconds: 10,
  ticks_accepted: 10,
  ticks_rejected: 0,
  rejected_by_issue: {},
  warmup_in_progress: false,
  last_warmup_at: null,
  candle_status_symbol: "NIFTY50",
  candles: {},
  ...over,
});

const z = (over: Partial<ZerodhaStatus>): ZerodhaStatus => ({
  broker: "ZERODHA",
  configured: true,
  connected: true,
  auth: "AUTHENTICATED",
  user_id: "AB**34",
  user_name: null,
  login_time: null,
  expires_at: null,
  last_error: null,
  redirect_url_configured: true,
  market_data: "CONNECTED",
  execution_mode: "PAPER",
  ...over,
});

describe("data source labelling", () => {
  it("never lets simulated data look real", () => {
    const sim = md({ simulated: true, provider: "MOCK", mode: "SIMULATED", status: "LIVE" });
    expect(dataSourceLabel(sim).text).toBe("SIMULATED");
    expect(dataSourceLabel(sim).live).toBe(false);
    expect(marketDataLabel(sim).text).toBe("SIMULATED");
    expect(dataNoun(sim)).toBe("simulated");
    expect(dataNoun(undefined)).toBe("simulated");
  });

  it("says ZERODHA LIVE only while real data is fresh", () => {
    expect(dataSourceLabel(md({})).text).toBe("ZERODHA LIVE");
    expect(dataSourceLabel(md({ status: "STALE", last_tick_age_seconds: 42 })).text).toBe("ZERODHA · STALE");
    expect(dataSourceLabel(md({ status: "STALE", last_tick_age_seconds: 42 })).detail).toContain("42s");
    expect(dataSourceLabel(md({ status: "DISCONNECTED" })).live).toBe(false);
    expect(marketDataLabel(md({ status: "STALE" })).variant).toBe("disconnected");
  });

  it("shows execution LIVE only when the backend reports live execution", () => {
    expect(executionLabel(z({})).text).toBe("PAPER");
    expect(executionLabel(undefined).text).toBe("PAPER");
    expect(executionLabel(z({ execution_mode: "ZERODHA" })).text).toContain("LIVE");
  });

  it("labels broker and auth states", () => {
    expect(brokerLabel(z({})).text).toBe("ZERODHA CONNECTED");
    expect(brokerLabel(z({ connected: false })).text).toBe("ZERODHA DISCONNECTED");
    expect(brokerLabel(z({ configured: false })).text).toBe("NOT CONFIGURED");
    expect(authLabel(z({ auth: "TOKEN_EXPIRED" })).text).toBe("EXPIRED");
    expect(authLabel(z({ auth: "LOGIN_REQUIRED", connected: false })).text).toBe("LOGIN REQUIRED");
    expect(candleDot("FORMING")).toBe("bg-profit");
    expect(candleDot(undefined)).toBe("bg-border");
  });
});
