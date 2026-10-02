import type {
  ExitReason,
  RejectionReason,
  TradeStatus,
  TradingStateValue,
} from "@/types/trading";

/** The lifecycle, in order, as drawn on the paper trading page. */
export const LIFECYCLE: TradingStateValue[] = [
  "SCANNING",
  "SIGNAL_FOUND",
  "RISK_CHECK",
  "ORDER_PENDING",
  "POSITION_ACTIVE",
  "EXIT",
  "CLOSED",
];

export const STATE_LABEL: Record<TradingStateValue, string> = {
  IDLE: "Idle",
  SCANNING: "Scanning",
  SIGNAL_FOUND: "Signal found",
  RISK_CHECK: "Risk check",
  ORDER_PENDING: "Order pending",
  POSITION_ACTIVE: "Position active",
  EXIT: "Exiting",
  CLOSED: "Closed",
};

/** Index of a state on the lifecycle strip; IDLE sits with SCANNING. */
export function lifecycleIndex(state: TradingStateValue): number {
  return state === "IDLE" ? 0 : LIFECYCLE.indexOf(state);
}

export const EXIT_REASON_LABEL: Record<ExitReason, string> = {
  STOP_LOSS: "Stop loss",
  TARGET: "Target",
  TRAILING_STOP: "Trailing stop",
  TIME_EXIT: "Square-off",
  MANUAL: "Manual exit",
  EMERGENCY: "Emergency stop",
  ENTRY_EXPIRED: "Entry expired",
  ENTRY_REJECTED: "Entry rejected",
};

export const REJECTION_LABEL: Record<RejectionReason, string> = {
  TRADING_MODE_NOT_PAPER: "Not in PAPER mode",
  EMERGENCY_STOP: "Emergency stop active",
  SYSTEM_UNHEALTHY: "System unhealthy",
  MARKET_CLOSED: "Market closed",
  DATA_STALE: "Data stale",
  ACTIVE_POSITION_EXISTS: "A trade is already active",
  DUPLICATE_ORDER: "Duplicate order",
  DAILY_LOSS_LIMIT: "Daily loss limit",
  MAX_TRADES_REACHED: "Max trades reached",
  MAX_CONSECUTIVE_LOSSES: "Consecutive loss limit",
  SIGNAL_NOT_EXECUTABLE: "Below executable score",
  SHORT_NOT_ALLOWED: "Shorts disabled",
  INVALID_STOP: "Invalid stop",
  INVALID_TARGET: "Invalid target",
  PRICE_MOVED_AWAY: "Price moved away",
  INVALID_QUANTITY: "Invalid quantity",
  INSUFFICIENT_CAPITAL: "Insufficient capital",
  MAX_EXPOSURE: "Max exposure",
};

export const TRADE_STATUS_VARIANT: Record<
  TradeStatus,
  "neutral" | "connected" | "pending" | "disconnected" | "accent"
> = {
  PENDING: "pending",
  OPEN: "accent",
  EXITING: "pending",
  CLOSED: "neutral",
  CANCELLED: "neutral",
  REJECTED: "disconnected",
  INTERRUPTED: "disconnected",
};

const inr = new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

/** ₹1,23,456.78 — Indian digit grouping; signed when asked. */
export function formatInr(value: number | null | undefined, signed = false): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const sign = value < 0 ? "−" : signed && value > 0 ? "+" : "";
  return `${sign}₹${inr.format(Math.abs(value))}`;
}

export function pnlTone(value: number | null | undefined): "text-profit" | "text-loss" | "text-muted" {
  if (value === null || value === undefined || value === 0) return "text-muted";
  return value > 0 ? "text-profit" : "text-loss";
}

export function formatR(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : value < 0 ? "−" : ""}${Math.abs(value).toFixed(2)}R`;
}

/** Share of a limit used, clamped to 0–100, for progress bars. */
export function usedPercent(used: number, limit: number): number {
  if (limit <= 0) return 100;
  return Math.min(100, Math.max(0, (used / limit) * 100));
}

export function humanCheck(name: string): string {
  return name.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
}
