"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { Badge } from "@/components/ui/badge";
import { NAV_ITEMS, activeNavHref } from "@/components/layout/nav-items";
import { cn } from "@/lib/utils";

export function Sidebar() {
  const pathname = usePathname() ?? "/";
  const active = activeNavHref(pathname);

  return (
    <aside className="flex w-56 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />
        <span className="text-sm font-semibold tracking-wide">GAINZALGO</span>
        <span className="text-xs text-muted">PRO MAX</span>
      </div>

      <nav className="flex-1 overflow-y-auto py-3" aria-label="Main">
        <ul className="flex flex-col gap-0.5 px-2">
          {NAV_ITEMS.map((item) => {
            const Icon = item.icon;
            const isActive = active === item.href;
            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  aria-current={isActive ? "page" : undefined}
                  className={cn(
                    "flex items-center gap-2.5 rounded px-3 py-2 text-sm",
                    isActive
                      ? "bg-surface-alt font-medium text-foreground"
                      : "text-muted hover:bg-surface-alt/60 hover:text-foreground"
                  )}
                >
                  <Icon className={cn("h-4 w-4", isActive ? "text-accent" : "")} aria-hidden />
                  <span className="flex-1">{item.label}</span>
                  {item.tag && <span className="text-[10px] text-muted/70">{item.tag}</span>}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="border-t border-border px-4 py-3">
        <Badge variant="accent">PAPER MODE</Badge>
        <p className="mt-2 text-[10px] leading-snug text-muted">
          Simulated market data. No broker connected; no real orders.
        </p>
      </div>
    </aside>
  );
}
