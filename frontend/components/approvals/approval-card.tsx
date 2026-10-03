"use client";

import { Check, X } from "lucide-react";
import { useState } from "react";

import { Figure } from "@/components/dashboard/panel";
import { RegimeBadge } from "@/components/market/regime-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { strategyLabel } from "@/lib/dashboard-format";
import { formatIstTime, formatPrice } from "@/lib/intelligence-format";
import { formatInr } from "@/lib/trading-format";
import { cn } from "@/lib/utils";
import type { ApprovalRequest } from "@/types/approvals";
import type { MarketRegime } from "@/types/intelligence";

/** Seconds left before the request expires (from the server's own count). */
export function secondsLeft(a: ApprovalRequest, fetchedAt: number, now: number): number {
  return Math.max(0, a.seconds_remaining - Math.max(0, now - fetchedAt) / 1000);
}

export function formatCountdown(seconds: number): string {
  const s = Math.ceil(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

/** One pending request: everything needed to decide, and the two buttons. */
export function ApprovalCard({
  approval,
  remaining,
  ttl,
  canDecide,
  busy,
  onApprove,
  onReject,
}: {
  approval: ApprovalRequest;
  remaining: number;
  ttl: number;
  canDecide: boolean;
  busy: boolean;
  onApprove: () => void;
  onReject: (reason: string) => void;
}) {
  const [rejecting, setRejecting] = useState(false);
  const [reason, setReason] = useState("");
  const a = approval;
  const expired = remaining <= 0;
  const buy = a.direction === "BUY";

  return (
    <Card className={cn("flex flex-col gap-4 p-4", expired && "opacity-60")} data-testid="approval-card">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <span className="text-lg font-semibold tracking-tight" data-testid="ap-stock">
            {a.symbol}
          </span>
          <Badge variant={buy ? "connected" : "disconnected"} data-testid="ap-direction">
            {a.direction}
          </Badge>
          <span className="text-xs text-muted">{strategyLabel(a.strategy)}</span>
          {a.source === "MANUAL" && <Badge variant="neutral">requested by {a.requested_by_email}</Badge>}
        </div>
        <div className="flex min-w-[160px] flex-col items-end gap-1" data-testid="ap-expiry">
          <span className={cn("tabular text-sm", remaining < 20 ? "text-loss" : "text-foreground")}>
            {expired ? "expired" : `expires in ${formatCountdown(remaining)}`}
          </span>
          <Progress
            value={(remaining / ttl) * 100}
            className="h-1 w-40"
            indicatorClassName={remaining < 20 ? "bg-loss" : undefined}
            aria-label="time left to decide"
          />
        </div>
      </div>

      <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4 xl:grid-cols-6">
        <Figure label="Entry" testId="ap-entry">{formatPrice(a.entry)}</Figure>
        <Figure label="Stop loss" testId="ap-stop" hint={`${formatPrice(a.risk_per_share)} per share`}>
          <span className="text-loss">{formatPrice(a.stop_loss)}</span>
        </Figure>
        <Figure label="Target" testId="ap-target">
          <span className="text-profit">{formatPrice(a.target)}</span>
        </Figure>
        <Figure label="Quantity" testId="ap-quantity" hint={`value ${formatInr(a.position_value)}`}>
          {a.quantity}
        </Figure>
        <Figure label="Risk" testId="ap-risk" hint="if the stop is hit, before costs">
          {formatInr(a.risk_amount)}
        </Figure>
        <Figure label="Risk / reward" testId="ap-rr">1 : {a.reward_risk.toFixed(2)}</Figure>
        <Figure label="Strategy" testId="ap-strategy" hint={a.strategy_version ?? undefined}>
          {strategyLabel(a.strategy)}
        </Figure>
        <Figure label="Score" testId="ap-score" hint={a.category ?? undefined}>
          {a.score?.toFixed(1) ?? "—"}
        </Figure>
        <Figure label="Market regime" testId="ap-regime" hint={a.regime_confidence !== null ? `${a.regime_confidence.toFixed(0)}% confidence` : undefined}>
          {a.market_regime ? <RegimeBadge regime={a.market_regime as MarketRegime} /> : "—"}
        </Figure>
        <Figure label="Signal time" testId="ap-signal-time">{formatIstTime(a.signal_timestamp, true)}</Figure>
        <Figure label="Requested" testId="ap-timestamp">{formatIstTime(a.created_at, true)}</Figure>
        <Figure label="Expires" hint={`${ttl}s window`}>{formatIstTime(a.expires_at, true)}</Figure>
      </div>

      <div data-testid="ap-reasons">
        <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Why</div>
        <ul className="grid grid-cols-1 gap-x-6 gap-y-0.5 text-xs text-muted lg:grid-cols-2">
          {a.reasons.map((r, i) => (
            <li key={i}>· {r}</li>
          ))}
        </ul>
      </div>

      <div className="flex flex-col gap-3 border-t border-border pt-3">
        {rejecting ? (
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="text"
              autoFocus
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              placeholder="Reason for rejecting (required)"
              aria-label="Rejection reason"
              maxLength={500}
              className="min-w-[260px] flex-1 rounded border border-border bg-surface-alt px-3 py-2 text-sm outline-none focus:border-accent"
            />
            <Button
              variant="outline"
              className="border-loss/60 text-loss hover:bg-loss/10"
              disabled={busy || reason.trim().length < 3}
              onClick={() => onReject(reason.trim())}
              data-testid="ap-confirm-reject"
            >
              Confirm rejection
            </Button>
            <Button variant="ghost" onClick={() => setRejecting(false)} disabled={busy}>
              Cancel
            </Button>
          </div>
        ) : (
          <div className="flex flex-wrap items-center gap-3">
            <Button
              onClick={onApprove}
              disabled={!canDecide || busy || expired}
              className="bg-profit text-accent-foreground hover:bg-profit/90"
              data-testid="ap-approve"
            >
              <Check className="h-4 w-4" aria-hidden />
              APPROVE TRADE
            </Button>
            <Button
              variant="outline"
              onClick={() => setRejecting(true)}
              disabled={!canDecide || busy || expired}
              className="border-loss/60 text-loss hover:bg-loss/10"
              data-testid="ap-reject"
            >
              <X className="h-4 w-4" aria-hidden />
              REJECT TRADE
            </Button>
            <span className="text-[11px] text-muted">
              Approving re-runs every risk check at the current price and places a PAPER market order of at most{" "}
              {a.quantity} shares.
            </span>
          </div>
        )}
      </div>
    </Card>
  );
}
