import { apiGet, apiPost } from "@/lib/api";
import type { BrokerHealthReport, MarketDataStatus, ZerodhaStatus } from "@/types/market-data";

export const getMarketDataStatusRequest = () => apiGet<MarketDataStatus>("/system/market-data");

export const getZerodhaStatusRequest = () => apiGet<ZerodhaStatus>("/broker/zerodha/status");

export const getBrokerHealthRequest = () => apiGet<BrokerHealthReport>("/system/broker-health");

/** ADMIN: the Kite login URL (with a one-time state). The browser then
 * navigates there; Kite redirects back to the backend callback, which
 * exchanges the request token server-side. No token reaches this app. */
export const getZerodhaLoginUrlRequest = (accessToken: string) =>
  apiGet<{ login_url: string }>("/broker/zerodha/login", { accessToken });

export const zerodhaLogoutRequest = (accessToken: string) =>
  apiPost<ZerodhaStatus>("/broker/zerodha/logout", undefined, { accessToken });
