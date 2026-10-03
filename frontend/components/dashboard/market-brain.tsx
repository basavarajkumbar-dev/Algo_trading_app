"use client";

import { DataNoun } from "@/components/market/data-source-badge";
import type { ReactNode } from "react";

import { Panel, errorText } from "@/components/dashboard/panel";
import { RegimeBadge } from "@/components/market/regime-badge";
import { Badge } from "@/components/ui/badge";
import { NIFTY_SYMBOL, strategyLabel } from "@/lib/dashboard-format";
import { formatPercent, formatScore, humanize } from "@/lib/intelligence-format";
import { useMarketIntelligence, useRoute, useSymbolIntelligence, useTradingStatus } from "@/lib/queries";
import { cn } from "@/lib/utils";

function Row({ label, value, detail, tone }: { label: string; value: ReactNode; detail?: ReactNode; tone?: string }) {
  return (
    <div className="grid grid-cols-[110px_minmax(0,1fr)] items-baseline gap-3 border-b border-border py-1.5 last:border-0">
      <span className="text-[11px] uppercase tracking-wide text-muted">{label}</span>
      <span className="min-w-0">
        <span className={cn("text-sm font-medium", tone)}>{value}</span>
        {detail && <span className="ml-2 text-xs text-muted">{detail}</span>}
      </span>
    </div>
  );
}

function dirTone(direction: string | null | undefined): string {
  if (direction === "UP" || direction === "ABOVE" || direction === "BULLISH") return "text-profit";
  if (direction === "DOWN" || direction === "BELOW" || direction === "BEARISH") return "text-loss";
  return "";
}

/**
 * "AI Market Brain": the NIFTY 50 picture from the market intelligence
 * engines (rule-based analysis of closed candles, not a forecast) and the
 * strategies the router allows for that regime.
 */
export function MarketBrain() {
  const intel = useMarketIntelligence();
  const index = useSymbolIntelligence(NIFTY_SYMBOL);
  const trading = useTradingStatus();
  const snap = intel.data?.snapshot;
  const route = useRoute(snap?.market_regime, snap?.confidence);
  const a = index.data?.analysis;
  const active = trading.data?.active_trade;

  const leaders = [...(intel.data?.symbols ?? [])]
    .filter((s) => s.symbol !== NIFTY_SYMBOL && s.relative_strength_score !== null)
    .sort((x, y) => (y.relative_strength_score ?? 0) - (x.relative_strength_score ?? 0))
    .slice(0, 3);

  return (
    <Panel
      title="AI Market Brain"
      href="/market"
      linkLabel="Market"
      loading={intel.isLoading}
      error={errorText(intel.error)}
      testId="market-brain"
    >
      {snap && (
        <div className="flex flex-col">
          <Row label="Market regime" value={<RegimeBadge regime={snap.market_regime} />} detail={humanize(snap.market_direction)} />
          <Row label="Confidence" value={`${formatScore(snap.confidence)}%`} detail={snap.regime.reasons[0]} />
          <Row
            label="Active strategy"
            value={
              active?.strategy ? (
                strategyLabel(active.strategy)
              ) : route.data?.eligible_strategies.length ? (
                <span className="flex flex-wrap gap-1">
                  {route.data.eligible_strategies.map((s) => (
                    <Badge key={s} variant="neutral">
                      {strategyLabel(s)}
                    </Badge>
                  ))}
                </span>
              ) : route.data ? (
                <span className="text-muted">None routed</span>
              ) : (
                "—"
              )
            }
            detail={active?.strategy ? `in the active ${active.symbol} trade` : "routed for this regime"}
          />
          <Row
            label="Trend"
            value={a?.trend_quality ? `${humanize(a.trend_quality.direction)} · ${formatScore(a.trend_quality.score)}` : humanize(snap.market_direction)}
            tone={dirTone(a?.trend_quality?.direction ?? snap.market_direction)}
            detail={a?.moving_averages ? `MAs ${a.moving_averages.alignment.toLowerCase()}` : undefined}
          />
          <Row
            label="Momentum"
            value={a?.momentum ? `${humanize(a.momentum.direction)} · ${formatScore(a.momentum.momentum_score)}` : formatScore(snap.momentum)}
            tone={dirTone(a?.momentum?.direction)}
          />
          <Row
            label="VWAP"
            value={a?.vwap ? `${humanize(a.vwap.price_vs_vwap)} VWAP` : "—"}
            tone={dirTone(a?.vwap?.price_vs_vwap)}
            detail={
              a?.vwap
                ? `${formatPercent(a.vwap.distance_from_vwap_percent)} · breadth ${formatScore(snap.breadth.percent_above_vwap)}% above`
                : undefined
            }
          />
          <Row
            label="Volume"
            value={a?.volume ? `${a.volume.relative_volume.toFixed(2)}× average` : "—"}
            detail={a?.volume ? `${humanize(a.volume.volume_trend)}${a.volume.is_spike ? " · spike" : ""}` : undefined}
          />
          <Row
            label="Volatility"
            value={humanize(a?.volatility?.volatility_level ?? snap.volatility)}
            detail={a?.volatility ? `ATR ${a.volatility.atr_percent.toFixed(2)}%${a.volatility.is_expanding ? ", expanding" : ""}` : undefined}
          />
          <Row
            label="Structure"
            value={a?.structure ? humanize(a.structure.structure_state) : "—"}
            tone={dirTone(a?.structure?.trend)}
            detail={a?.structure && a.structure.last_event !== "NONE" ? humanize(a.structure.last_event) : undefined}
          />
          <Row
            label="Relative strength"
            value={
              leaders.length ? (
                <span className="flex flex-wrap gap-x-3">
                  {leaders.map((l) => (
                    <span key={l.symbol}>
                      {l.symbol} <span className="tabular text-xs text-muted">{formatScore(l.relative_strength_score)}</span>
                    </span>
                  ))}
                </span>
              ) : (
                "—"
              )
            }
            detail="leaders vs NIFTY 50"
          />
          <p className="mt-2 text-[10px] leading-snug text-muted">
            Rule-based analysis of closed <DataNoun /> candles. Describes recent behaviour; it does not
            predict the next move.
          </p>
        </div>
      )}
    </Panel>
  );
}
