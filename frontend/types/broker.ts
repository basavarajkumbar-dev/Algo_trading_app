/** GET /broker/status (Phase 11). No secrets are ever part of it. */
export type ConnectionState =
  | "NOT_CONFIGURED"
  | "DISCONNECTED"
  | "CONNECTING"
  | "CONNECTED"
  | "DEGRADED"
  | "AUTH_REQUIRED";

export interface BrokerStatus {
  execution_broker: string;
  trading_mode: string;
  live_trading_possible: boolean;
  live_blockers: string[];
  zerodha: {
    configured: boolean;
    state: ConnectionState;
    healthy: boolean;
    detail: string | null;
    checked_at: string | null;
    latency_ms: number | null;
    last_success_at: string | null;
    last_error: string | null;
    consecutive_failures: number;
    session_expires_at: string | null;
    live_orders_armed: boolean;
    market_protection: number;
    orders_per_second: number;
    supervisor_running: boolean;
    paused_by_user: boolean;
    supervisor_error: string | null;
  };
  order_manager: {
    tracked: number;
    working: number;
    unresolved: number;
    external_orders: number;
    resync_pending: boolean;
    last_reconcile_at: string | null;
    last_reconcile_ok: boolean | null;
    last_reconcile_error: string | null;
  };
  note: string;
}
