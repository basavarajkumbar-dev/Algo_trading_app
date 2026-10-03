/** Phase 13: market data feed and Zerodha connection (no credentials ever). */

export type DataStatus = "LIVE" | "STALE" | "WAITING" | "DISCONNECTED";
export type CandleState = "FORMING" | "CLOSED" | "NONE";

export interface MarketDataStatus {
  provider: "MOCK" | "ZERODHA" | string;
  mode: "SIMULATED" | "ZERODHA_LIVE";
  simulated: boolean;
  status: DataStatus;
  websocket: string;
  feed_state: string;
  rest_api: string;
  last_tick: string | null;
  last_tick_age_seconds: number | null;
  latency_ms: number | null;
  subscribed_instruments: number;
  unresolved_symbols: string[];
  stale: boolean;
  stale_threshold_seconds: number;
  ticks_accepted: number;
  ticks_rejected: number;
  rejected_by_issue: Record<string, number>;
  warmup_in_progress: boolean;
  last_warmup_at: string | null;
  candle_status_symbol: string | null;
  candles: Record<string, CandleState>;
}

export interface ZerodhaStatus {
  broker: "ZERODHA";
  configured: boolean;
  connected: boolean;
  auth: string;
  user_id: string | null;
  user_name: string | null;
  login_time: string | null;
  expires_at: string | null;
  last_error: string | null;
  redirect_url_configured: boolean;
  market_data: "CONNECTED" | "STALE" | "DISCONNECTED" | "SIMULATED";
  execution_mode: "PAPER" | "ZERODHA";
}

export interface BrokerHealthCheck {
  name: string;
  ok: boolean;
  status: string;
  detail: string;
}

export interface BrokerHealthReport {
  status: "HEALTHY" | "DEGRADED" | "DISCONNECTED" | "AUTH_REQUIRED" | "ERROR";
  broker: string;
  configured: boolean;
  market_data_source: string;
  execution_mode: string;
  checked_at: string;
  checks: BrokerHealthCheck[];
}
