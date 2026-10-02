"use client";

import { useEffect, useState } from "react";

import { DecisionSummary, LimitBar, Stat } from "@/components/trading/trading-parts";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { getTradingRiskRequest } from "@/lib/trading-client";
import { REJECTION_LABEL, formatInr, pnlTone } from "@/lib/trading-format";
import type { RiskResponse } from "@/types/trading";

const POLL_MS = 3_000;

export default function RiskPage() {
  const [data, setData] = useState<RiskResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      getTradingRiskRequest()
        .then((d) => {
          if (cancelled) return;
          setData(d);
          setError(null);
        })
        .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : "Request failed"));
    }
    poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const l = data?.limits;
  const snap = data?.snapshot;

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Risk</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Every paper order passes these checks first. Quantity = (capital × risk per trade) ÷
          |entry − stop|, rounded down to whole lots and capped by position value, available
          capital, order size and what is left of today&apos;s loss limit. Limits are
          configuration (RISK_* environment variables), not guarantees.
        </p>
      </div>

      {error && (
        <Card>
          <CardContent className="py-6 text-sm text-loss">Could not load risk: {error}</CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle>Today</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {snap && (
              <>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                  <Stat label="Trading day">{snap.state.trading_day}</Stat>
                  <Stat label="Day-start capital">{formatInr(snap.state.day_start_capital)}</Stat>
                  <Stat label="Realized (net)">
                    <span className={pnlTone(snap.state.realized_pnl_today)}>
                      {formatInr(snap.state.realized_pnl_today, true)}
                    </span>
                  </Stat>
                  <Stat label="Trades taken" hint={`${snap.trades_remaining} left`}>
                    {snap.state.trades_today}
                  </Stat>
                  <Stat label="Losses in a row" hint={`${snap.consecutive_losses_remaining} before stop`}>
                    {snap.state.consecutive_losses}
                  </Stat>
                  <Stat label="Open exposure">{formatInr(snap.open_exposure)}</Stat>
                </div>
                <LimitBar
                  label={`Daily loss used (limit ${formatInr(snap.daily_loss_limit_amount)})`}
                  used={snap.daily_loss_limit_amount - snap.daily_loss_remaining}
                  limit={snap.daily_loss_limit_amount}
                />
                {l && (
                  <>
                    <LimitBar
                      label={`Trades (limit ${l.max_trades_per_day})`}
                      used={snap.state.trades_today}
                      limit={l.max_trades_per_day}
                    />
                    <LimitBar
                      label={`Consecutive losses (limit ${l.max_consecutive_losses})`}
                      used={snap.state.consecutive_losses}
                      limit={l.max_consecutive_losses}
                    />
                  </>
                )}
                <div className="flex flex-wrap items-center gap-1.5">
                  <span className="text-xs text-muted">New entries:</span>
                  {snap.entries_blocked ? (
                    snap.blocked_reasons.map((r) => (
                      <Badge key={r} variant="disconnected">
                        {REJECTION_LABEL[r]}
                      </Badge>
                    ))
                  ) : (
                    <Badge variant="connected">Allowed</Badge>
                  )}
                </div>
              </>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Configured limits</CardTitle>
          </CardHeader>
          <CardContent>
            {l && (
              <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                <Stat label="Risk per trade" hint={snap ? formatInr(snap.risk_per_trade_amount) : undefined}>
                  {l.risk_per_trade_percent}%
                </Stat>
                <Stat label="Daily loss limit">{l.daily_loss_limit_percent}%</Stat>
                <Stat label="Max trades / day">{l.max_trades_per_day}</Stat>
                <Stat label="Max consecutive losses">{l.max_consecutive_losses}</Stat>
                <Stat label="Max position value">{formatInr(l.max_position_value)}</Stat>
                <Stat label="Max exposure">{formatInr(l.max_exposure)}</Stat>
                <Stat label="Lot / tick">
                  {l.lot_size} / {l.tick_size}
                </Stat>
                <Stat label="Max order quantity">{l.max_order_quantity.toLocaleString("en-IN")}</Stat>
                <Stat label="Margin multiplier">{l.margin_multiplier}×</Stat>
                <Stat label="Min signal score">{l.min_signal_score}</Stat>
                <Stat label="Max data age">{l.max_data_age_seconds}s</Stat>
                <Stat label="Max signal age">{l.max_signal_age_seconds}s</Stat>
                <Stat label="Max entry deviation">{l.max_entry_deviation_r}R</Stat>
                <Stat label="Short selling">{l.allow_short ? "allowed" : "disabled"}</Stat>
                <Stat
                  label="Session"
                  hint={l.session.mode === "EXCHANGE" ? "IST, Mon–Fri; holidays from config" : "exchange hours not enforced"}
                >
                  {l.session.mode === "EXCHANGE"
                    ? `${l.session.open_time.slice(0, 5)}–${l.session.close_time.slice(0, 5)}, entries until ${l.session.last_entry_time.slice(0, 5)}, square-off ${l.session.square_off_time.slice(0, 5)}`
                    : "Simulated 24h"}
                </Stat>
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Last risk decision</CardTitle>
        </CardHeader>
        <CardContent>
          {data?.last_decision ? (
            <DecisionSummary decision={data.last_decision} />
          ) : (
            <p className="text-sm text-muted">No signal has been evaluated since the service started.</p>
          )}
        </CardContent>
      </Card>
      {data && <p className="text-[11px] text-muted">{data.note}</p>}
    </div>
  );
}
