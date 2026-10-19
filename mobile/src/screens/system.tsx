/** Notifications & alerts, system health & data freshness, the More menu. */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import React from "react";
import { Alert, Pressable, StyleSheet, Text, View } from "react-native";

import { describeError } from "../api/client";
import { api } from "../api/endpoints";
import { useAuth } from "../auth/AuthProvider";
import { Badge, Button, Card, Empty, Note, QueryView, Row, Screen, SectionTitle } from "../components/ui";
import { isEncrypted } from "../config";
import { ageLabel, ageSeconds, inr, num, timeIst, titleCase } from "../lib/format";
import { usePoll, useStackNav } from "../lib/hooks";
import type { RootStackParamList } from "../navigation/types";
import { colors, radius, space, type Tone } from "../theme";

function severityTone(severity: string): Tone {
  const s = severity.toUpperCase();
  if (s === "CRITICAL" || s === "ERROR") return "bad";
  if (s === "WARNING") return "warn";
  return "neutral";
}

function healthTone(status: string): Tone {
  const s = status.toUpperCase();
  if (s === "HEALTHY" || s === "OK" || s === "UP") return "good";
  if (s === "DEGRADED" || s === "WARNING" || s === "STALE") return "warn";
  if (s === "UNKNOWN" || s === "SIMULATED") return "neutral";
  return "bad";
}

// --- notifications & alerts -------------------------------------------------------------------

export function NotificationsScreen() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const events = usePoll(["events"], () => api.events(60), 15_000);
  const approvals = usePoll(["approvals"], api.approvals, 10_000);
  const decide = useMutation({
    mutationFn: ({ id, ok }: { id: string; ok: boolean }) => (ok ? api.approve(id) : api.reject(id)),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["approvals"] }),
    onError: (e) => Alert.alert("Not done", describeError(e)),
  });
  const pending = (approvals.data?.items ?? []).filter((a) => a.status === "PENDING");
  const operator = user?.role === "ADMIN" || user?.role === "TRADER";

  return (
    <Screen testID="notifications" refreshing={events.isFetching && !events.isLoading} onRefresh={() => { void events.refetch(); void approvals.refetch(); }}>
      <SectionTitle>Waiting for approval ({pending.length})</SectionTitle>
      {pending.length ? (
        pending.map((a) => (
          <Card key={a.id} title={`${a.side} ${a.quantity} ${a.symbol}`} right={<Badge label={`expires ${timeIst(a.expires_at)}`} tone="warn" />}>
            <Row label="Strategy · score" value={`${titleCase(a.strategy)} · ${num(a.score, 0)}`} />
            <Row label="Entry / stop / target" value={`${num(a.entry)} / ${num(a.stop_loss)} / ${num(a.target)}`} />
            <Row label="Risk" value={inr(a.risk_amount)} />
            <View style={st.two}>
              <View style={{ flex: 1 }}>
                <Button title="Approve" tone="good" disabled={!operator} busy={decide.isPending} onPress={() => decide.mutate({ id: a.id, ok: true })} />
              </View>
              <View style={{ flex: 1 }}>
                <Button title="Reject" tone="bad" disabled={!operator} busy={decide.isPending} onPress={() => decide.mutate({ id: a.id, ok: false })} />
              </View>
            </View>
            <Note>Approving re-runs the risk checks on the backend before the paper order.</Note>
          </Card>
        ))
      ) : (
        <Empty title="Nothing to approve" detail={approvals.data ? `Mode ${approvals.data.mode}` : undefined} />
      )}
      <SectionTitle>Events</SectionTitle>
      <QueryView query={events}>
        {(l) =>
          l.items.length ? (
            l.items.map((e) => (
              <Card key={e.id} title={titleCase(e.event_type)} right={<Badge label={e.severity} tone={severityTone(e.severity)} />}>
                <Text style={st.message}>{e.message}</Text>
                <Note>{timeIst(e.created_at)}</Note>
              </Card>
            ))
          ) : (
            <Empty title="No events" />
          )
        }
      </QueryView>
      <Note>Push notifications are not enabled in this version; alerts appear here while the app is open.</Note>
    </Screen>
  );
}

// --- system health & data freshness -----------------------------------------------------------

