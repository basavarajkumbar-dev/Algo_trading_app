"use client";

import { Panel, errorText } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { HEALTH_DOT, HEALTH_VARIANT } from "@/lib/dashboard-format";
import { formatIstTime } from "@/lib/intelligence-format";
import { useComponentHealth } from "@/lib/queries";
import { useMarketStore } from "@/lib/stores/market-store";
import { cn } from "@/lib/utils";

/** Compact list of the eight components for the dashboard. */
export function SystemHealthList() {
  const health = useComponentHealth();
  const socket = useMarketStore((s) => s.status);
  const data = health.data;

  return (
    <Panel
      title="System Health"
      href="/system"
      linkLabel="System"
      aside={data && <Badge variant={HEALTH_VARIANT[data.status]}>{data.status}</Badge>}
      loading={health.isLoading}
      error={errorText(health.error)}
      testId="system-health-list"
    >
      {data && (
        <ul className="flex flex-col divide-y divide-border">
          {data.components.map((c) => (
            <li key={c.key} className="flex items-start gap-2 py-1.5" data-testid={`health-${c.key}`}>
              <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full", HEALTH_DOT[c.status])} aria-hidden />
              <span className="w-28 shrink-0 text-xs font-medium">{c.name}</span>
              <span className="min-w-0 flex-1 truncate text-[11px] text-muted" title={c.detail}>
                {c.key === "websocket" ? `${c.detail} · this tab: ${socket}` : c.detail}
              </span>
              <span className="sr-only">{c.status}</span>
            </li>
          ))}
        </ul>
      )}
      {data && <p className="mt-2 text-[10px] text-muted">Checked {formatIstTime(data.checked_at, true)}</p>}
    </Panel>
  );
}
