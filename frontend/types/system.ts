/**
 * Mirrors backend/app/schemas/system.py.
 *
 * Kept as a hand-maintained mirror in Phase 1. If the API surface grows
 * enough to justify it, a later phase may introduce generated types
 * instead — tracked as a possible improvement, not a Phase 1 task.
 */

export type TradingMode = "PAPER" | "APPROVAL" | "LIVE";

export type SystemHealthStatus = "HEALTHY" | "WARNING" | "CRITICAL" | "OFFLINE";

export type ComponentStatus = "CONNECTED" | "PENDING" | "DISCONNECTED" | "NOT_CONFIGURED";

export interface ComponentHealth {
  name: string;
  status: ComponentStatus;
  detail: string | null;
}

export interface SystemHealthResponse {
  status: SystemHealthStatus;
  app_name: string;
  app_env: string;
  demo_mode: boolean;
  trading_mode: TradingMode;
  live_trading_active: boolean;
  components: ComponentHealth[];
  notes: string[];
}
