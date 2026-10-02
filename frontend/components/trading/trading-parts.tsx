import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import { formatIstTime, formatPrice } from "@/lib/intelligence-format";
import {
  EXIT_REASON_LABEL,
  LIFECYCLE,
  REJECTION_LABEL,
  STATE_LABEL,
  TRADE_STATUS_VARIANT,
  formatInr,
  formatR,
  humanCheck,
  lifecycleIndex,
  pnlTone,
  usedPercent,
} from "@/lib/trading-format";
import { cn } from "@/lib/utils";
import type { PaperTrade, RiskCheck, RiskDecision, TradingStateValue } from "@/types/trading";

export function Stat({
  label,
  children,
  hint,
  className,
}: {
  label: string;
  children: ReactNode;
  hint?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-0.5", className)}>
      <span className="text-[10px] uppercase tracking-wide text-muted">{label}</span>
      <span className="tabular text-sm">{children}</span>
      {hint && <span className="text-[10px] text-muted">{hint}</span>}
    </div>
  );
}

/** SCANNING → … → CLOSED with the current state highlighted. */
export function LifecycleStrip({ state }: { state: TradingStateValue }) {
  const current = lifecycleIndex(state);
  return (
    <ol className="flex flex-wrap items-center gap-1 text-[11px]">
      {LIFECYCLE.map((s, i) => {
        const label = i === 0 && state === "IDLE" ? STATE_LABEL.IDLE : STATE_LABEL[s];
        return (
          <li key={s} className="flex items-center gap-1">
            <span
              className={cn(
                "rounded-sm border px-2 py-1",
                i === current
                  ? "border-accent bg-accent/15 font-semibold text-foreground"
                  : i < current
                    ? "border-border text-foreground/70"
                    : "border-border text-muted"
              )}
            >
              {label}
            </span>
            {i < LIFECYCLE.length - 1 && <span className="text-muted">→</span>}
          </li>
        );
      })}
    </ol>
  );
}

export function LimitBar({ used, limit, label }: { used: number; limit: number; label: string }) {
  const pct = usedPercent(used, limit);
  return (
    <div className="flex flex-col gap-1">
      <div className="flex justify-between text-[11px] text-muted">
        <span>{label}</span>
        <span className="tabular">{pct.toFixed(0)}%</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-surface-alt">
        <div
          className={cn("h-full", pct >= 100 ? "bg-loss" : pct >= 66 ? "bg-warning" : "bg-profit")}
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export function RiskChecks({ checks }: { checks: RiskCheck[] }) {
  return (
    <ul className="flex flex-col divide-y divide-border text-xs">
      {checks.map((c) => (
        <li key={c.name} className="flex items-start gap-2 py-1.5">
          <span className={cn("mt-px w-3 shrink-0 font-semibold", c.passed ? "text-profit" : "text-loss")}>
            {c.passed ? "✓" : "✕"}
          </span>
          <span className="w-36 shrink-0 font-medium">{humanCheck(c.name)}</span>
          <span className="min-w-0 text-muted">{c.detail}</span>
        </li>
      ))}
    </ul>
  );
}

export function DecisionSummary({ decision }: { decision: RiskDecision }) {
  const i = decision.intent;
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-sm">
        <Badge variant={decision.approved ? "connected" : "disconnected"}>
          {decision.approved ? "APPROVED" : "REJECTED"}
        </Badge>
        <span className="font-medium">
          {i.side} {i.symbol}
        </span>
        <span className="text-xs text-muted">{formatIstTime(decision.evaluated_at, true)}</span>
      </div>
      {!decision.approved && (
        <div className="flex flex-wrap gap-1.5">
          {decision.rejection_reasons.map((r) => (
            <Badge key={r} variant="disconnected">
              {REJECTION_LABEL[r]}
            </Badge>
          ))}
        </div>
      )}
      {decision.size && (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          <Stat label="Quantity" hint={`raw ${decision.size.raw_quantity.toFixed(2)}`}>
            {decision.size.quantity}
          </Stat>
          <Stat label="Risk budget">{formatInr(decision.size.risk_amount)}</Stat>
          <Stat label="Planned risk" hint={`${formatPrice(decision.size.risk_per_unit)} / share`}>
            {formatInr(decision.size.actual_risk)}
          </Stat>
          <Stat label="Position value">{formatInr(decision.size.position_value)}</Stat>
        </div>
      )}
      {decision.size && decision.size.constraints.length > 0 && (
        <p className="text-[11px] text-muted">Sizing: {decision.size.constraints.join("; ")}.</p>
      )}
      <RiskChecks checks={decision.checks} />
    </div>
  );
}

export function TradeFigures({ trade }: { trade: PaperTrade }) {
  const live = trade.status === "OPEN" || trade.status === "EXITING" || trade.status === "PENDING";
  return (
    <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <Stat label="Filled" hint={`approved ${trade.quantity}`}>
        {trade.filled_quantity}
        {live && trade.open_quantity !== trade.filled_quantity ? ` (open ${trade.open_quantity})` : ""}
      </Stat>
      <Stat label="Entry" hint={`planned ${formatPrice(trade.planned_entry)}`}>
        {formatPrice(trade.entry_price)}
      </Stat>
      <Stat label="Stop loss">{formatPrice(trade.stop_loss)}</Stat>
      <Stat label="Target">{formatPrice(trade.target)}</Stat>
      <Stat label={live ? "Last price" : "Exit"}>
        {formatPrice(live ? trade.last_price : trade.exit_price)}
      </Stat>
      <Stat label={live ? "Unrealized" : "Gross P&L"}>
        <span className={pnlTone(live ? trade.unrealized_pnl : trade.gross_pnl)}>
          {formatInr(live ? trade.unrealized_pnl : trade.gross_pnl, true)}
        </span>
      </Stat>
      <Stat label="Charges">{formatInr(trade.charges)}</Stat>
      <Stat label={live ? "Initial risk" : "Net P&L"} hint={live ? undefined : formatR(trade.r_multiple)}>
        {live ? (
          formatInr(trade.initial_risk)
        ) : (
          <span className={cn("font-semibold", pnlTone(trade.net_pnl))}>{formatInr(trade.net_pnl, true)}</span>
        )}
      </Stat>
    </div>
  );
}

export function TradeHeader({ trade }: { trade: PaperTrade }) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-lg font-semibold">{trade.symbol}</span>
      <span className={cn("text-sm font-semibold", trade.side === "LONG" ? "text-profit" : "text-loss")}>
        {trade.side}
      </span>
      <Badge variant={TRADE_STATUS_VARIANT[trade.status]}>{trade.status}</Badge>
      {trade.exit_reason && <Badge variant="neutral">{EXIT_REASON_LABEL[trade.exit_reason]}</Badge>}
      <span className="text-xs text-muted">
        {trade.strategy?.replace(/_/g, " ").toLowerCase()}
        {trade.score !== null ? ` · score ${trade.score.toFixed(1)}` : ""} · opened{" "}
        {formatIstTime(trade.opened_at ?? trade.created_at, true)}
      </span>
    </div>
  );
}
