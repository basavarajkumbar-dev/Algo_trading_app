import { apiGet } from "@/lib/api";
import type {
  MarketIntelligenceResponse,
  RegimeResult,
  SymbolIntelligence,
} from "@/types/intelligence";
import type { PriceActionOverview, SymbolPriceAction } from "@/types/price-action";
import type {
  ScannerResponse,
  SignalEngineConfig,
  SignalList,
  StoredSignal,
} from "@/types/signals";
import type {
  StrategyEngineConfig,
  StrategyOverview,
  SymbolStrategies,
} from "@/types/strategies";
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

export function getMarketIntelligenceRequest(): Promise<MarketIntelligenceResponse> {
  return apiGet<MarketIntelligenceResponse>("/market/intelligence");
}

export function getSymbolIntelligenceRequest(symbol: string): Promise<SymbolIntelligence> {
  return apiGet<SymbolIntelligence>(`/market/intelligence/${encodeURIComponent(symbol)}`);
}

export function getRegimeRequest(symbol?: string): Promise<RegimeResult> {
  const query = symbol ? `?symbol=${encodeURIComponent(symbol)}` : "";
  return apiGet<RegimeResult>(`/market/regime${query}`);
}

export function getPriceActionOverviewRequest(): Promise<PriceActionOverview> {
  return apiGet<PriceActionOverview>("/market/price-action");
}

export function getSymbolPriceActionRequest(symbol: string): Promise<SymbolPriceAction> {
  return apiGet<SymbolPriceAction>(`/market/price-action/${encodeURIComponent(symbol)}`);
}

export function getStrategiesRequest(): Promise<StrategyOverview> {
  return apiGet<StrategyOverview>("/strategies");
}

export function getStrategyConfigRequest(): Promise<StrategyEngineConfig> {
  return apiGet<StrategyEngineConfig>("/strategies/config");
}

export function getSymbolStrategiesRequest(
  symbol: string,
  includeUnrouted = false
): Promise<SymbolStrategies> {
  const query = includeUnrouted ? "?include_unrouted=true" : "";
  return apiGet<SymbolStrategies>(`/strategies/${encodeURIComponent(symbol)}${query}`);
}

export function getScannerRequest(): Promise<ScannerResponse> {
  return apiGet<ScannerResponse>("/scanner");
}

export function getSignalConfigRequest(): Promise<SignalEngineConfig> {
  return apiGet<SignalEngineConfig>("/signals/config");
}

export function getSignalsRequest(
  filters: { status?: string; category?: string; symbol?: string; limit?: number; offset?: number } = {}
): Promise<SignalList> {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const query = params.toString();
  return apiGet<SignalList>(`/signals${query ? `?${query}` : ""}`);
}

export function getSignalRequest(id: string): Promise<StoredSignal> {
  return apiGet<StoredSignal>(`/signals/${encodeURIComponent(id)}`);
}
