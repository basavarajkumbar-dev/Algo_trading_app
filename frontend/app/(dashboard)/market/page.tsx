"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { BreadthPanel } from "@/components/market/breadth-panel";
import { FactorList } from "@/components/market/factor-list";
import { RegimeBadge } from "@/components/market/regime-badge";
import { RegimeExplainer } from "@/components/market/regime-explainer";
import { RegimeTable } from "@/components/market/regime-table";
import { SymbolDetail } from "@/components/market/symbol-detail";
import { Badge } from "@/components/ui/badge";
import type { BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  changeTone,
  formatIstTime,
  formatPercent,
  formatPrice,
  formatScore,
  humanize,
} from "@/lib/intelligence-format";
import { getMarketIntelligenceRequest, getMarketStatusRequest } from "@/lib/market-client";
import { useMarketSocket, type SocketStatus } from "@/lib/use-market-socket";
import { cn } from "@/lib/utils";
import type { IntelligenceServiceStatus, MarketIntelligenceResponse } from "@/types/intelligence";
import type { MarketStatusResponse } from "@/types/market";

const SOCKET_BADGE_VARIANT: Record<SocketStatus, BadgeProps["variant"]> = {
  open: "connected",
  connecting: "pending",
  reconnecting: "pending",
  closed: "disconnected",
};

const SOCKET_LABEL: Record<SocketStatus, string> = {
  open: "Streaming",
  connecting: "Connecting…",
  reconnecting: "Reconnecting…",
  closed: "Disconnected",
};

const POLL_MS = 15_000;

