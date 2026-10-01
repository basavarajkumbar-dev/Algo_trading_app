import type { WsMarketIntelligenceMessage, WsMarketRegimeMessage } from "@/types/intelligence";

/**
 * Mirrors backend/app/schemas/market.py and app/market_data/schemas.py.
 * Hand-maintained mirror, same rationale as types/system.ts.
 */

export type ProviderStatus = "DISCONNECTED" | "CONNECTING" | "CONNECTED" | "ERROR";

export type TimeframeValue = "1m" | "3m" | "5m" | "15m" | "30m" | "60m";

export interface InstrumentResponse {
  symbol: string;
  name: string;
  exchange: string;
  sector: string | null;
  is_active: boolean;
}

export interface MarketSnapshotResponse {
  symbol: string;
  price: number;
  change: number;
  change_percent: number;
  volume: number;
  timestamp: string;
}

export interface CandleResponse {
  symbol: string;
  timeframe: TimeframeValue;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  is_closed: boolean;
}

export interface MarketStatusResponse {
  provider_status: ProviderStatus;
  is_streaming: boolean;
  watchlist: string[];
  connected_websocket_clients: number;
  ticks_received: number;
  ticks_accepted: number;
  ticks_rejected: number;
  rejected_by_issue: Record<string, number>;
  started_at: string | null;
  notes: string[];
}

/** Inbound WebSocket message shapes from /api/v1/ws/market. */
export interface WsConnectedMessage {
  type: "connected";
  message: string;
  watchlist: string[];
}

export interface WsMarketSnapshotMessage {
  type: "market_snapshot";
  symbol: string;
  price: number;
  change: number;
  change_percent: number;
  volume: number;
  timestamp: string;
}

export interface WsMarketTickMessage {
  type: "market_tick";
  symbol: string;
  price: number;
  volume: number;
  timestamp: string;
}

export interface WsCandleClosedMessage {
  type: "candle_closed";
  symbol: string;
  timeframe: TimeframeValue;
  timestamp: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type WsMessage =
  | WsConnectedMessage
  | WsMarketSnapshotMessage
  | WsMarketTickMessage
  | WsCandleClosedMessage
  | WsMarketIntelligenceMessage
  | WsMarketRegimeMessage;
