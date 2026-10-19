/** Typed calls to the existing backend REST API (types generated from its
 * OpenAPI schema: npm run gen:api). The app adds no business logic: every
 * number shown comes from these responses. */

import type { components } from "./schema";
import { del, get, patch, post, put } from "./client";

export type Schemas = components["schemas"];
export type TradingStatus = Schemas["TradingStatus"];
export type PaperTrade = Schemas["PaperTrade"];
export type TradeCandidate = Schemas["TradeCandidate"];
export type TradingSignal = Schemas["TradingSignal"];
export type StoredSignal = Schemas["StoredSignal"];
export type RunSummary = Schemas["RunSummary"];
export type UniverseReport = Schemas["UniverseReport"];
export type UserPublic = Schemas["UserPublic"];
export type TokenResponse = Schemas["TokenResponse"];

type Json = Record<string, unknown>;

/** /broker/zerodha/status: what the app may see (no token, secret or request token). */
export interface ZerodhaStatus {
  configured: boolean;
  connected: boolean;
  auth: string;
  user_id: string | null;
  user_name: string | null;
  login_time: string | null;
  expires_at: string | null;
  last_error: string | null;
  market_data: string;
  execution_mode: string;
}

/** Backend-reported states of the user's own Zerodha connection. */
export type BrokerConnectionStatus =
  | "NOT_CONFIGURED"
  | "CREDENTIALS_SAVED"
  | "NOT_CONNECTED"
  | "AWAITING_LOGIN"
  | "CONNECTED"
  | "SESSION_EXPIRED"
  | "CONNECTION_FAILED";

/** /broker/zerodha/credentials: never contains the API secret, the full API
 * key or any token — only a masked key hint and "Configured". */
export interface BrokerCredentialStatus {
  broker: string;
  status: BrokerConnectionStatus;
  status_label: string;
  detail: string | null;
  configured: boolean;
  credentials_source: "USER" | "ENVIRONMENT";
  api_key_hint: string | null;
  api_secret: "Configured" | null;
  active_on_server: boolean;
  connected: boolean;
  user_id: string | null;
  user_name: string | null;
  session_expires_at: string | null;
  last_validated_at: string | null;
  last_connected_at: string | null;
  updated_at: string | null;
  storage: "ENCRYPTED" | "UNAVAILABLE";
  storage_problem: string | null;
  redirect_url_configured: boolean;
  paper_trading_available: boolean;
  live_trading_note: string;
  market_data: string;
  market_data_note: string | null;
}

export interface CredentialTestResult {
  ok: boolean;
  verified: boolean;
  message: string;
  status: BrokerCredentialStatus;
}

export interface LiveStatus {
  configured: boolean;
  execution_mode: string;
  arming: { armed: boolean; expires_at: string | null };
  kill_switch: { engaged: boolean };
  auto_trading: boolean;
}

export interface UniverseInfo {
  id: string;
  name: string;
  group: string;
  notes: string;
  kind: string;
  cached: boolean;
  size: number | null;
  source: string | null;
  fetched_at: string | null;
  stale: boolean | null;
}

