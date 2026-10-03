/** Phase 14 live safety layer (GET /live/status and friends). */

export interface PreTradeCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface PreTradeReport {
  ok: boolean;
  symbol: string;
  quantity: number | null;
  checked_at: string;
  checks: PreTradeCheck[];
}

export interface LiveTrade {
  id: string;
  correlation_id: string;
  signal_id: string | null;
  symbol: string;
  side: "LONG" | "SHORT";
  quantity: number;
  planned_entry: number;
  stop_loss: number;
  target: number;
  status:
    "PENDING_ENTRY" | "OPEN" | "EXITING" | "CLOSED" | "CANCELLED" | "FAILED";
  filled_quantity: number;
  exited_quantity: number;
  average_entry: number | null;
  average_exit: number | null;
  gross_pnl: number | null;
  exit_reason: string | null;
  opened_at: string;
  closed_at: string | null;
  notes: string[];
}

export interface LiveStatus {
  configured: boolean;
  execution_mode: "PAPER" | "ZERODHA";
  arming: {
    armed: boolean;
    armed_by_email: string | null;
    armed_at: string | null;
    expires_at: string | null;
    last_disarm_reason: string | null;
  };
  auto_trading: boolean;
  kill_switch: {
    engaged: boolean;
    reason: string | null;
    engaged_at: string | null;
    close_positions: boolean;
  };
  health: {
    status: string;
    age_seconds: number | null;
    checks: { name: string; ok: boolean; status: string; detail: string }[];
  };
  max_active_positions: number;
  active_trade: LiveTrade | null;
  recent_trades: LiveTrade[];
  unresolved: string | null;
  arm_blockers: string[];
  last_report: PreTradeReport | null;
  last_error: string | null;
  limits: Record<string, number | string | boolean>;
  confirmation_phrase: string;
}

export interface LiveAuditItem {
  action: string;
  user_id: string | null;
  details: Record<string, unknown> | null;
  at: string;
}
