"use client";

import { useEffect, useState } from "react";
import type { ReactNode } from "react";

import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { apiGet } from "@/lib/api";
import type { ComponentStatus, SystemHealthResponse } from "@/types/system";

type BackendReachability = "checking" | "connected" | "disconnected";

const POLL_INTERVAL_MS = 5000;

const COMPONENT_BADGE_VARIANT: Record<ComponentStatus, BadgeProps["variant"]> = {
  CONNECTED: "connected",
  PENDING: "pending",
  DISCONNECTED: "disconnected",
  NOT_CONFIGURED: "neutral",
};

const COMPONENT_LABEL: Record<ComponentStatus, string> = {
  CONNECTED: "Connected",
  PENDING: "Pending",
  DISCONNECTED: "Disconnected",
  NOT_CONFIGURED: "Not configured",
};

function BackendStatusBadge({ state }: { state: BackendReachability }) {
  if (state === "checking") {
    return <Badge variant="pending">Checking…</Badge>;
  }
  if (state === "connected") {
    return <Badge variant="connected">Connected</Badge>;
  }
  return <Badge variant="disconnected">Disconnected</Badge>;
}

export function SystemStatusPanel() {
  const [backendState, setBackendState] = useState<BackendReachability>("checking");
  const [health, setHealth] = useState<SystemHealthResponse | null>(null);
  const [lastCheckedAt, setLastCheckedAt] = useState<Date | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const result = await apiGet<SystemHealthResponse>("/system/health");
        if (cancelled) return;
        setHealth(result);
        setBackendState("connected");
      } catch {
        if (cancelled) return;
        setHealth(null);
        setBackendState("disconnected");
      } finally {
        if (!cancelled) setLastCheckedAt(new Date());
      }
    }

    poll();
    const interval = setInterval(poll, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const databaseHealth = health?.components.find((c) => c.name === "database");
  const redisHealth = health?.components.find((c) => c.name === "redis");

  return (
    <Card>
      <CardHeader>
        <CardTitle>System Status</CardTitle>
        {lastCheckedAt && (
          <span className="tabular text-xs text-muted">
            Last checked {lastCheckedAt.toLocaleTimeString()}
          </span>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <StatusRow label="Backend">
          <BackendStatusBadge state={backendState} />
        </StatusRow>
        <StatusRow label="Database">
          {databaseHealth ? (
            <Badge variant={COMPONENT_BADGE_VARIANT[databaseHealth.status]}>
              {COMPONENT_LABEL[databaseHealth.status]}
            </Badge>
          ) : (
            <Badge variant="neutral">Unknown</Badge>
          )}
        </StatusRow>
        <StatusRow label="Redis">
          {redisHealth ? (
            <Badge variant={COMPONENT_BADGE_VARIANT[redisHealth.status]}>
              {COMPONENT_LABEL[redisHealth.status]}
            </Badge>
          ) : (
            <Badge variant="neutral">Unknown</Badge>
          )}
        </StatusRow>

        {backendState === "disconnected" && (
          <p className="text-xs text-loss">
            Could not reach the backend at the configured API URL. If you just ran{" "}
            <code className="tabular">docker compose up --build</code>, the backend may still be
            starting.
          </p>
        )}

        {health && health.notes.length > 0 && (
          <ul className="mt-1 flex flex-col gap-1 border-t border-border pt-3 text-xs text-muted">
            {health.notes.map((note) => (
              <li key={note}>· {note}</li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}

function StatusRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-muted">{label}</span>
      {children}
    </div>
  );
}
