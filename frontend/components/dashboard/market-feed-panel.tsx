"use client";

import { Figure, Panel, errorText } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { formatIstTime } from "@/lib/intelligence-format";
import {
  authLabel,
  brokerLabel,
  candleDot,
  executionLabel,
  marketDataLabel,
} from "@/lib/market-data-format";
import { useBrokerHealth, useMarketDataStatus, useZerodhaStatus } from "@/lib/queries";
import { cn } from "@/lib/utils";

/** Broker, auth, market data and execution at a glance, with feed metrics
 * and candle status. Execution never shows LIVE unless the backend reports
 * live execution enabled. */
export function MarketFeedPanel() {
  const mdq = useMarketDataStatus();
  const md = mdq.data;
  const z = useZerodhaStatus().data;
  const health = useBrokerHealth().data;
  const broker = brokerLabel(z);
  const auth = authLabel(z);
  const data = marketDataLabel(md);
  const exec = executionLabel(z);
  const instruments = health?.checks.find((c) => c.name === "instrument_master");

  return (
    <Panel title="Market Data & Broker" loading={mdq.isLoading} error={errorText(mdq.error)} testId="market-feed-panel">
      {md && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Figure label="Broker status" testId="feed-broker">
              <Badge variant={broker.variant}>{broker.text}</Badge>
            </Figure>
            <Figure label="Auth" testId="feed-auth" hint={z?.expires_at ? `expires ${formatIstTime(z.expires_at)} IST` : undefined}>
              <Badge variant={auth.variant}>{auth.text}</Badge>
            </Figure>
            <Figure label="Market data" testId="feed-market-data">
              <Badge variant={data.variant}>{data.text}</Badge>
            </Figure>
            <Figure label="Execution" testId="feed-execution">
              <Badge variant={exec.variant}>{exec.text}</Badge>
            </Figure>
          </div>
          <div className="grid grid-cols-2 gap-3 border-t border-border pt-3 text-sm sm:grid-cols-4">
            <Figure label="Last tick" testId="feed-last-tick">
              <span className="tabular">
                {md.last_tick ? `${formatIstTime(md.last_tick)} (${md.last_tick_age_seconds?.toFixed(0)}s)` : "—"}
              </span>
            </Figure>
            <Figure label="Latency" hint="exchange timestamp to receipt (index ticks)">
              <span className="tabular">{md.latency_ms != null ? `${md.latency_ms.toFixed(0)} ms` : "—"}</span>
            </Figure>
            <Figure label="Subscribed" testId="feed-subscribed">
              <span className="tabular">{md.subscribed_instruments}</span>
            </Figure>
            <Figure label="WebSocket">{md.websocket}</Figure>
            <Figure label="REST API">{md.rest_api}</Figure>
            <Figure label="Instrument master" hint={instruments?.detail}>
              {md.simulated ? "N/A" : (instruments?.status ?? "—")}
            </Figure>
            <Figure label="Ticks" hint={Object.entries(md.rejected_by_issue).map(([k, v]) => `${k} ${v}`).join(" · ") || undefined}>
              <span className="tabular">
                {md.ticks_accepted} ok · {md.ticks_rejected} rejected
              </span>
            </Figure>
            <Figure label={`Candles ${md.candle_status_symbol ?? ""}`} testId="feed-candles">
              <span className="flex items-center gap-2 text-xs">
                {(["5m", "15m", "60m"] as const).map((tf) => (
                  <span key={tf} className="flex items-center gap-1" title={md.candles[tf] ?? "NONE"}>
                    <span className={cn("h-2 w-2 rounded-full", candleDot(md.candles[tf]))} aria-hidden />
                    {tf}
                  </span>
                ))}
              </span>
            </Figure>
          </div>
          {md.unresolved_symbols.length > 0 && (
            <p className="text-xs text-warning">Not in the instrument master: {md.unresolved_symbols.join(", ")}</p>
          )}
          {md.stale && !md.simulated && (
            <p className="text-xs text-loss" data-testid="feed-stale">
              Data is not fresh: executable signals and new orders are blocked until ticks resume.
            </p>
          )}
        </div>
      )}
    </Panel>
  );
}
