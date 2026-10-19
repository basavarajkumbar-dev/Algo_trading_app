/** Pure formatting helpers (unit-tested). Numbers are never invented: a
 * missing value renders as an em dash. */

export const DASH = "—";

export function num(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  return value.toLocaleString("en-IN", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

export function inr(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const sign = value < 0 ? "-" : "";
  return `${sign}₹${num(Math.abs(value), digits)}`;
}

export function signed(value: number | null | undefined, digits = 2, suffix = ""): string {
  if (value === null || value === undefined || Number.isNaN(value)) return DASH;
  const text = `${value > 0 ? "+" : ""}${num(value, digits)}${suffix}`;
  return text;
}

export function pct(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? DASH : `${num(value, digits)}%`;
}

export function rMultiple(value: number | null | undefined): string {
  return value === null || value === undefined ? DASH : signed(value, 2, "R");
}

/** "14:05 IST" or "18 Oct 14:05 IST" for older times. */
export function timeIst(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return DASH;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return DASH;
  const ist = new Date(date.getTime() + 330 * 60_000);
  const nowIst = new Date(now.getTime() + 330 * 60_000);
  const hh = String(ist.getUTCHours()).padStart(2, "0");
  const mm = String(ist.getUTCMinutes()).padStart(2, "0");
  const sameDay = ist.toISOString().slice(0, 10) === nowIst.toISOString().slice(0, 10);
  if (sameDay) return `${hh}:${mm} IST`;
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return `${ist.getUTCDate()} ${months.at(ist.getUTCMonth())} ${hh}:${mm} IST`;
}

/** Seconds since `iso`, for data-freshness labels. */
export function ageSeconds(iso: string | null | undefined, now: Date = new Date()): number | null {
  if (!iso) return null;
  const t = new Date(iso).getTime();
  return Number.isNaN(t) ? null : Math.max(0, Math.round((now.getTime() - t) / 1000));
}

export function ageLabel(seconds: number | null): string {
  if (seconds === null) return "no data";
  if (seconds < 60) return `${seconds}s ago`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m ago`;
  return `${Math.floor(seconds / 3600)}h ago`;
}

export function titleCase(text: string | null | undefined): string {
  if (!text) return DASH;
  const words = text.replace(/_/g, " ").toLowerCase();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export function pnlTone(value: number | null | undefined): "good" | "bad" | "neutral" {
  if (value === null || value === undefined || value === 0) return "neutral";
  return value > 0 ? "good" : "bad";
}
