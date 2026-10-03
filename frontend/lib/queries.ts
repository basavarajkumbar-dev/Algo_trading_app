"use client";

import { useQuery } from "@tanstack/react-query";

import {
  getBacktestEquityRequest,
  getBacktestOptionsRequest,
  getBacktestRunRequest,
  getBacktestRunsRequest,
  getBacktestTradesRequest,
} from "@/lib/backtest-client";
import {
  getBrokerStatusRequest,
  getComponentHealthRequest,
  getPerformanceRequest,
  getRouteRequest,
  getSystemEventsRequest,
  getSystemHealthRequest,
} from "@/lib/dashboard-client";
import {
  getMarketIntelligenceRequest,
  getScannerRequest,
  getSignalRequest,
  getSignalsRequest,
  getSymbolIntelligenceRequest,
} from "@/lib/market-client";
import { qk } from "@/lib/query-keys";
import {
  getOrdersRequest,
  getTradesRequest,
  getTradingRiskRequest,
  getTradingStatusRequest,
} from "@/lib/trading-client";
import type { MarketRegime } from "@/types/intelligence";

// Polling intervals are fallbacks: trading events arriving over the
// WebSocket invalidate the trading queries immediately (SocketBridge).

export const useTradingStatus = () =>
  useQuery({ queryKey: qk.tradingStatus, queryFn: getTradingStatusRequest, refetchInterval: 3_000 });

export const useTradingRisk = () =>
  useQuery({ queryKey: qk.tradingRisk, queryFn: getTradingRiskRequest, refetchInterval: 5_000 });

export const useTrades = (limit = 200) =>
  useQuery({ queryKey: qk.trades(limit), queryFn: () => getTradesRequest({ limit }), refetchInterval: 10_000 });

export const useOrders = (tradeId: string | null) =>
  useQuery({
    queryKey: qk.orders(tradeId),
    queryFn: () => getOrdersRequest({ tradeId: tradeId ?? undefined }),
    enabled: tradeId !== null,
  });

export const useScanner = () =>
  useQuery({ queryKey: qk.scanner, queryFn: getScannerRequest, refetchInterval: 15_000 });

export const useSignals = (params: { status?: string; category?: string; limit?: number } = {}) =>
  useQuery({
    queryKey: qk.signals(params),
    queryFn: () => getSignalsRequest(params),
    refetchInterval: 15_000,
  });

export const useSignal = (id: string | null) =>
  useQuery({ queryKey: qk.signal(id ?? ""), queryFn: () => getSignalRequest(id as string), enabled: !!id });

export const useMarketIntelligence = () =>
  useQuery({ queryKey: qk.intelligence, queryFn: getMarketIntelligenceRequest, refetchInterval: 30_000 });

export const useSymbolIntelligence = (symbol: string | null) =>
  useQuery({
    queryKey: qk.symbolIntelligence(symbol ?? ""),
    queryFn: () => getSymbolIntelligenceRequest(symbol as string),
    enabled: !!symbol,
    refetchInterval: 15_000,
  });

export const useRoute = (regime: MarketRegime | undefined, confidence: number | undefined) =>
  useQuery({
    queryKey: qk.route(regime ?? "", Math.round(confidence ?? 0)),
    queryFn: () => getRouteRequest(regime as MarketRegime, Math.round(confidence ?? 0)),
    enabled: !!regime,
    staleTime: 60_000,
  });

export const usePerformance = () =>
  useQuery({ queryKey: qk.performance, queryFn: getPerformanceRequest, refetchInterval: 30_000 });

export const useComponentHealth = () =>
  useQuery({ queryKey: qk.components, queryFn: getComponentHealthRequest, refetchInterval: 10_000 });

export const useSystemEvents = () =>
  useQuery({ queryKey: qk.events, queryFn: () => getSystemEventsRequest(30), refetchInterval: 30_000 });

export const useSystemHealth = () =>
  useQuery({ queryKey: qk.systemHealth, queryFn: getSystemHealthRequest, refetchInterval: 15_000 });

// --- backtesting -------------------------------------------------------------

const ACTIVE = new Set(["QUEUED", "RUNNING"]);

export const useBacktestOptions = () =>
  useQuery({ queryKey: qk.backtestOptions, queryFn: getBacktestOptionsRequest, staleTime: 60_000 });

/** Polls quickly while any run is queued or running, slowly otherwise. */
export const useBacktestRuns = () =>
  useQuery({
    queryKey: qk.backtestRuns,
    queryFn: () => getBacktestRunsRequest(),
    refetchInterval: (q) => (q.state.data?.items.some((r) => ACTIVE.has(r.status)) ? 1_500 : 15_000),
  });

export const useBacktestRun = (id: string | null) =>
  useQuery({
    queryKey: qk.backtestRun(id ?? ""),
    queryFn: () => getBacktestRunRequest(id as string),
    enabled: !!id,
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 1_500 : false),
  });

export const useBacktestTrades = (id: string | null, completed: boolean) =>
  useQuery({
    queryKey: qk.backtestTrades(id ?? ""),
    queryFn: () => getBacktestTradesRequest(id as string),
    enabled: !!id && completed,
    staleTime: Infinity,
  });

export const useBacktestEquity = (id: string | null, completed: boolean) =>
  useQuery({
    queryKey: qk.backtestEquity(id ?? ""),
    queryFn: () => getBacktestEquityRequest(id as string),
    enabled: !!id && completed,
    staleTime: Infinity,
  });

export const useBrokerStatus = () =>
  useQuery({ queryKey: qk.brokerStatus, queryFn: getBrokerStatusRequest, refetchInterval: 10_000 });
