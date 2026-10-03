import type { MarketDataStatus, ZerodhaStatus } from "@/types/market-data";

type Variant = "neutral" | "connected" | "pending" | "disconnected" | "accent" | "caution";

export interface SourceLabel {
  text: string;
  variant: Variant;
  detail: string;
  live: boolean; // real exchange data that is fresh right now
}

/**
 * The DATA SOURCE badge. Simulated data is always labelled SIMULATED; real
 * data says ZERODHA LIVE only while ticks are fresh.
 */
export function dataSourceLabel(md: MarketDataStatus | undefined): SourceLabel {
  if (!md) return { text: "DATA SOURCE …", variant: "neutral", detail: "Checking the feed", live: false };
  if (md.simulated) {
    return {
      text: "SIMULATED",
      variant: "pending",
      detail: "Simulated prices from the mock provider — not real market data",
      live: false,
    };
  }
  switch (md.status) {
    case "LIVE":
      return {
        text: "ZERODHA LIVE",
        variant: "connected",
        detail: `Real NSE data from Kite Connect · ${md.subscribed_instruments} instruments`,
        live: true,
      };
    case "STALE":
      return {
        text: "ZERODHA · STALE",
        variant: "disconnected",
        detail: `No tick for ${md.last_tick_age_seconds?.toFixed(0) ?? "?"}s: signals and orders are blocked`,
        live: false,
      };
    case "WAITING":
      return { text: "ZERODHA · WAITING", variant: "pending", detail: "Connected, waiting for ticks", live: false };
    default:
      return {
        text: "ZERODHA · DISCONNECTED",
        variant: "disconnected",
        detail: "No live feed: log in to Zerodha (Settings) or check the connection",
        live: false,
      };
  }
}

/** Lower-case noun for page descriptions ("simulated" / "live Zerodha"). */
export function dataNoun(md: MarketDataStatus | undefined): string {
  if (!md || md.simulated) return "simulated";
  return "Zerodha";
}

/** The execution label never says LIVE unless the backend reports live execution. */
export function executionLabel(z: ZerodhaStatus | undefined): { text: string; variant: Variant } {
  if (z?.execution_mode === "ZERODHA") return { text: "LIVE (ZERODHA)", variant: "caution" };
  return { text: "PAPER", variant: "accent" };
}

export function brokerLabel(z: ZerodhaStatus | undefined): { text: string; variant: Variant } {
  if (!z) return { text: "…", variant: "neutral" };
  if (!z.configured) return { text: "NOT CONFIGURED", variant: "neutral" };
  return z.connected
    ? { text: "ZERODHA CONNECTED", variant: "connected" }
    : { text: "ZERODHA DISCONNECTED", variant: "disconnected" };
}

export function authLabel(z: ZerodhaStatus | undefined): { text: string; variant: Variant } {
  if (!z || !z.configured) return { text: "—", variant: "neutral" };
  switch (z.auth) {
    case "AUTHENTICATED":
      return { text: "AUTHENTICATED", variant: "connected" };
    case "TOKEN_EXPIRED":
      return { text: "EXPIRED", variant: "disconnected" };
    case "AUTHENTICATING":
      return { text: "AUTHENTICATING", variant: "pending" };
    case "AUTH_ERROR":
      return { text: "ERROR", variant: "disconnected" };
    case "LOGGED_OUT":
      return { text: "LOGGED OUT", variant: "neutral" };
    default:
      return { text: "LOGIN REQUIRED", variant: "pending" };
  }
}

export function marketDataLabel(md: MarketDataStatus | undefined): { text: string; variant: Variant } {
  if (!md) return { text: "…", variant: "neutral" };
  if (md.simulated) return { text: "SIMULATED", variant: "pending" };
  if (md.status === "LIVE") return { text: "LIVE", variant: "connected" };
  if (md.status === "STALE") return { text: "STALE", variant: "disconnected" };
  if (md.status === "WAITING") return { text: "WAITING", variant: "pending" };
  return { text: "DISCONNECTED", variant: "disconnected" };
}

/** Candle dot colour: forming (live) / closed / none. */
export function candleDot(state: string | undefined): string {
  if (state === "FORMING") return "bg-profit";
  if (state === "CLOSED") return "bg-accent";
  return "bg-border";
}
