import Link from "next/link";

import { Badge } from "@/components/ui/badge";
import { NAV_ITEMS } from "@/components/layout/nav-items";
import { cn } from "@/lib/utils";

const ACTIVE_ROUTES = new Set(["/", "/settings", "/market", "/price-action", "/strategies", "/scanner", "/signals"]);

export function Sidebar() {
  return (
    <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-surface">
      <div className="flex h-14 items-center gap-2 border-b border-border px-4">
        <span className="h-2 w-2 rounded-full bg-accent" aria-hidden />
        <span className="text-sm font-semibold tracking-wide">GAINZALGO</span>
        <span className="text-xs text-muted">PRO MAX</span>
      </div>

      <nav className="flex-1 overflow-y-auto py-3">
        <ul className="flex flex-col gap-0.5 px-2">
          {NAV_ITEMS.map((item) => {
            const isActive = ACTIVE_ROUTES.has(item.href);
            const Icon = item.icon;

            if (!isActive) {
              return (
                <li key={item.href}>
                  <div
                    className="flex cursor-not-allowed items-center justify-between rounded px-3 py-2 text-sm text-muted/50"
                    title="Not available yet"
                  >
                    <span className="flex items-center gap-2.5">
                      <Icon className="h-4 w-4" aria-hidden />
                      {item.label}
                    </span>
                    <span className="text-[10px] text-muted/50">soon</span>
                  </div>
                </li>
              );
            }

            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={cn(
                    "flex items-center gap-2.5 rounded px-3 py-2 text-sm font-medium",
                    "bg-surface-alt text-foreground"
                  )}
                >
                  <Icon className="h-4 w-4 text-accent" aria-hidden />
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      <div className="border-t border-border px-4 py-3">
        <Badge variant="accent">PAPER MODE</Badge>
      </div>
    </aside>
  );
}
