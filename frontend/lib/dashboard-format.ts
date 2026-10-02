import type { HealthLevel } from "@/types/dashboard";
import type { TradingStatus } from "@/types/trading";

export const NIFTY_SYMBOL = "NIFTY50";

export type BadgeVariant = "neutral" | "connected" | "pending" | "disconnected" | "accent";

export const HEALTH_VARIANT: Record<HealthLevel, BadgeVariant> = {
  HEALTHY: "connected",
  WARNING: "pending",
  CRITICAL: "disconnected",
};

export const HEALTH_DOT: Record<HealthLevel, string> = {
  HEALTHY: "bg-profit",
  WARNING: "bg-warning",
  CRITICAL: "bg-loss",
};

export const SEVERITY_DOT: Record<string, string> = {
  INFO: "bg-muted",
  WARNING: "bg-warning",
  CRITICAL: "bg-loss",
};

export interface AlgoBadge {
  label: string;
  variant: BadgeVariant;
}

const ACTIVE_LABEL: Partial<Record<TradingStatus["state"], string>> = {
  SIGNAL_FOUND: "Signal found",
  RISK_CHECK: "Risk check",
  ORDER_PENDING: "Order pending",
  POSITION_ACTIVE: "In position",
  EXIT: "Exiting",
};

/** Algo status for the top bar and the command center. */
export function algoStatus(status: TradingStatus | null | undefined): AlgoBadge {
  if (!status) return { label: "Unknown", variant: "neutral" };
  if (!status.is_running) return { label: "Offline", variant: "disconnected" };
  if (status.emergency_stop) return { label: "Emergency stop", variant: "disconnected" };
  const active = ACTIVE_LABEL[status.state];
  if (active) return { label: active, variant: "accent" };
  return status.auto_trading
    ? { label: "Auto · scanning", variant: "connected" }
    : { label: "Stopped", variant: "neutral" };
}

/** Market session for the top bar. */
export function marketStatus(status: TradingStatus | null | undefined): AlgoBadge & { detail: string } {
  if (!status) return { label: "Unknown", variant: "neutral", detail: "" };
  if (status.session_mode === "SIMULATED_24H") {
    return { label: "Simulated 24h", variant: "pending", detail: status.market_detail };
  }
  if (!status.market_open) return { label: "Closed", variant: "neutral", detail: status.market_detail };
  if (!status.entries_allowed) return { label: "No new entries", variant: "pending", detail: status.market_detail };
  return { label: "Open", variant: "connected", detail: status.market_detail };
}

export interface PositionGeometry {
  /** Positions along SL (0) → TARGET (1); may fall outside 0..1. */
  entry: number;
  current: number | null;
  /** current clamped into 0..1 for drawing */
  currentClamped: number | null;
  /** (current - entry) in units of the initial risk, signed by direction */
  rNow: number | null;
  beyondStop: boolean;
  beyondTarget: boolean;
}

/**
 * Lay out SL ── ENTRY ── CURRENT ── TARGET on one axis that runs from the
 * stop (0) to the target (1). Works for longs and shorts alike because
 * both the numerator and the span change sign together.
 */
export function positionGeometry(
  stop: number,
  entry: number,
  target: number,
  current: number | null | undefined
): PositionGeometry {
  const span = target - stop;
  const at = (x: number) => (span === 0 ? 0 : (x - stop) / span);
  const e = at(entry);
  if (current === null || current === undefined) {
    return { entry: e, current: null, currentClamped: null, rNow: null, beyondStop: false, beyondTarget: false };
  }
  const c = at(current);
  const risk = entry - stop; // signed: + for long, - for short
  return {
    entry: e,
    current: c,
    currentClamped: Math.min(1, Math.max(0, c)),
    rNow: risk === 0 ? null : (current - entry) / risk,
    beyondStop: c < 0,
    beyondTarget: c > 1,
  };
}

/** "1h 05m", "3m 12s" — how long a position has been open. */
export function formatDuration(fromIso: string | null | undefined, now: number = Date.now()): string {
  if (!fromIso) return "—";
  const seconds = Math.max(0, Math.floor((now - Date.parse(fromIso)) / 1000));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(s).padStart(2, "0")}s`;
}

export function formatRatio(value: number | null | undefined, digits = 2): string {
  return value === null || value === undefined ? "—" : value.toFixed(digits);
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}%`;
}

/** Strategy enum → readable label without importing the full map. */
export function strategyLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return value
    .toLowerCase()
    .split("_")
    .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
    .join(" ");
}

/** Time ago for notifications: "now", "4m", "2h", "3d". */
export function timeAgo(iso: string, now: number = Date.now()): string {
  const seconds = Math.max(0, Math.floor((now - Date.parse(iso)) / 1000));
  if (seconds < 45) return "now";
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.round(seconds / 3600)}h`;
  return `${Math.round(seconds / 86_400)}d`;
}