export default function MarketIntelligencePage() {
  const { status: socketStatus, snapshots, intelligence: liveIntelligence, regimeChanges } =
    useMarketSocket();
  const [polled, setPolled] = useState<MarketIntelligenceResponse | null>(null);
  const [serviceStatus, setServiceStatus] = useState<IntelligenceServiceStatus | null>(null);
  const [marketStatus, setMarketStatus] = useState<MarketStatusResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);

  // REST polling: initial load, status details the socket doesn't carry,
  // and a fallback if the socket is down.
  useEffect(() => {
    let cancelled = false;
    function poll() {
      getMarketIntelligenceRequest()
        .then((data) => {
          if (cancelled) return;
          setPolled(data);
          setServiceStatus(data.status ?? null);
          setLoadError(null);
        })
        .catch((err: unknown) => {
          if (!cancelled) setLoadError(err instanceof Error ? err.message : "Request failed");
        });
      getMarketStatusRequest()
        .then((data) => !cancelled && setMarketStatus(data))
        .catch(() => undefined);
    }
    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  // Prefer whichever picture is newer.
  const intelligence = newest(liveIntelligence, polled);
  const snapshot = intelligence?.snapshot ?? null;
  const symbols = intelligence?.symbols ?? [];
  const benchmark = snapshot?.benchmark_symbol ?? "NIFTY50";
  const benchmarkTick = snapshots[benchmark];
  const benchmarkSummary = symbols.find((s) => s.symbol === benchmark);
  const selectedSymbol = selected ?? benchmark;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">Market Intelligence</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            Regime and factor analysis of simulated NIFTY 50 data. No live feed or broker is
            connected. A regime describes recent behaviour; it is not a forecast.
          </p>
        </div>
        <div className="flex shrink-0 flex-col gap-1.5 md:items-end">
          <Badge variant={SOCKET_BADGE_VARIANT[socketStatus]}>{SOCKET_LABEL[socketStatus]}</Badge>
          <span className="text-[11px] text-muted">
            Analysis: {snapshot?.timeframe ?? "5m"} closed candles · updated{" "}
            {formatIstTime(snapshot?.timestamp, true)}
          </span>
        </div>
      </div>

      {loadError && !intelligence && (
        <Card>
          <CardContent className="py-6 text-sm text-loss">
            Could not load market intelligence: {loadError}
          </CardContent>
        </Card>
      )}

      {!loadError && !intelligence && (
        <Card>
          <CardContent className="py-6 text-sm text-muted">Waiting for the first analysis…</CardContent>
        </Card>
      )}

      {snapshot && (
        <>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>NIFTY 50</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-4">
                <div className="flex flex-col gap-1">
                  <span className="tabular text-3xl font-semibold">
                    {formatPrice(benchmarkTick?.price ?? benchmarkSummary?.price)}
                  </span>
                  <span
                    className={cn(
                      "tabular text-sm",
                      changeTone(benchmarkTick?.change_percent ?? benchmarkSummary?.change_percent)
                    )}
                  >
                    {formatPercent(benchmarkTick?.change_percent ?? benchmarkSummary?.change_percent)}{" "}
                    <span className="text-xs text-muted">vs simulated open</span>
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 text-sm">
                  <Stat label="Regime">
                    <RegimeBadge regime={snapshot.market_regime} className="w-fit" />
                  </Stat>
                  <Stat label="Direction">
                    <span
                      className={cn(
                        snapshot.market_direction === "UP" && "text-profit",
                        snapshot.market_direction === "DOWN" && "text-loss"
                      )}
                    >
                      {humanize(snapshot.market_direction)}
                    </span>
                  </Stat>
                  <Stat label="Volatility">{humanize(snapshot.volatility)}</Stat>
                  <Stat label="Trend strength">
                    <span className="tabular">{formatScore(snapshot.trend_strength)}</span>
                  </Stat>
                  <Stat label="Momentum">
                    <span className="tabular">{formatScore(snapshot.momentum)}</span>
                  </Stat>
                  <Stat label="Confidence">
                    <span className="tabular">{formatScore(snapshot.confidence)}%</span>
                  </Stat>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Why this regime</CardTitle>
              </CardHeader>
              <CardContent>
                <RegimeExplainer result={snapshot.regime} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Breadth</CardTitle>
              </CardHeader>
              <CardContent>
                <BreadthPanel breadth={snapshot.breadth} distribution={snapshot.regime_distribution} />
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Index factor scores</CardTitle>
              </CardHeader>
              <CardContent>
                <FactorList factors={snapshot.factors} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Recent regime changes</CardTitle>
              </CardHeader>
              <CardContent>
                {regimeChanges.length === 0 ? (
                  <p className="text-sm text-muted">
                    None since this page opened. Regimes are re-evaluated when a candle closes.
                  </p>
                ) : (
                  <ul className="flex flex-col gap-2 text-sm">
                    {regimeChanges.map((change) => (
                      <li
                        key={`${change.symbol}-${change.timestamp}`}
                        className="flex flex-col gap-1 border-b border-border pb-2 last:border-0"
                      >
                        <div className="flex items-center justify-between gap-2">
                          <button
                            type="button"
                            className="font-medium hover:underline"
                            onClick={() => setSelected(change.symbol)}
                          >
                            {change.symbol}
                          </button>
                          <span className="tabular text-[11px] text-muted">
                            {formatIstTime(change.timestamp)}
                          </span>
                        </div>
                        <div className="flex items-center gap-1.5 text-xs">
                          {change.previous_regime && (
                            <>
                              <RegimeBadge regime={change.previous_regime} short />
                              <span className="text-muted">→</span>
                            </>
                          )}
                          <RegimeBadge regime={change.regime} short />
                          <span className="tabular ml-auto text-muted">
                            {formatScore(change.confidence)}%
                          </span>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </CardContent>
            </Card>
          </div>

          <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
            <Card className="min-w-0">
              <CardHeader>
                <CardTitle>Regimes by symbol</CardTitle>
              </CardHeader>
              <CardContent className="p-0">
                <RegimeTable symbols={symbols} selected={selectedSymbol} onSelect={setSelected} />
              </CardContent>
            </Card>

            <Card className="xl:sticky xl:top-0">
              <CardHeader>
                <CardTitle>Symbol detail</CardTitle>
              </CardHeader>
              <CardContent>
                <SymbolDetail symbol={selectedSymbol} />
              </CardContent>
            </Card>
          </div>
        </>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Data feed</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4 text-sm sm:grid-cols-3 lg:grid-cols-6">
            <Stat label="Provider">
              {marketStatus ? (
                <Badge
                  variant={marketStatus.provider_status === "CONNECTED" ? "connected" : "disconnected"}
                  className="w-fit"
                >
                  {marketStatus.provider_status}
                </Badge>
              ) : (
                "—"
              )}
            </Stat>
            <Stat label="Ticks accepted">
              <span className="tabular">{marketStatus?.ticks_accepted.toLocaleString() ?? "—"}</span>
            </Stat>
            <Stat label="Symbols analysed">
              <span className="tabular">{serviceStatus?.symbols_analyzed ?? symbols.length}</span>
            </Stat>
            <Stat label="Refresh">
              <span className="tabular">
                {serviceStatus ? `every ${serviceStatus.refresh_seconds}s` : "—"}
              </span>
            </Stat>
            <Stat label="Compute time">
              <span className="tabular">
                {serviceStatus?.last_compute_ms != null ? `${serviceStatus.last_compute_ms.toFixed(0)} ms` : "—"}
              </span>
            </Stat>
            <Stat label="Last error">
              <span className={cn("text-xs", serviceStatus?.last_error ? "text-loss" : "text-muted")}>
                {serviceStatus?.last_error ?? "None"}
              </span>
            </Stat>
          </div>
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
  );
}

function newest(
  a: MarketIntelligenceResponse | null,
  b: MarketIntelligenceResponse | null
): MarketIntelligenceResponse | null {
  if (!a) return b;
  if (!b) return a;
  return new Date(a.snapshot.timestamp) >= new Date(b.snapshot.timestamp) ? a : b;
}

function Stat({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[11px] uppercase tracking-wide text-muted">{label}</span>
      {children}
    </div>
  );
}
