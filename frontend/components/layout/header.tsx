"use client";

import { CircleUserRound, LogOut } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { NotificationsMenu } from "@/components/layout/notifications-menu";
import { RegimeBadge } from "@/components/market/regime-badge";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import {
  HEALTH_VARIANT,
  NIFTY_SYMBOL,
  algoStatus,
  marketStatus,
} from "@/lib/dashboard-format";
import { formatPrice } from "@/lib/intelligence-format";
import { useComponentHealth, useMarketIntelligence, useTradingStatus } from "@/lib/queries";
import { useMarketStore } from "@/lib/stores/market-store";
import { cn } from "@/lib/utils";

function Item({ label, children, hint }: { label: string; children: ReactNode; hint?: string }) {
  const body = (
    <div className="flex items-center gap-2">
      <span className="text-[10px] uppercase tracking-wide text-muted">{label}</span>
      {children}
    </div>
  );
  if (!hint) return body;
  return (
    <Tooltip>
      <TooltipTrigger asChild>{body}</TooltipTrigger>
      <TooltipContent>{hint}</TooltipContent>
    </Tooltip>
  );
}

const SOCKET_LABEL = {
  open: { text: "Live", dot: "bg-profit" },
  connecting: { text: "Connecting", dot: "bg-warning" },
  reconnecting: { text: "Reconnecting", dot: "bg-warning" },
  closed: { text: "Disconnected", dot: "bg-loss" },
} as const;

/**
 * Top bar: NIFTY (live over the WebSocket), market session, trading mode,
 * algo status, overall system health and notifications.
 */
export function Header() {
  const { user, logout } = useAuth();
  const trading = useTradingStatus().data;
  const health = useComponentHealth().data;
  const intelligence = useMarketIntelligence().data;
  const nifty = useMarketStore((s) => s.snapshots[NIFTY_SYMBOL]);
  const socket = useMarketStore((s) => s.status);

  const algo = algoStatus(trading);
  const market = marketStatus(trading);
  const sock = SOCKET_LABEL[socket];
  const change = nifty?.change_percent ?? null;

  return (
    <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-border bg-surface px-5">
      <div className="flex min-w-0 items-center gap-5 overflow-x-auto text-sm">
        <Item label="NIFTY 50" hint="Simulated NIFTY 50 index, streamed over the WebSocket">
          <span className="tabular font-medium" data-testid="topbar-nifty">
            {nifty ? formatPrice(nifty.price) : "—"}
          </span>
          {change !== null && (
            <span className={cn("tabular text-xs", change >= 0 ? "text-profit" : "text-loss")}>
              {change >= 0 ? "+" : ""}
              {change.toFixed(2)}%
            </span>
          )}
          {intelligence?.snapshot && (
            <RegimeBadge regime={intelligence.snapshot.market_regime} short className="text-[10px]" />
          )}
        </Item>
        <Item label="Market" hint={market.detail || "Market session"}>
          <Badge variant={market.variant} data-testid="topbar-market">
            {market.label}
          </Badge>
          <Badge variant="pending" className="hidden xl:inline-flex" title="No live feed is connected">
            Simulated data
          </Badge>
        </Item>
        <Item label="Mode">
          <Badge variant="accent" data-testid="topbar-mode">
            {trading?.mode ?? health?.trading_mode ?? "PAPER"}
          </Badge>
        </Item>
        <Item label="Algo">
          <Link href="/positions">
            <Badge variant={algo.variant} data-testid="topbar-algo">
              {algo.label}
            </Badge>
          </Link>
        </Item>
        <Item label="System" hint={health ? undefined : "Checking components"}>
          <Link href="/system">
            <Badge
              variant={health ? HEALTH_VARIANT[health.status] : "neutral"}
              data-testid="topbar-health"
            >
              {health?.status ?? "…"}
            </Badge>
          </Link>
        </Item>
      </div>

      <div className="flex shrink-0 items-center gap-3">
        <span className="flex items-center gap-1.5 text-[11px] text-muted" data-testid="socket-status">
          <span className={cn("h-1.5 w-1.5 rounded-full", sock.dot)} aria-hidden />
          {sock.text}
        </span>
        <NotificationsMenu />
        <div className="hidden items-center gap-2 text-sm text-muted lg:flex">
          <CircleUserRound className="h-5 w-5" aria-hidden />
          <span className="max-w-40 truncate">{user?.email ?? "Unknown user"}</span>
        </div>
        <button
          type="button"
          onClick={() => logout()}
          className="flex items-center gap-1.5 rounded p-2 text-muted hover:bg-surface-alt hover:text-foreground"
          aria-label="Log out"
        >
          <LogOut className="h-4 w-4" aria-hidden />
        </button>
      </div>
    </header>
  );
}
