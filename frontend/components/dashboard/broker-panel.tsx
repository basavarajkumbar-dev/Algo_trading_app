"use client";

import { Figure, Panel, errorText } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { formatIstTime } from "@/lib/intelligence-format";
import { useBrokerStatus } from "@/lib/queries";
import type { ConnectionState } from "@/types/broker";

const STATE_VARIANT: Record<ConnectionState, "neutral" | "connected" | "pending" | "disconnected"> = {
  NOT_CONFIGURED: "neutral",
  CONNECTED: "connected",
  CONNECTING: "pending",
  DEGRADED: "pending",
  AUTH_REQUIRED: "pending",
  DISCONNECTED: "disconnected",
};

const STATE_LABEL: Record<ConnectionState, string> = {
  NOT_CONFIGURED: "not configured",
  CONNECTED: "connected (read-only)",
  CONNECTING: "connecting",
  DEGRADED: "degraded",
  AUTH_REQUIRED: "login required",
  DISCONNECTED: "disconnected",
};

/** Execution broker, the Zerodha connection and why live orders are blocked. */
export function BrokerPanel() {
  const q = useBrokerStatus();
  const s = q.data;
  return (
    <Panel title="Broker" loading={q.isLoading} error={errorText(q.error)} testId="broker-panel">
      {s && (
        <div className="flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Figure label="Orders execute on" testId="broker-execution">
              <Badge variant="caution">{s.execution_broker.toUpperCase()}</Badge>
            </Figure>
            <Figure label="Zerodha" testId="broker-zerodha-state" hint={s.zerodha.detail ?? undefined}>
              <Badge variant={STATE_VARIANT[s.zerodha.state]}>{STATE_LABEL[s.zerodha.state]}</Badge>
            </Figure>
            <Figure label="Live orders" testId="broker-live-orders">
              {s.zerodha.live_orders_armed ? <Badge variant="disconnected">armed</Badge> : <Badge variant="neutral">disabled</Badge>}
            </Figure>
            <Figure label="Session expires" hint="Kite tokens expire 06:00 IST">
              {s.zerodha.session_expires_at ? formatIstTime(s.zerodha.session_expires_at) : "—"}
            </Figure>
            <Figure label="Orders tracked" hint={`${s.order_manager.working} working · ${s.order_manager.unresolved} unresolved`}>
              {s.order_manager.tracked}
            </Figure>
            <Figure
              label="Last reconciliation"
              hint={s.order_manager.last_reconcile_error ?? (s.order_manager.external_orders ? `${s.order_manager.external_orders} external order(s)` : undefined)}
            >
              {s.order_manager.last_reconcile_at
                ? `${formatIstTime(s.order_manager.last_reconcile_at)} ${s.order_manager.last_reconcile_ok ? "ok" : "failed"}`
                : "—"}
            </Figure>
            <Figure label="Market protection" hint="required by Zerodha for MARKET / SL-M">
              {s.zerodha.market_protection === -1 ? "auto (-1)" : `${s.zerodha.market_protection}%`}
            </Figure>
            <Figure label="Order rate cap" hint="Zerodha allows 10/s per account">
              {s.zerodha.orders_per_second}/s
            </Figure>
          </div>
          {s.zerodha.last_error && (
            <p className="text-xs text-warning" data-testid="broker-last-error">
              Last error: {s.zerodha.last_error}
            </p>
          )}
          <div className="border-t border-border pt-3">
            <div className="mb-1 text-[10px] uppercase tracking-wide text-muted">Live trading is blocked because</div>
            <ul className="flex flex-col gap-0.5 text-xs text-muted" data-testid="broker-blockers">
              {s.live_blockers.map((b) => (
                <li key={b}>· {b}</li>
              ))}
            </ul>
            <p className="mt-2 text-[11px] text-muted">{s.note}</p>
          </div>
        </div>
      )}
    </Panel>
  );
}
