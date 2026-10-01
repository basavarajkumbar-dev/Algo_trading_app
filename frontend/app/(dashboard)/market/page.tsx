"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import type { BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getMarketStatusRequest } from "@/lib/market-client";
import { useMarketSocket, type SocketStatus } from "@/lib/use-market-socket";
import type { MarketStatusResponse } from "@/types/market";

const NIFTY_INDEX_SYMBOL = "NIFTY50";

const SOCKET_BADGE_VARIANT: Record<SocketStatus, BadgeProps["variant"]> = {
  open: "connected",
  connecting: "pending",
  reconnecting: "pending",
  closed: "disconnected",
};

const SOCKET_LABEL: Record<SocketStatus, string> = {
  open: "Live",
  connecting: "Connecting…",
  reconnecting: "Reconnecting…",
  closed: "Disconnected",
};

export default function MarketPage() {
  const { status: socketStatus, snapshots } = useMarketSocket();
  const [marketStatus, setMarketStatus] = useState<MarketStatusResponse | null>(null);
  const [statusError, setStatusError] = useState(false);

  useEffect(() => {
    let cancelled = false;

    function poll() {
      getMarketStatusRequest()
        .then((data) => {
          if (!cancelled) {
            setMarketStatus(data);
            setStatusError(false);
          }
        })
        .catch(() => {
          if (!cancelled) setStatusError(true);
        });
    }

    poll();
    const interval = setInterval(poll, 5000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const nifty = snapshots[NIFTY_INDEX_SYMBOL];
  const watchlist = marketStatus?.watchlist ?? [];
  const stockSymbols = watchlist.filter((s) => s !== NIFTY_INDEX_SYMBOL);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Market</h1>
          <p className="mt-1 text-sm text-muted">
            Simulated NIFTY 50 market data — no live feed or broker is connected.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-muted">Data connection</span>
          <Badge variant={SOCKET_BADGE_VARIANT[socketStatus]}>{SOCKET_LABEL[socketStatus]}</Badge>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-1">
          <CardHeader>
            <CardTitle>NIFTY 50 Index</CardTitle>
          </CardHeader>
          <CardContent>
            {nifty ? (
              <div className="flex flex-col gap-2">
                <span className="tabular text-3xl font-semibold text-foreground">
                  {nifty.price.toLocaleString(undefined, { maximumFractionDigits: 2 })}
                </span>
                <ChangeBadge change={nifty.change} changePercent={nifty.change_percent} />
                <span className="tabular text-xs text-muted">
                  Volume: {nifty.volume.toLocaleString()}
                </span>
              </div>
            ) : (
              <p className="text-sm text-muted">Waiting for the first tick…</p>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Market Status</CardTitle>
          </CardHeader>
          <CardContent>
            {statusError && (
              <p className="text-sm text-loss">Could not reach the backend market status API.</p>
            )}
            {!statusError && !marketStatus && <p className="text-sm text-muted">Loading…</p>}
            {marketStatus && (
              <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3">
                <Stat label="Provider">
                  <Badge variant={marketStatus.provider_status === "CONNECTED" ? "connected" : "disconnected"}>
                    {marketStatus.provider_status}
                  </Badge>
                </Stat>
                <Stat label="Streaming">
                  <Badge variant={marketStatus.is_streaming ? "connected" : "disconnected"}>
                    {marketStatus.is_streaming ? "Yes" : "No"}
                  </Badge>
                </Stat>
                <Stat label="WS clients">
                  <span className="tabular">{marketStatus.connected_websocket_clients}</span>
                </Stat>
                <Stat label="Ticks accepted">
                  <span className="tabular">{marketStatus.ticks_accepted.toLocaleString()}</span>
                </Stat>
                <Stat label="Ticks rejected">
                  <span className="tabular">{marketStatus.ticks_rejected.toLocaleString()}</span>
                </Stat>
                <Stat label="Watchlist size">
                  <span className="tabular">{watchlist.length}</span>
                </Stat>
              </div>
            )}
            {marketStatus && marketStatus.notes.length > 0 && (
              <ul className="mt-4 flex flex-col gap-1 border-t border-border pt-3 text-xs text-muted">
                {marketStatus.notes.map((note) => (
                  <li key={note}>· {note}</li>
                ))}
              </ul>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Watchlist</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-xs uppercase tracking-wide text-muted">
                <th className="px-4 py-2 font-medium">Symbol</th>
                <th className="px-4 py-2 font-medium">Price</th>
                <th className="px-4 py-2 font-medium">Change</th>
                <th className="px-4 py-2 font-medium">Volume</th>
              </tr>
            </thead>
            <tbody>
              {stockSymbols.length === 0 && (
                <tr>
                  <td colSpan={4} className="px-4 py-6 text-center text-muted">
                    Loading watchlist…
                  </td>
                </tr>
              )}
              {stockSymbols.map((symbol) => {
                const snapshot = snapshots[symbol];
                return (
                  <tr key={symbol} className="border-b border-border last:border-0">
                    <td className="px-4 py-2 font-medium">{symbol}</td>
                    <td className="tabular px-4 py-2">
                      {snapshot ? snapshot.price.toLocaleString(undefined, { maximumFractionDigits: 2 }) : "—"}
                    </td>
                    <td className="px-4 py-2">
                      {snapshot ? (
                        <ChangeBadge change={snapshot.change} changePercent={snapshot.change_percent} />
                      ) : (
                        <span className="text-muted">—</span>
                      )}
                    </td>
                    <td className="tabular px-4 py-2 text-muted">
                      {snapshot ? snapshot.volume.toLocaleString() : "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </CardContent>
      </Card>
    </div>
  );
}

function ChangeBadge({ change, changePercent }: { change: number; changePercent: number }) {
  const isUp = change >= 0;
  return (
    <Badge variant={isUp ? "connected" : "disconnected"}>
      {isUp ? "+" : ""}
      {change.toFixed(2)} ({isUp ? "+" : ""}
      {changePercent.toFixed(2)}%)
    </Badge>
  );
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
      {children}
    </div>
  );
}
