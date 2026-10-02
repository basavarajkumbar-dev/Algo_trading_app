"use client";

import { Bell } from "lucide-react";
import Link from "next/link";

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { SEVERITY_DOT, timeAgo } from "@/lib/dashboard-format";
import { useSystemEvents } from "@/lib/queries";
import { countUnread, useUiStore } from "@/lib/stores/ui-store";
import { cn } from "@/lib/utils";

/**
 * Recent system events (paper orders, fills, closed trades, risk
 * rejections, emergency stops). Refreshed whenever a trading event
 * arrives over the WebSocket; "unread" is per browser.
 */
export function NotificationsMenu() {
  const { data, isError } = useSystemEvents();
  const seenAt = useUiStore((s) => s.notificationsSeenAt);
  const markSeen = useUiStore((s) => s.markNotificationsSeen);
  const items = data?.items ?? [];
  const unread = countUnread(
    items.map((e) => e.created_at),
    seenAt
  );

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        if (!open && items[0]) markSeen(items[0].created_at);
      }}
    >
      <DropdownMenuTrigger
        className="relative rounded p-2 text-muted hover:bg-surface-alt hover:text-foreground"
        aria-label={`Notifications${unread ? ` (${unread} unread)` : ""}`}
        data-testid="notifications-trigger"
      >
        <Bell className="h-4 w-4" aria-hidden />
        {unread > 0 && (
          <span
            className="absolute right-0.5 top-0.5 min-w-4 rounded-full bg-accent px-1 text-center text-[9px] font-semibold leading-4 text-accent-foreground"
            data-testid="notifications-count"
          >
            {unread > 9 ? "9+" : unread}
          </span>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-96">
        <DropdownMenuLabel>Notifications</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <div className="max-h-96 overflow-y-auto">
          {isError && <p className="px-2 py-3 text-xs text-loss">Could not load events.</p>}
          {!isError && items.length === 0 && (
            <p className="px-2 py-3 text-xs text-muted">
              No events yet. Paper orders, fills, closed trades, risk rejections and emergency stops
              appear here.
            </p>
          )}
          {items.map((e) => (
            <DropdownMenuItem key={e.id} className="items-start">
              <span
                className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", SEVERITY_DOT[e.severity] ?? "bg-muted")}
                aria-hidden
              />
              <span className="min-w-0 flex-1">
                <span className="block text-[10px] uppercase tracking-wide text-muted">
                  {e.event_type.replace(/^PAPER_/, "").replace(/_/g, " ").toLowerCase()}
                </span>
                <span className="block text-xs leading-snug">{e.message}</span>
              </span>
              <span className="tabular shrink-0 text-[10px] text-muted">{timeAgo(e.created_at)}</span>
            </DropdownMenuItem>
          ))}
        </div>
        <DropdownMenuSeparator />
        <DropdownMenuItem asChild>
          <Link href="/system" className="text-xs text-accent">
            System health and event log →
          </Link>
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
