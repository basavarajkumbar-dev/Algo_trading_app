import type { BrokerStatus } from "@/types/broker";
import { apiGet } from "@/lib/api";
import type { ComponentHealthResponse, PerformanceReport, SystemEventList } from "@/types/dashboard";
import type { MarketRegime } from "@/types/intelligence";
import type { RouterDecision } from "@/types/strategies";
import type { SystemHealthResponse } from "@/types/system";

export function getSystemHealthRequest(): Promise<SystemHealthResponse> {
  return apiGet<SystemHealthResponse>("/system/health");
}

export function getComponentHealthRequest(): Promise<ComponentHealthResponse> {
  return apiGet<ComponentHealthResponse>("/system/components");
}

export function getSystemEventsRequest(limit = 30): Promise<SystemEventList> {
  return apiGet<SystemEventList>(`/system/events?limit=${limit}`);
}

export function getPerformanceRequest(): Promise<PerformanceReport> {
  return apiGet<PerformanceReport>("/analytics/performance");
}

export function getRouteRequest(regime: MarketRegime, confidence: number): Promise<RouterDecision> {
  return apiGet<RouterDecision>(`/strategies/route?regime=${regime}&confidence=${confidence}`);
}

export function getBrokerStatusRequest(): Promise<BrokerStatus> {
  return apiGet<BrokerStatus>("/broker/status");
}
