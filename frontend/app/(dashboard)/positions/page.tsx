"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { PositionBar } from "@/components/dashboard/position-bar";
import {
  DecisionSummary,
  LifecycleStrip,
  LimitBar,
  Stat,
  TradeFigures,
  TradeHeader,
} from "@/components/trading/trading-parts";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import { formatDuration } from "@/lib/dashboard-format";
import { formatIstTime, formatPrice } from "@/lib/intelligence-format";
import { useTradingStatus } from "@/lib/queries";
import { qk } from "@/lib/query-keys";
import { useMarketStore } from "@/lib/stores/market-store";
import {
  emergencyStopRequest,
  exitPositionRequest,
  resetEmergencyStopRequest,
  setAutoTradingRequest,
} from "@/lib/trading-client";
import { REJECTION_LABEL, STATE_LABEL, formatInr, pnlTone } from "@/lib/trading-format";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";
import type { TradingStatus } from "@/types/trading";

export default function PositionsPage() {
  const { accessToken } = useAuth();
  const queryClient = useQueryClient();
  const query = useTradingStatus();
  const status = query.data ?? null;
  const error = query.error ? (query.error instanceof Error ? query.error.message : "Request failed") : null;
  const [actionError, setActionError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const live = useMarketStore((st) => (status?.active_trade ? st.snapshots[status.active_trade.symbol] : undefined));
  const now = useNow();

  async function act(fn: (token: string) => Promise<TradingStatus>) {
    if (!accessToken) return;
    setBusy(true);
    setActionError(null);
    try {
      queryClient.setQueryData(qk.tradingStatus, await fn(accessToken));
      void queryClient.invalidateQueries({ queryKey: ["trading"] });
      void queryClient.invalidateQueries({ queryKey: ["system"] });
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : "Action failed");
    } finally {
      setBusy(false);
    }
  }

  const s = status;
  const trade = s?.active_trade ?? null;
  const risk = s?.risk;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">Positions</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            Signal → risk check → paper order → fill → position → stop / target → exit → P&amp;L,
            one trade at a time. Orders go to a simulated broker; no broker is connected and no
            real money is used.
          </p>
        </div>
        <div className="flex shrink-0 flex-wrap items-center gap-2">
          <Badge variant="accent">PAPER</Badge>
          {s && (
            <Badge variant={s.system_healthy ? "connected" : "disconnected"}>
              {s.system_healthy ? "System healthy" : "System unhealthy"}
            </Badge>
          )}
          {s?.emergency_stop && <Badge variant="disconnected">EMERGENCY STOP</Badge>}
        </div>
      </div>

      {error && (
        <Card>
          <CardContent className="py-6 text-sm text-loss">Could not load paper trading: {error}</CardContent>
        </Card>
      )}

      {s && (
        <Card>
          <CardContent className="flex flex-col gap-4 py-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <LifecycleStrip state={s.state} />
              <span className="text-xs text-muted">
                {STATE_LABEL[s.state]} since {formatIstTime(s.state_since, true)}
              </span>
            </div>
            <div className="flex flex-wrap items-center gap-2 border-t border-border pt-4">
              <Button
                size="sm"
                variant={s.auto_trading ? "outline" : "default"}
                disabled={busy || !accessToken || (s.emergency_stop && !s.auto_trading)}
                onClick={() => act((t) => setAutoTradingRequest(!s.auto_trading, t))}
              >
                {s.auto_trading ? "Turn automatic trading off" : "Turn automatic trading on"}
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy || !accessToken || !trade || s.state === "EXIT"}
                onClick={() => act((t) => exitPositionRequest(t))}
              >
                Exit position
              </Button>
              {s.emergency_stop ? (
                <Button
                  size="sm"
                  variant="outline"
                  disabled={busy || !accessToken || s.state === "EXIT"}
                  onClick={() => act((t) => resetEmergencyStopRequest(t))}
                >
                  Reset emergency stop
                </Button>
              ) : (
                <Button
                  size="sm"
                  className="bg-loss text-white hover:bg-loss/90"
                  disabled={busy || !accessToken}
                  onClick={() => {
                    if (!window.confirm("Emergency stop: disable new trading, turn automatic trading off and cancel pending entry orders?")) {
                      return;
                    }
                    // Positions are never closed automatically: only on an explicit yes.
                    const close =
                      !!trade &&
                      window.confirm(
                        `Also close the active ${trade.symbol} position at market now?\n\nOK = close it. Cancel = keep it, with its stop loss and target still working.`
                      );
                    void act((t) => emergencyStopRequest("dashboard", t, close));
                  }}
                >
                  Emergency stop
                </Button>
              )}
              <span className="text-xs text-muted">
                Automatic trading is {s.auto_trading ? "ON: the best READY signal is taken after each scan" : "OFF"}.
                {s.emergency_stop && s.emergency_reason ? ` Stopped: ${s.emergency_reason}.` : ""}
                {s.emergency_stop && trade && s.state === "POSITION_ACTIVE"
                  ? " The open position was kept; its stop and target are still working."
                  : ""}
              </span>
            </div>
            {actionError && <p className="text-sm text-loss">{actionError}</p>}
            {!s.system_healthy && <p className="text-xs text-loss">{s.health_detail}</p>}
          </CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,1fr)_380px]">
        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Active trade</CardTitle>
            </CardHeader>
            <CardContent>
              {!trade ? (
                <p className="text-sm text-muted">
                  No active trade. Execute a READY signal from the{" "}
                  <Link href="/signals" className="text-accent hover:underline">
                    Signals
                  </Link>{" "}
                  page, or turn automatic trading on.
                </p>
              ) : (
                <div className="flex flex-col gap-4">
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <TradeHeader trade={trade} />
                    <span className="tabular text-xs text-muted">
                      held {formatDuration(trade.opened_at ?? trade.created_at, now)}
                    </span>
                  </div>
                  <TradeFigures trade={trade} />
                  {trade.entry_price !== null && (
                    <PositionBar
                      stop={trade.stop_loss}
                      entry={trade.entry_price}
                      target={trade.target}
                      current={live?.price ?? trade.last_price}
                    />
                  )}
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>
                Working orders <span className="ml-1 text-xs font-normal text-muted">{s?.working_orders.length ?? 0}</span>
              </CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <table className="w-full text-xs">
                <thead>
                  <tr className="border-b border-border text-left text-[10px] uppercase tracking-wide text-muted">
                    <th className="px-4 py-2 font-medium">Purpose</th>
                    <th className="px-2 py-2 font-medium">Side</th>
                    <th className="px-2 py-2 font-medium">Type</th>
                    <th className="px-2 py-2 text-right font-medium">Qty</th>
                    <th className="px-2 py-2 text-right font-medium">Price / trigger</th>
                    <th className="px-4 py-2 font-medium">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {s && s.working_orders.length === 0 && (
                    <tr>
                      <td colSpan={6} className="px-4 py-4 text-center text-muted">
                        No working orders.
                      </td>
                    </tr>
                  )}
                  {s?.working_orders.map((o) => (
                    <tr key={o.id} className="border-b border-border last:border-0">
                      <td className="px-4 py-1.5">{o.tag?.replace("_", " ").toLowerCase()}</td>
                      <td className={cn("px-2 py-1.5 font-semibold", o.side === "BUY" ? "text-profit" : "text-loss")}>{o.side}</td>
                      <td className="px-2 py-1.5 text-muted">{o.order_type.replace("_", "-")}</td>
                      <td className="tabular px-2 py-1.5 text-right">
                        {o.filled_quantity > 0 ? `${o.filled_quantity}/` : ""}
                        {o.quantity}
                      </td>
                      <td className="tabular px-2 py-1.5 text-right">{formatPrice(o.price ?? o.trigger_price)}</td>
                      <td className="px-4 py-1.5 text-muted">{o.status.replace("_", " ").toLowerCase()}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Last risk decision</CardTitle>
            </CardHeader>
            <CardContent>
              {s?.last_decision ? (
                <DecisionSummary decision={s.last_decision} />
              ) : (
                <p className="text-sm text-muted">No signal has been evaluated since the service started.</p>
              )}
            </CardContent>
          </Card>
        </div>

        <div className="flex flex-col gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Paper account</CardTitle>
            </CardHeader>
            <CardContent className="grid grid-cols-2 gap-4">
              <Stat label="Equity">{formatInr(s?.balance.equity)}</Stat>
              <Stat label="Available">{formatInr(s?.balance.available)}</Stat>
              <Stat label="Realized (gross)">
                <span className={pnlTone(s?.balance.realized_pnl)}>{formatInr(s?.balance.realized_pnl, true)}</span>
              </Stat>
              <Stat label="Unrealized">
                <span className={pnlTone(s?.balance.unrealized_pnl)}>{formatInr(s?.balance.unrealized_pnl, true)}</span>
              </Stat>
              <Stat label="Charges paid">{formatInr(s?.balance.charges_paid)}</Stat>
              <Stat label="Margin used">{formatInr(s?.balance.used_margin)}</Stat>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Today&apos;s risk</CardTitle>
            </CardHeader>
            <CardContent className="flex flex-col gap-4">
              {risk && (
                <>
                  <div className="grid grid-cols-2 gap-4">
                    <Stat label="Realized today (net)">
                      <span className={pnlTone(risk.state.realized_pnl_today)}>
                        {formatInr(risk.state.realized_pnl_today, true)}
                      </span>
                    </Stat>
                    <Stat label="Risk per trade">{formatInr(risk.risk_per_trade_amount)}</Stat>
                    <Stat label="Trades" hint={`${risk.trades_remaining} left`}>
                      {risk.state.trades_today} ({risk.state.wins_today}W / {risk.state.losses_today}L)
                    </Stat>
                    <Stat label="Losses in a row" hint={`${risk.consecutive_losses_remaining} before stop`}>
                      {risk.state.consecutive_losses}
                    </Stat>
                  </div>
                  <LimitBar
                    label={`Daily loss: ${formatInr(risk.daily_loss_limit_amount - risk.daily_loss_remaining)} of ${formatInr(risk.daily_loss_limit_amount)}`}
                    used={risk.daily_loss_limit_amount - risk.daily_loss_remaining}
                    limit={risk.daily_loss_limit_amount}
                  />
                  {risk.entries_blocked ? (
                    <div className="flex flex-wrap gap-1.5">
                      <span className="text-xs text-muted">New entries blocked:</span>
                      {risk.blocked_reasons.map((r) => (
                        <Badge key={r} variant={r === "ACTIVE_POSITION_EXISTS" ? "pending" : "disconnected"}>
                          {REJECTION_LABEL[r]}
                        </Badge>
                      ))}
                    </div>
                  ) : (
                    <p className="text-xs text-profit">New entries allowed.</p>
                  )}
                  <Link href="/risk" className="text-xs text-accent hover:underline">
                    Risk limits and checks →
                  </Link>
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Recent transitions</CardTitle>
            </CardHeader>
            <CardContent>
              <ul className="flex max-h-72 flex-col gap-1 overflow-y-auto text-[11px]">
                {s?.recent_transitions.length === 0 && <li className="text-muted">None yet.</li>}
                {s?.recent_transitions
                  .slice()
                  .reverse()
                  .map((t, i) => (
                    <li key={`${t.at}-${i}`} className="flex gap-2">
                      <span className="tabular shrink-0 text-muted">{formatIstTime(t.at, true)}</span>
                      <span className="shrink-0 font-medium">{STATE_LABEL[t.to_state]}</span>
                      <span className="min-w-0 truncate text-muted">{t.reason}</span>
                    </li>
                  ))}
              </ul>
            </CardContent>
          </Card>
        </div>
      </div>

      {s && <p className="text-[11px] text-muted">{s.note}</p>}
    </div>
  );
}
