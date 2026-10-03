"use client";

import { DataNoun } from "@/components/market/data-source-badge";
import { livePnl } from "@/components/dashboard/active-position";
import { Figure, errorText } from "@/components/dashboard/panel";
import { RegimeBadge } from "@/components/market/regime-badge";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { HEALTH_VARIANT, NIFTY_SYMBOL, algoStatus } from "@/lib/dashboard-format";
import { formatPrice, formatScore } from "@/lib/intelligence-format";
import { useComponentHealth, useMarketIntelligence, useTradingStatus } from "@/lib/queries";
import { useMarketStore } from "@/lib/stores/market-store";
import { formatInr, pnlTone } from "@/lib/trading-format";
import { cn } from "@/lib/utils";

/**
 * The nine headline figures. Today's P&L = realized net P&L today (after
 * charges) + the open trade's net P&L at the live price (its charges so
 * far included), the same figure the Active Position panel shows.
 */
export function CommandCenter() {
  const trading = useTradingStatus();
  const intel = useMarketIntelligence();
  const health = useComponentHealth();
  const nifty = useMarketStore((s) => s.snapshots[NIFTY_SYMBOL]);

  const s = trading.data;
  const snap = intel.data?.snapshot;
  const risk = s?.risk.state;
  const trade = s?.active_trade ?? null;
  const tradeLive = useMarketStore((st) => (trade ? st.snapshots[trade.symbol]?.price : undefined));
  const openNet = trade
    ? trade.net_pnl + (livePnl(trade, tradeLive ?? trade.last_price) ?? trade.unrealized_pnl)
    : 0;
  const todayPnl = risk ? risk.realized_pnl_today + openNet : null;
  const algo = algoStatus(s);
  const err = errorText(trading.error) ?? errorText(intel.error);

  return (
    <Card className="px-4 py-3" data-testid="command-center">
      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5 2xl:grid-cols-9">
        <Figure
          label="NIFTY 50"
          testId="cc-nifty"
          hint={
            nifty ? (
              <span className={nifty.change_percent >= 0 ? "text-profit" : "text-loss"}>
                {nifty.change_percent >= 0 ? "+" : ""}
                {nifty.change_percent.toFixed(2)}% · <DataNoun />
              </span>
            ) : (
              "waiting for the feed"
            )
          }
        >
          {nifty ? formatPrice(nifty.price) : "—"}
        </Figure>
        <Figure label="Market regime" hint={snap ? `${snap.market_direction.toLowerCase()} · ${snap.timeframe}` : undefined}>
          {snap ? <RegimeBadge regime={snap.market_regime} /> : "—"}
        </Figure>
        <Figure label="Regime confidence" hint="how clearly the regime fits">
          {snap ? `${formatScore(snap.confidence)}%` : "—"}
        </Figure>
        <Figure label="Algo status" hint={s ? (s.auto_trading ? "automatic: on" : "automatic: off") : undefined}>
          <Badge variant={algo.variant} data-testid="cc-algo">
            {algo.label}
          </Badge>
        </Figure>
        <Figure label="Trading mode" hint="orders run on the paper broker">
          <Badge variant="accent">{s?.mode ?? "PAPER"}</Badge>
        </Figure>
        <Figure
          label="Today's P&L"
          testId="cc-pnl"
          hint={s ? `realized ${formatInr(risk?.realized_pnl_today, true)}${trade ? ` · open ${formatInr(openNet, true)}` : ""}` : undefined}
        >
          <span className={cn("font-semibold", pnlTone(todayPnl))}>{formatInr(todayPnl, true)}</span>
        </Figure>
        <Figure label="Trade count" hint={s ? `${s.risk.trades_remaining} left today` : undefined} testId="cc-trades">
          {risk ? risk.trades_today : "—"}
        </Figure>
        <Figure label="Win / loss" hint={risk ? `${risk.consecutive_losses} loss(es) in a row` : undefined} testId="cc-winloss">
          {risk ? (
            <>
              <span className="text-profit">{risk.wins_today}W</span>
              <span className="text-muted"> / </span>
              <span className="text-loss">{risk.losses_today}L</span>
            </>
          ) : (
            "—"
          )}
        </Figure>
        <Figure label="System health" hint={health.data ? `${health.data.components.filter((c) => c.status !== "HEALTHY").length} component(s) need attention` : undefined}>
          {health.data ? (
            <Badge variant={HEALTH_VARIANT[health.data.status]} data-testid="cc-health">
              {health.data.status}
            </Badge>
          ) : (
            "—"
          )}
        </Figure>
      </div>
      {err && <p className="mt-3 text-xs text-loss">Some figures are unavailable: {err}</p>}
    </Card>
  );
}
