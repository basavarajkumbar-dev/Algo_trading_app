"use client";

import { useEffect, useRef, useState } from "react";

import { WS_BASE_URL } from "@/lib/api";
import type {
  MarketIntelligenceResponse,
  WsMarketRegimeMessage,
} from "@/types/intelligence";
import type { MarketSnapshotResponse, WsMessage } from "@/types/market";

export type SocketStatus = "connecting" | "open" | "closed" | "reconnecting";

const RECONNECT_DELAY_MS = 2000;
const MAX_REGIME_CHANGES = 12;

/**
 * Connects to /ws/market and keeps a live snapshot-per-symbol map
 * updated from `market_tick` / `market_snapshot` messages, plus the
 * latest `market_intelligence` picture and a short feed of
 * `market_regime` changes. Reconnects
 * automatically (with a fixed delay) if the connection drops, and
 * exposes the connection status so the UI can show it honestly rather
 * than silently going stale.
 */
export function useMarketSocket() {
  const [status, setStatus] = useState<SocketStatus>("connecting");
  const [snapshots, setSnapshots] = useState<Record<string, MarketSnapshotResponse>>({});
  const [intelligence, setIntelligence] = useState<MarketIntelligenceResponse | null>(null);
  const [regimeChanges, setRegimeChanges] = useState<WsMarketRegimeMessage[]>([]);
  const socketRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;
      setStatus((prev) => (prev === "closed" ? "reconnecting" : "connecting"));

      const socket = new WebSocket(`${WS_BASE_URL}/ws/market`);
      socketRef.current = socket;

      socket.onopen = () => {
        if (cancelled) return;
        setStatus("open");
      };

      socket.onmessage = (event) => {
        if (cancelled) return;
        let message: WsMessage;
        try {
          message = JSON.parse(event.data);
        } catch {
          return;
        }

        if (message.type === "market_snapshot") {
          setSnapshots((prev) => ({
            ...prev,
            [message.symbol]: {
              symbol: message.symbol,
              price: message.price,
              change: message.change,
              change_percent: message.change_percent,
              volume: message.volume,
              timestamp: message.timestamp,
            },
          }));
        } else if (message.type === "market_tick") {
          setSnapshots((prev) => {
            const existing = prev[message.symbol];
            const openPrice = existing ? existing.price - existing.change : message.price;
            const change = message.price - openPrice;
            const changePercent = openPrice ? (change / openPrice) * 100 : 0;
            return {
              ...prev,
              [message.symbol]: {
                symbol: message.symbol,
                price: message.price,
                change,
                change_percent: changePercent,
                volume: message.volume,
                timestamp: message.timestamp,
              },
            };
          });
        } else if (message.type === "market_intelligence") {
          setIntelligence({ snapshot: message.snapshot, symbols: message.symbols });
        } else if (message.type === "market_regime" && message.changed) {
          setRegimeChanges((prev) => [message, ...prev].slice(0, MAX_REGIME_CHANGES));
        }
      };

      socket.onclose = () => {
        if (cancelled) return;
        setStatus("closed");
        socketRef.current = null;
        reconnectTimer = setTimeout(connect, RECONNECT_DELAY_MS);
      };

      socket.onerror = () => {
        socket.close();
      };
    }

    connect();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      socketRef.current?.close();
    };
  }, []);

  return { status, snapshots, intelligence, regimeChanges };
}
