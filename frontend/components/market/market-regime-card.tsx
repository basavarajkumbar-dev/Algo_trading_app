"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { RegimeBadge } from "@/components/market/regime-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatIstTime, formatScore, humanize } from "@/lib/intelligence-format";
import { getMarketIntelligenceRequest } from "@/lib/market-client";
import type { MarketIntelligenceSnapshot } from "@/types/intelligence";

const POLL_MS = 15_000;

/** Compact market regime summary for the dashboard home page. */
export function MarketRegimeCard({ className }: { className?: string }) {
  const [snapshot, setSnapshot] = useState<MarketIntelligenceSnapshot | null>(null);
  const [error, setError] = useState(false);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      getMarketIntelligenceRequest()
        .then((data) => {
          if (cancelled) return;
          setSnapshot(data.snapshot);
          setError(false);
        })
        .catch(() => !cancelled && setError(true));
    }
    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  return (
    <Card className={className}>
      <CardHeader className="flex flex-row items-center justify-between">
        <CardTitle>Market Regime</CardTitle>
        <Link href="/market" className="text-xs text-accent hover:underline">
          Open Market Intelligence →
        </Link>
      </CardHeader>
      <CardContent>
        {error && !snapshot && (
          <p className="text-sm text-loss">Could not reach the market intelligence API.</p>
        )}
        {!error && !snapshot && <p className="text-sm text-muted">Loading…</p>}
        {snapshot && (
          <div className="flex flex-col gap-4">
            <div className="flex flex-wrap items-center gap-3">
              <RegimeBadge regime={snapshot.market_regime} className="text-sm" />
              <span className="tabular text-sm">
                {formatScore(snapshot.confidence)}% <span className="text-muted">confidence</span>
              </span>
              <span className="text-xs text-muted">
                {snapshot.benchmark_symbol} · {snapshot.timeframe} · {formatIstTime(snapshot.timestamp)}
              </span>
            </div>
            {snapshot.regime.reasons.length > 0 && (
              <ul className="flex flex-col gap-1 text-sm">
                {snapshot.regime.reasons.slice(0, 3).map((reason) => (
                  <li key={reason}>· {reason}</li>
                ))}
              </ul>
            )}
            <div className="grid grid-cols-2 gap-3 text-sm sm:grid-cols-4">
              <Mini label="Direction" value={humanize(snapshot.market_direction)} />
              <Mini label="Volatility" value={humanize(snapshot.volatility)} />
              <Mini label="Trend strength" value={formatScore(snapshot.trend_strength)} />
              <Mini
                label="Advancing / declining"
                value={`${snapshot.breadth.advancing} / ${snapshot.breadth.declining}`}
              />
            </div>
            <p className="text-[11px] text-muted">
              Simulated data. {snapshot.note}
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function Mini({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="text-[11px] uppercase tracking-wide text-muted">{label}</span>
      <span className="tabular">{value}</span>
    </div>
  );
}
