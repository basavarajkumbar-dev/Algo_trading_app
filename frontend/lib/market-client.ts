import { apiGet } from "@/lib/api";
import type {
  CandleResponse,
  InstrumentResponse,
  MarketSnapshotResponse,
  MarketStatusResponse,
  TimeframeValue,
} from "@/types/market";

export function getMarketStatusRequest(): Promise<MarketStatusResponse> {
  return apiGet<MarketStatusResponse>("/market/status");
}

export function getInstrumentsRequest(): Promise<InstrumentResponse[]> {
  return apiGet<InstrumentResponse[]>("/market/instruments");
}

export function getSnapshotRequest(symbol: string): Promise<MarketSnapshotResponse> {
  return apiGet<MarketSnapshotResponse>(`/market/snapshot/${encodeURIComponent(symbol)}`);
}

export function getCandlesRequest(
  symbol: string,
  timeframe: TimeframeValue,
  limit = 50
): Promise<CandleResponse[]> {
  const params = new URLSearchParams({ symbol, timeframe, limit: String(limit) });
  return apiGet<CandleResponse[]>(`/market/candles?${params.toString()}`);
}
