"use client";

import { Bell, CircleUserRound, LogOut } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { getTradingStatusRequest } from "@/lib/trading-client";
import { STATE_LABEL } from "@/lib/trading-format";
import type { TradingStatus } from "@/types/trading";

type AlgoBadge = { label: string; variant: "neutral" | "connected" | "disconnected" | "accent" };

function algoBadge(status: TradingStatus | null): AlgoBadge {
  if (!status) return { label: "Unknown", variant: "neutral" };
  if (status.emergency_stop) return { label: "Emergency stop", variant: "disconnected" };
  if (status.active_trade) return { label: STATE_LABEL[status.state], variant: "accent" };
  return status.auto_trading
    ? { label: "Auto · scanning", variant: "connected" }
    : { label: "Stopped", variant: "neutral" };
}

/**
 * Market shows "Simulated": the only data source is the mock provider
 * (no live feed, no broker). Algo reflects the paper trading engine:
 * stopped, scanning automatically, the active trade's state, or an
 * emergency stop. The user section reflects the authenticated user
 * from AuthProvider and offers a working logout.
 */
export function Header() {
  const { user, logout } = useAuth();
  const [trading, setTrading] = useState<TradingStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      getTradingStatusRequest()
        .then((s) => !cancelled && setTrading(s))
        .catch(() => !cancelled && setTrading(null));
    }
    poll();
    const id = setInterval(poll, 5_000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);
  const algo = algoBadge(trading);

  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-surface px-6">
      <div className="flex items-center gap-6 text-sm">
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-muted">Market</span>
          <Badge variant="pending" title="Mock market data. No live feed or broker is connected.">
            Simulated
          </Badge>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-muted">Mode</span>
          <Badge variant="accent">PAPER</Badge>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs uppercase tracking-wide text-muted">Algo</span>
          <Link href="/positions" title="Paper trading engine">
            <Badge variant={algo.variant}>{algo.label}</Badge>
          </Link>
        </div>
      </div>

      <div className="flex items-center gap-4">
        <button
          type="button"
          className="rounded p-2 text-muted hover:bg-surface-alt hover:text-foreground"
          aria-label="Notifications"
        >
          <Bell className="h-4 w-4" aria-hidden />
        </button>
        <div className="flex items-center gap-2 text-sm text-muted">
          <CircleUserRound className="h-5 w-5" aria-hidden />
          <span>{user?.email ?? "Unknown user"}</span>
        </div>
        <button
          type="button"
          onClick={() => logout()}
          className="flex items-center gap-1.5 rounded p-2 text-muted hover:bg-surface-alt hover:text-foreground"
          aria-label="Log out"
          title="Log out"
        >
          <LogOut className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </header>
  );
}
