import { apiGet, apiPost } from "@/lib/api";
import type {
  ExecuteResponse,
  OrderList,
  PaperTrade,
  RiskResponse,
  TradeList,
  TradingStatus,
} from "@/types/trading";

export function getTradingStatusRequest(): Promise<TradingStatus> {
  return apiGet<TradingStatus>("/trading/status");
}

export function getTradingRiskRequest(): Promise<RiskResponse> {
  return apiGet<RiskResponse>("/trading/risk");
}

export function getTradesRequest(params: { status?: string; limit?: number } = {}): Promise<TradeList> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  q.set("limit", String(params.limit ?? 100));
  return apiGet<TradeList>(`/trading/trades?${q.toString()}`);
}

export function getTradeRequest(id: string): Promise<PaperTrade> {
  return apiGet<PaperTrade>(`/trading/trades/${id}`);
}

export function getOrdersRequest(params: { tradeId?: string; limit?: number } = {}): Promise<OrderList> {
  const q = new URLSearchParams();
  if (params.tradeId) q.set("trade_id", params.tradeId);
  q.set("limit", String(params.limit ?? 100));
  return apiGet<OrderList>(`/trading/orders?${q.toString()}`);
}

export function executeSignalRequest(signalId: string, accessToken: string): Promise<ExecuteResponse> {
  return apiPost<ExecuteResponse>(`/trading/signals/${signalId}/execute`, undefined, { accessToken });
}

export function setAutoTradingRequest(enabled: boolean, accessToken: string): Promise<TradingStatus> {
  return apiPost<TradingStatus>("/trading/auto", { enabled }, { accessToken });
}

export function exitPositionRequest(accessToken: string): Promise<TradingStatus> {
  return apiPost<TradingStatus>("/trading/exit", undefined, { accessToken });
}

/**
 * Disable new trading and cancel pending entry orders. An open position
 * keeps its stop and target and is closed only when `closePosition` is
 * true (an explicit request).
 */
export function emergencyStopRequest(
  reason: string,
  accessToken: string,
  closePosition = false
): Promise<TradingStatus> {
  return apiPost<TradingStatus>(
    "/trading/emergency-stop",
    { reason, close_position: closePosition },
    { accessToken }
  );
}

export function resetEmergencyStopRequest(accessToken: string): Promise<TradingStatus> {
  return apiPost<TradingStatus>("/trading/emergency-stop/reset", undefined, { accessToken });
}
