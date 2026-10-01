"use client";

import { Bell, CircleUserRound, LogOut } from "lucide-react";

import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";

/**
 * Market shows "Simulated": the only data source is the mock provider
 * (no live feed, no broker). Algo stays "Stopped" until the paper
 * trading loop exists. The user section reflects the authenticated
 * user from AuthProvider and offers a working logout.
 */
export function Header() {
  const { user, logout } = useAuth();

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
          <Badge variant="neutral">Stopped</Badge>
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
