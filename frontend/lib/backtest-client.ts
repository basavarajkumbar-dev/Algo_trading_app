import { apiDelete, apiGet, apiPost } from "@/lib/api";
import type {
  BacktestEquity,
  BacktestOptions,
  BacktestRequest,
  BacktestTradeList,
  RunDetail,
  RunList,
  RunSummary,
} from "@/types/backtest";

export const getBacktestOptionsRequest = () => apiGet<BacktestOptions>("/backtests/options");

export const getBacktestRunsRequest = (limit = 50) => apiGet<RunList>(`/backtests?limit=${limit}`);

export const getBacktestRunRequest = (id: string) => apiGet<RunDetail>(`/backtests/${id}`);

export const getBacktestTradesRequest = (id: string, limit = 2000) =>
  apiGet<BacktestTradeList>(`/backtests/${id}/trades?limit=${limit}`);

export const getBacktestEquityRequest = (id: string) => apiGet<BacktestEquity>(`/backtests/${id}/equity`);

export const createBacktestRequest = (body: BacktestRequest, accessToken: string) =>
  apiPost<RunSummary>("/backtests", body, { accessToken });

export const cancelBacktestRequest = (id: string, accessToken: string) =>
  apiPost<RunSummary>(`/backtests/${id}/cancel`, undefined, { accessToken });

export const deleteBacktestRequest = (id: string, accessToken: string) =>
  apiDelete<void>(`/backtests/${id}`, { accessToken });