export function SystemHealthScreen() {
  const { backendUrl } = useAuth();
  const health = usePoll(["health"], api.health, 15_000);
  const comps = usePoll(["components"], api.components, 15_000);
  const md = usePoll(["market-data-health"], api.marketDataHealth, 15_000);
  const zerodha = usePoll(["zerodha-status"], api.zerodhaStatus, 30_000);

  return (
    <Screen testID="system-health" refreshing={health.isFetching && !health.isLoading} onRefresh={() => { void health.refetch(); void comps.refetch(); void md.refetch(); }}>
      <Card title="Backend">
        <Row label="Address" value={backendUrl ?? "—"} mono={false} />
        <Row label="Encrypted" value={isEncrypted(backendUrl) ? "HTTPS" : "No (http)"} tone={isEncrypted(backendUrl) ? "good" : "warn"} />
        {health.data ? (
          <>
            <Row label="Status" value={titleCase(health.data.status)} tone={healthTone(String(health.data.status))} />
            <Row label="Environment" value={health.data.app_env} />
            <Row label="Trading mode" value={String(health.data.trading_mode)} />
            <Row label="Live trading active" value={health.data.live_trading_active ? "YES" : "No"} tone={health.data.live_trading_active ? "bad" : "good"} />
          </>
        ) : health.error ? (
          <Badge label="BACKEND UNREACHABLE" tone="bad" />
        ) : null}
      </Card>
      <QueryView query={comps}>
        {(c) => (
          <Card title="Components" right={<Badge label={String(c.status)} tone={healthTone(String(c.status))} />}>
            {c.components.map((x) => (
              <View key={x.key} style={st.comp}>
                <Row label={x.name} value={String(x.status)} tone={healthTone(String(x.status))} />
                <Note>{x.detail}</Note>
              </View>
            ))}
            <Note>Checked {timeIst(c.checked_at)} ({ageLabel(ageSeconds(c.checked_at))})</Note>
          </Card>
        )}
      </QueryView>
      <QueryView query={md}>
        {(m) => (
          <Card title="Market data freshness">
            {Object.entries(m)
              .filter(([, v]) => typeof v !== "object" || v === null)
              .slice(0, 14)
              .map(([k, v]) => (
                <Row key={k} label={titleCase(k)} value={String(v ?? "—")} />
              ))}
          </Card>
        )}
      </QueryView>
      <Card title="Broker">
        <Row label="Zerodha" value={zerodha.data ? (zerodha.data.connected ? "Connected" : titleCase(zerodha.data.auth)) : "—"} tone={zerodha.data?.connected ? "good" : "neutral"} />
        <Row label="Execution" value={zerodha.data?.execution_mode ?? "PAPER"} />
      </Card>
      <Note>Stale or missing market data makes every signal non-executable on the backend (NO TRADE).</Note>
    </Screen>
  );
}

// --- more menu --------------------------------------------------------------------------------

const MENU: { route: keyof RootStackParamList; title: string; detail: string }[] = [
  { route: "Market", title: "Market overview", detail: "NIFTY 50, regime, data feed" },
  { route: "Universe", title: "Stock universe", detail: "Indices, counts, exclusions" },
  { route: "Signals", title: "Trading signals", detail: "Stored signals with scores" },
  { route: "Positions", title: "Open positions", detail: "Paper positions" },
  { route: "Orders", title: "Orders", detail: "Execution status" },
  { route: "TradeHistory", title: "Trade history", detail: "Closed paper trades" },
  { route: "StrategyAnalytics", title: "Strategy analytics", detail: "Per-strategy results" },
  { route: "Performance", title: "Performance reports", detail: "P&L, drawdown, R" },
  { route: "Risk", title: "Risk management", detail: "Limits and today's state" },
  { route: "Notifications", title: "Notifications & approvals", detail: "Events and pending trades" },
  { route: "SystemHealth", title: "System health", detail: "Backend, data freshness" },
  { route: "StrategyConfig", title: "Strategy configuration", detail: "Versions and thresholds" },
  { route: "BrokerConnections", title: "Broker connections", detail: "Zerodha Kite Connect credentials and login" },
  { route: "Settings", title: "Settings", detail: "Trading mode, backend, account" },
];

export function MoreScreen() {
  const nav = useStackNav();
  return (
    <Screen testID="more">
      {MENU.map((m) => (
        <Pressable
          key={m.route}
          testID={`menu-${m.route}`}
          onPress={() => nav.navigate(m.route as never)}
          style={({ pressed }) => [st.item, pressed && { backgroundColor: colors.surfaceAlt }]}
        >
          <Text style={st.itemTitle}>{m.title}</Text>
          <Text style={st.itemDetail}>{m.detail}</Text>
        </Pressable>
      ))}
    </Screen>
  );
}

const st = StyleSheet.create({
  two: { flexDirection: "row", gap: space.sm },
  message: { color: colors.text, fontSize: 13, lineHeight: 18 },
  comp: { gap: 2 },
  item: {
    backgroundColor: colors.surface,
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: radius.md,
    padding: space.lg,
  },
  itemTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  itemDetail: { color: colors.muted, fontSize: 12, marginTop: 2 },
});
