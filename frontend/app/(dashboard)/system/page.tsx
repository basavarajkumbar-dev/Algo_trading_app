"use client";

import { errorText, Figure, Panel } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { HEALTH_DOT, HEALTH_VARIANT, SEVERITY_DOT, timeAgo } from "@/lib/dashboard-format";
import { formatIstTime } from "@/lib/intelligence-format";
import { useComponentHealth, useSystemEvents, useSystemHealth } from "@/lib/queries";
import { useShallow } from "zustand/react/shallow";

import { useMarketStore } from "@/lib/stores/market-store";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";
import type { ComponentReport } from "@/types/dashboard";

function metricValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.length ? v.join(", ") : "none";
  if (typeof v === "number") return Number.isInteger(v) ? v.toLocaleString("en-IN") : v.toFixed(2);
  return String(v);
}

function ComponentCard({ c }: { c: ComponentReport }) {
  return (
    <Card className="flex flex-col gap-3 p-4" data-testid={`component-${c.key}`}>
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-sm font-medium">
          <span className={cn("h-2 w-2 rounded-full", HEALTH_DOT[c.status])} aria-hidden />
          {c.name}
        </span>
        <Badge variant={HEALTH_VARIANT[c.status]}>{c.status}</Badge>
      </div>
      <p className="text-xs leading-snug text-muted">{c.detail}</p>
      {Object.keys(c.metrics).length > 0 && (
        <dl className="grid grid-cols-2 gap-x-3 gap-y-1 border-t border-border pt-2 text-[11px]">
          {Object.entries(c.metrics).map(([k, v]) => (
            <div key={k} className="contents">
              <dt className="truncate text-muted">{k.replace(/_/g, " ")}</dt>
              <dd className="tabular truncate text-right">{metricValue(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </Card>
  );
}

export default function SystemPage() {
  const components = useComponentHealth();
  const health = useSystemHealth();
  const events = useSystemEvents();
  const socket = useMarketStore(
    useShallow((s) => ({ status: s.status, last: s.lastMessageAt, count: s.messagesReceived }))
  );
  const now = useNow();
  const data = components.data;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">System</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            Each component is HEALTHY, WARNING or CRITICAL with the reason. Checked every 10 seconds.
          </p>
        </div>
        {data && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-muted">Overall</span>
            <Badge variant={HEALTH_VARIANT[data.status]} data-testid="system-overall">
              {data.status}
            </Badge>
          </div>
        )}
      </div>

      {components.error && (
        <Card className="px-4 py-4 text-sm text-loss">
          Backend unreachable: {errorText(components.error)}. Every component is unknown until it answers.
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {data?.components.map((c) => <ComponentCard key={c.key} c={c} />)}
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
        <Panel title="This browser">
          <div className="grid grid-cols-2 gap-4">
            <Figure label="WebSocket" testId="browser-socket">
              <Badge variant={socket.status === "open" ? "connected" : socket.status === "closed" ? "disconnected" : "pending"}>
                {socket.status}
              </Badge>
            </Figure>
            <Figure label="Last message">
              {socket.last ? `${Math.max(0, Math.round((now - socket.last) / 1000))}s ago` : "—"}
            </Figure>
            <Figure label="Messages received">{socket.count.toLocaleString("en-IN")}</Figure>
            <Figure label="Demo mode">{health.data ? (health.data.demo_mode ? "on" : "off") : "—"}</Figure>
            <Figure label="Trading mode">{health.data?.trading_mode ?? "—"}</Figure>
            <Figure label="Environment">{health.data?.app_env ?? "—"}</Figure>
          </div>
          {health.data && (
            <ul className="mt-4 flex flex-col gap-1 border-t border-border pt-3 text-[11px] text-muted">
              {health.data.notes.map((n) => (
                <li key={n}>· {n}</li>
              ))}
            </ul>
          )}
        </Panel>

        <Panel title="Event log" bodyClassName="p-0" error={errorText(events.error)} testId="event-log">
          <div className="max-h-[420px] overflow-y-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Event</TableHead>
                  <TableHead>Message</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {events.data?.items.length === 0 && (
                  <TableRow>
                    <TableCell colSpan={3} className="py-6 text-center text-muted">
                      No events recorded yet.
                    </TableCell>
                  </TableRow>
                )}
                {events.data?.items.map((e) => (
                  <TableRow key={e.id}>
                    <TableCell className="tabular whitespace-nowrap text-muted" title={e.created_at}>
                      {formatIstTime(e.created_at, true)} <span className="text-[10px]">({timeAgo(e.created_at, now)})</span>
                    </TableCell>
                    <TableCell className="whitespace-nowrap">
                      <span className={cn("mr-1.5 inline-block h-1.5 w-1.5 rounded-full", SEVERITY_DOT[e.severity] ?? "bg-muted")} />
                      {e.event_type.replace(/^PAPER_/, "").replace(/_/g, " ").toLowerCase()}
                    </TableCell>
                    <TableCell className="text-muted">{e.message}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Panel>
      </div>
      {data && <p className="text-[11px] text-muted">{data.note}</p>}
    </div>
  );
}
