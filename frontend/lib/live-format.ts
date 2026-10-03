import type { LiveStatus, PreTradeCheck } from "@/types/live";

type Variant =
  "neutral" | "connected" | "pending" | "disconnected" | "accent" | "caution";

/** The one-line live state, most severe first. */
export function liveState(s: LiveStatus | undefined): {
  text: string;
  variant: Variant;
  detail: string;
} {
  if (!s) return { text: "…", variant: "neutral", detail: "" };
  if (s.kill_switch.engaged)
    return {
      text: "KILL SWITCH",
      variant: "disconnected",
      detail: s.kill_switch.reason ?? "engaged",
    };
  if (!s.configured)
    return {
      text: "PAPER",
      variant: "accent",
      detail:
        "Live is not configured (EXECUTION_MODE=ZERODHA and LIVE_TRADING_ENABLED=true)",
    };
  if (s.arming.armed)
    return {
      text: "LIVE ARMED",
      variant: "caution",
      detail: `armed by ${s.arming.armed_by_email ?? "admin"}`,
    };
  return {
    text: "LIVE DISARMED",
    variant: "pending",
    detail: s.arming.last_disarm_reason ?? "not armed",
  };
}

const CHECK_LABELS: Record<string, string> = {
  live_configured: "Live configured",
  armed: "Armed by an admin",
  broker_connected: "Broker connected",
  market_open: "Market open",
  market_data_healthy: "Market data healthy",
  websocket_healthy: "WebSocket healthy",
  database_healthy: "Database healthy",
  redis_healthy: "Redis healthy",
  broker_health: "Broker health",
  risk_engine_healthy: "Risk engine healthy",
  no_emergency_stop: "No emergency stop",
  no_unresolved_trade: "No unresolved trade",
  risk_limits_valid: "Risk limits valid",
  risk_approved: "Risk approved",
  account_balance: "Account balance available",
  no_duplicate_position: "No duplicate position",
  no_stale_signal: "No stale signal",
  valid_quantity: "Valid quantity",
  valid_stop: "Valid stop loss",
  valid_target: "Valid target",
};

export function checkLabel(c: PreTradeCheck): string {
  return CHECK_LABELS[c.name] ?? c.name;
}

/** Execute is offered only when armed, no kill switch and every check passed. */
export function canExecute(
  s: LiveStatus | undefined,
  checks: PreTradeCheck[] | undefined,
): boolean {
  return Boolean(
    s?.configured &&
    s.arming.armed &&
    !s.kill_switch.engaged &&
    checks &&
    checks.length > 0 &&
    checks.every((c) => c.passed),
  );
}
