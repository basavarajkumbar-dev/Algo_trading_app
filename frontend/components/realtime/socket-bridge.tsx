"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";

import { WS_BASE_URL } from "@/lib/api";
import { getStoredTokens } from "@/lib/auth-storage";
import { TRADING_EVENT_INVALIDATES, qk } from "@/lib/query-keys";
import { useMarketStore } from "@/lib/stores/market-store";
import type { WsMessage } from "@/types/market";

const RECONNECT_DELAY_MS = 2_000;

/**
 * The dashboard's one WebSocket connection to /ws/market.
 *
 * Prices, intelligence and regime changes go into the Zustand market
 * store; a market_intelligence push also refreshes the TanStack Query
 * cache, and every trading_event (order placed, position opened, trade
 * closed, risk rejection, emergency stop) invalidates the trading,
 * analytics and system queries so every panel updates at once.
 * Reconnects after a drop and reports its status honestly.
 */
export function SocketBridge() {
  const queryClient = useQueryClient();

  useEffect(() => {
    const { setStatus, receive } = useMarketStore.getState();
    let cancelled = false;
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;

    function connect() {
      if (cancelled) return;
      setStatus(useMarketStore.getState().status === "closed" ? "reconnecting" : "connecting");
      socket = new WebSocket(`${WS_BASE_URL}/ws/market`);
      socket.onopen = () => {
        if (cancelled) return;
        // When the backend requires sign-in the first message carries the
        // token (never the URL, which servers log); it is ignored otherwise.
        const token = getStoredTokens().accessToken;
        if (token) socket?.send(JSON.stringify({ type: "auth", token }));
        setStatus("open");
      };
      socket.onmessage = (event) => {
        if (cancelled) return;
        let message: WsMessage;
        try {
          message = JSON.parse(event.data as string) as WsMessage;
        } catch {
          return;
        }
        receive(message);
        if (message.type === "market_intelligence") {
          queryClient.setQueryData(qk.intelligence, (old: object | undefined) => ({
            ...(old ?? {}),
            snapshot: message.snapshot,
            symbols: message.symbols,
          }));
        } else if (message.type === "trading_event") {
          for (const key of TRADING_EVENT_INVALIDATES) {
            void queryClient.invalidateQueries({ queryKey: [...key] });
          }
        }
      };
      socket.onclose = () => {
        if (cancelled) return;
        setStatus("closed");
        timer = setTimeout(connect, RECONNECT_DELAY_MS);
      };
      socket.onerror = () => socket?.close();
    }

    connect();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      socket?.close();
    };
  }, [queryClient]);

  return null;
}
