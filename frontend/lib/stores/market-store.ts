import { create } from "zustand";

import type { WsTradingEventMessage } from "@/types/dashboard";
import type { MarketIntelligenceResponse, WsMarketRegimeMessage } from "@/types/intelligence";
import type { MarketSnapshotResponse, WsMessage } from "@/types/market";

export type SocketStatus = "connecting" | "open" | "closed" | "reconnecting";

export const MAX_REGIME_CHANGES = 12;
export const MAX_TRADING_EVENTS = 50;

export interface MarketState {
  status: SocketStatus;
  snapshots: Record<string, MarketSnapshotResponse>;
  intelligence: MarketIntelligenceResponse | null;
  regimeChanges: WsMarketRegimeMessage[];
  tradingEvents: WsTradingEventMessage[];
  lastMessageAt: number | null;
  messagesReceived: number;
}

export const initialMarketState: MarketState = {
  status: "connecting",
  snapshots: {},
  intelligence: null,
  regimeChanges: [],
  tradingEvents: [],
  lastMessageAt: null,
  messagesReceived: 0,
};

/**
 * Pure reducer for one /ws/market message. Kept separate from the store
 * so it can be tested without a socket.
 */
export function applyMarketMessage(state: MarketState, message: WsMessage, now: number): MarketState {
  const base = { ...state, lastMessageAt: now, messagesReceived: state.messagesReceived + 1 };
  switch (message.type) {
    case "market_snapshot":
      return {
        ...base,
        snapshots: {
          ...state.snapshots,
          [message.symbol]: {
            symbol: message.symbol,
            price: message.price,
            change: message.change,
            change_percent: message.change_percent,
            volume: message.volume,
            timestamp: message.timestamp,
          },
        },
      };
    case "market_tick": {
      const existing = state.snapshots[message.symbol];
      // keep the day's open implied by the last snapshot
      const open = existing ? existing.price - existing.change : message.price;
      const change = message.price - open;
      return {
        ...base,
        snapshots: {
          ...state.snapshots,
          [message.symbol]: {
            symbol: message.symbol,
            price: message.price,
            change,
            change_percent: open ? (change / open) * 100 : 0,
            volume: message.volume,
            timestamp: message.timestamp,
          },
        },
      };
    }
    case "market_intelligence":
      return { ...base, intelligence: { snapshot: message.snapshot, symbols: message.symbols } };
    case "market_regime":
      return message.changed
        ? { ...base, regimeChanges: [message, ...state.regimeChanges].slice(0, MAX_REGIME_CHANGES) }
        : base;
    case "trading_event":
      return { ...base, tradingEvents: [message, ...state.tradingEvents].slice(0, MAX_TRADING_EVENTS) };
    default:
      return base;
  }
}

interface MarketStore extends MarketState {
  setStatus: (status: SocketStatus) => void;
  receive: (message: WsMessage) => void;
  reset: () => void;
}

export const useMarketStore = create<MarketStore>((set) => ({
  ...initialMarketState,
  setStatus: (status) => set({ status }),
  receive: (message) => set((state) => applyMarketMessage(state, message, Date.now())),
  reset: () => set(initialMarketState),
}));
