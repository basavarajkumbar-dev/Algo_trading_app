"use client";

import { Panel, errorText } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { HEALTH_VARIANT } from "@/lib/dashboard-format";
import { useComponentHealth, useTradingRisk, useTradingStatus } from "@/lib/queries";
import { REJECTION_LABEL, formatInr, usedPercent } from "@/lib/trading-format";

function Meter({ label, used, limit, value, testId }: { label: string; used: number; limit: number; value: string; testId?: string }) {
  const pct = usedPercent(used, limit);
  return (
    <div className="flex flex-col gap-1" data-testid={testId}>
      <div className="flex justify-between text-xs">
        <span className="text-muted">{label}</span>
        <span className="tabular">{value}</span>
      </div>
      <Progress
        value={pct}
        aria-label={label}
        indicatorClassName={pct >= 100 ? "bg-loss" : pct >= 66 ? "bg-warning" : "bg-accent"}
      />
    </div>
  );
}

/**
 * Daily loss, risk used by the open trade, trades used, exposure,
 * consecutive losses and system status, against the configured limits.
 */
export function RiskSummary({ title = "Risk" }: { title?: string }) {
  const risk = useTradingRisk();
  const status = useTradingStatus();
  const health = useComponentHealth();
  const r = risk.data;
  const snap = r?.snapshot;
  const lim = r?.limits;
  const trade = status.data?.active_trade;
  const riskUsed = trade ? trade.initial_risk : 0;

  return (
    <Panel title={title} href="/risk" linkLabel="Risk" loading={risk.isLoading} error={errorText(risk.error)} testId="risk-summary">
      {snap && lim && (
        <div className="flex flex-col gap-3">
          <Meter
            label="Daily loss"
            used={snap.daily_loss_limit_amount - snap.daily_loss_remaining}
            limit={snap.daily_loss_limit_amount}
            value={`${formatInr(snap.daily_loss_limit_amount - snap.daily_loss_remaining)} / ${formatInr(snap.daily_loss_limit_amount)}`}
            testId="meter-daily-loss"
          />
          <Meter
            label="Risk used (open trade)"
            used={riskUsed}
            limit={snap.risk_per_trade_amount}
            value={`${formatInr(riskUsed)} / ${formatInr(snap.risk_per_trade_amount)}`}
          />
          <Meter
            label="Trades used"
            used={snap.state.trades_today}
            limit={lim.max_trades_per_day}
            value={`${snap.state.trades_today} / ${lim.max_trades_per_day}`}
            testId="meter-trades"
          />
          <Meter
            label="Exposure"
            used={snap.open_exposure}
            limit={lim.max_exposure}
            value={`${formatInr(snap.open_exposure)} / ${formatInr(lim.max_exposure)}`}
          />
          <Meter
            label="Consecutive losses"
            used={snap.state.consecutive_losses}
            limit={lim.max_consecutive_losses}
            value={`${snap.state.consecutive_losses} / ${lim.max_consecutive_losses}`}
          />
          <div className="flex flex-wrap items-center gap-1.5 border-t border-border pt-3 text-xs">
            <span className="text-muted">System</span>
            {health.data ? (
              <Badge variant={HEALTH_VARIANT[health.data.status]}>{health.data.status}</Badge>
            ) : (
              <Badge variant="neutral">…</Badge>
            )}
            <span className="ml-2 text-muted">Entries</span>
            {snap.entries_blocked ? (
              snap.blocked_reasons.map((reason) => (
                <Badge key={reason} variant={reason === "ACTIVE_POSITION_EXISTS" ? "pending" : "disconnected"}>
                  {REJECTION_LABEL[reason]}
                </Badge>
              ))
            ) : (
              <Badge variant="connected">Allowed</Badge>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}