export const api = {
  // auth
  login: (email: string, password: string) =>
    post<TokenResponse>("/auth/login", { email, password }),
  register: (email: string, password: string, full_name?: string) =>
    post<TokenResponse>("/auth/register", { email, password, full_name }),
  me: () => get<UserPublic>("/auth/me"),
  logout: (refresh_token: string) => post<void>("/auth/logout", { refresh_token }),

  // system
  health: () => get<Schemas["SystemHealthResponse"]>("/system/health"),
  components: () => get<Schemas["ComponentHealthResponse"]>("/system/components"),
  events: (limit = 50) => get<Schemas["SystemEventList"]>(`/system/events?limit=${limit}`),
  marketDataHealth: () => get<Json>("/system/market-data"),

  // market
  marketStatus: () => get<Schemas["MarketStatusResponse"]>("/market/status"),
  intelligence: () => get<Schemas["MarketIntelligenceResponse"]>("/market/intelligence"),
  regime: () => get<Schemas["RegimeResult"]>("/market/regime"),
  candles: (symbol: string, timeframe = "5m", limit = 60) =>
    get<Schemas["CandleResponse"][]>(
      `/market/candles?symbol=${encodeURIComponent(symbol)}&timeframe=${timeframe}&limit=${limit}`,
    ),

  // universes
  universes: () => get<{ universes: UniverseInfo[]; active: Json | null; note: string }>("/universes"),
  activeUniverse: () => get<UniverseReport>("/universes/active"),
  resolveUniverse: (universes: string[], limit?: number) =>
    post<UniverseReport>("/universes/resolve", { universes, limit }),

  // scanner & signals
  scanner: () => get<Schemas["ScannerResponse"]>("/scanner"),
  signals: (limit = 50) => get<Schemas["SignalList"]>(`/signals?limit=${limit}`),
  signal: (id: string) => get<StoredSignal>(`/signals/${id}`),

  // strategies
  strategies: () => get<Schemas["StrategyOverview"]>("/strategies"),
  strategyConfig: () => get<Schemas["StrategyEngineConfig"]>("/strategies/config"),
  signalConfig: () => get<Schemas["SignalEngineConfig"]>("/signals/config"),

  // paper trading (server-side risk checks apply to every action)
  tradingStatus: () => get<TradingStatus>("/trading/status"),
  positions: () => get<Schemas["PositionsResponse"]>("/trading/positions"),
  orders: (limit = 50) => get<Schemas["OrderList"]>(`/trading/orders?limit=${limit}`),
  trades: (limit = 100) =>
    get<Schemas["app__api__v1__trading__TradeList"]>(`/trading/trades?limit=${limit}`),
  risk: () => get<Schemas["RiskResponse"]>("/trading/risk"),
  executeSignal: (id: string) =>
    post<Schemas["ExecuteResponse"]>(`/trading/signals/${id}/execute`),
  setAuto: (enabled: boolean) => post<TradingStatus>("/trading/auto", { enabled }),
  setMode: (mode: "PAPER" | "APPROVAL") => post<TradingStatus>("/trading/mode", { mode }),
  exitPosition: () => post<TradingStatus>("/trading/exit"),
  emergencyStop: (reason: string) => post<TradingStatus>("/trading/emergency-stop", { reason }),
  resetEmergency: () => post<TradingStatus>("/trading/emergency-stop/reset"),

  // approvals
  approvals: () => get<Schemas["ApprovalList"]>("/approvals"),
  approve: (id: string) => post<Schemas["ApprovalView"]>(`/approvals/${id}/approve`),
  reject: (id: string) => post<Schemas["ApprovalView"]>(`/approvals/${id}/reject`),

  // analytics & backtests
  performance: () => get<Schemas["PerformanceReport"]>("/analytics/performance"),
  backtests: (limit = 30) => get<Schemas["RunList"]>(`/backtests?limit=${limit}`),
  backtest: (id: string) => get<Schemas["RunDetail"]>(`/backtests/${id}`),
  backtestOptions: () => get<Schemas["BacktestOptions"]>("/backtests/options"),
  createBacktest: (body: Partial<Schemas["BacktestRequest"]>) =>
    post<RunSummary>("/backtests", body),

  // settings
  settings: () => get<Schemas["TradingSettingsResponse"]>("/settings/me"),
  updateSettings: (body: Json) => patch<Schemas["TradingSettingsResponse"]>("/settings/me", body),

  // broker / Zerodha (the API secret and tokens never leave the backend)
  brokerStatus: () => get<Json>("/broker/status"),
  zerodhaStatus: () => get<ZerodhaStatus>("/broker/zerodha/status"),
  zerodhaLoginUrl: () => get<{ login_url: string }>("/broker/zerodha/login?client=mobile"),
  zerodhaLogout: () => post<Json>("/broker/zerodha/logout"),

  // the user's own Kite Connect credentials (sent once, over HTTPS, to the
  // backend, which encrypts them; they are never read back)
  zerodhaCredentials: () => get<BrokerCredentialStatus>("/broker/zerodha/credentials"),
  saveZerodhaCredentials: (apiKey: string, apiSecret: string) =>
    put<BrokerCredentialStatus>("/broker/zerodha/credentials", {
      api_key: apiKey,
      api_secret: apiSecret,
    }),
  removeZerodhaCredentials: () => del<BrokerCredentialStatus>("/broker/zerodha/credentials"),
  testZerodhaCredentials: () => post<CredentialTestResult>("/broker/zerodha/credentials/test"),
  connectZerodha: () =>
    post<{ login_url: string; status: BrokerCredentialStatus }>(
      "/broker/zerodha/credentials/connect?client=mobile",
    ),
  disconnectZerodha: () => post<BrokerCredentialStatus>("/broker/zerodha/credentials/disconnect"),
  liveStatus: () => get<LiveStatus>("/live/status"),
};
