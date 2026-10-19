/** Market overview & regime, stock universe, scanner, signals, signal detail. */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import React, { useState } from "react";
import { Alert, Pressable, StyleSheet, Text } from "react-native";

import { describeError } from "../api/client";
import { api, type TradingSignal, type UniverseReport } from "../api/endpoints";
import { useAuth } from "../auth/AuthProvider";
import {
  Badge,
  Button,
  Card,
  Empty,
  ErrorState,
  Note,
  QueryView,
  Row,
  Screen,
  SectionTitle,
  SourceBadge,
  Sparkline,
  Stat,
  StatGrid,
} from "../components/ui";
import { ageLabel, ageSeconds, num, timeIst, titleCase } from "../lib/format";
import { usePoll, useStackNav } from "../lib/hooks";
import { colors } from "../theme";

// --- market overview & regime ------------------------------------------------------------

export function MarketScreen() {
  const status = usePoll(["market-status"], api.marketStatus, 15_000);
  const intel = usePoll(["intelligence"], api.intelligence, 15_000);
  const regime = usePoll(["regime"], api.regime, 30_000);
  const candles = usePoll(["candles", "NIFTY50"], () => api.candles("NIFTY50", "5m", 75), 30_000);
  const simulated = status.data ? status.data.data_source === "SIMULATED" : null;

  return (
    <Screen
      testID="market"
      refreshing={status.isFetching && !status.isLoading}
      onRefresh={() => {
        void status.refetch();
        void intel.refetch();
        void regime.refetch();
        void candles.refetch();
      }}
    >
      <SourceBadge simulated={simulated} source="ZERODHA" />
      <Card title="NIFTY 50 (5 minute)">
        {candles.data && candles.data.length > 1 ? (
          <>
            <Sparkline values={candles.data.map((c) => c.close)} baseline={candles.data[0].open} />
            <Row label="Last close" value={num(candles.data.at(-1)!.close)} />
            <Row label="Last candle" value={timeIst(candles.data.at(-1)!.timestamp)} />
          </>
        ) : candles.error ? (
          <ErrorState error={candles.error} />
        ) : (
          <Empty title="No candles yet" />
        )}
      </Card>
      <QueryView query={intel}>
        {(i) => (
          <Card title="Market snapshot" right={<Badge label={titleCase(i.snapshot.market_regime)} tone="accent" />}>
            <Row label="Direction" value={titleCase(i.snapshot.market_direction)} />
            <Row label="Confidence" value={num(i.snapshot.confidence, 0)} />
            <Row label="Volatility" value={titleCase(String(i.snapshot.volatility ?? "—"))} />
            <Row label="Trend strength" value={num(i.snapshot.trend_strength as number | null, 0)} />
            <Row label="Momentum" value={num(i.snapshot.momentum as number | null, 0)} />
            <Row label="As of" value={timeIst(i.snapshot.timestamp)} />
          </Card>
        )}
      </QueryView>
      <QueryView query={regime}>
        {(r) => (
          <Card title="Regime detail (NIFTY 50)">
            <Row label="Regime" value={titleCase(r.regime)} />
            <Row label="Confidence" value={num(r.confidence, 0)} />
            {(r.reasons ?? []).slice(0, 5).map((reason) => (
              <Note key={reason}>✓ {reason}</Note>
            ))}
            {(r.counter_reasons ?? []).slice(0, 3).map((reason) => (
              <Note key={reason}>✗ {reason}</Note>
            ))}
            <Note>Regimes describe the recent past, not a prediction.</Note>
          </Card>
        )}
      </QueryView>
      <QueryView query={status}>
        {(m) => (
          <Card title="Data feed">
            <Row label="Source" value={m.data_source} tone={m.data_source === "SIMULATED" ? "warn" : "good"} />
            <Row label="Status" value={m.data_status ?? "—"} />
            <Row label="Streaming" value={m.is_streaming ? "Yes" : "No"} />
            <Row label="Symbols watched" value={String(m.watchlist.length)} />
            <Row label="Ticks accepted / rejected" value={`${m.ticks_accepted} / ${m.ticks_rejected}`} />
            {m.notes.map((n) => (
              <Note key={n}>{n}</Note>
            ))}
          </Card>
        )}
      </QueryView>
    </Screen>
  );
}

// --- stock universe --------------------------------------------------------------------------

export function UniverseCounts({ report }: { report: UniverseReport }) {
  return (
    <>
      <StatGrid>
        <Stat label="Requested" value={String(report.requested_count)} />
        <Stat label="Eligible" value={String(report.eligible_count)} />
        <Stat label="Excluded" value={String(report.excluded_count)} tone={report.excluded_count ? "warn" : "neutral"} />
        <Stat label="Processed" value={String(report.processed_count)} tone="accent" />
      </StatGrid>
      {report.truncated ? (
        <Badge label={`CAPPED AT ${report.limit} SYMBOLS (configured limit)`} tone="warn" />
      ) : null}
      {report.duplicates_removed ? <Note>{report.duplicates_removed} duplicate symbols removed.</Note> : null}
      {Object.entries(report.excluded ?? {})
        .slice(0, 12)
        .map(([sym, why]) => (
          <Row key={sym} label={sym} value={why} mono={false} tone="warn" />
        ))}
      {report.excluded_count > 12 ? <Note>…and {report.excluded_count - 12} more.</Note> : null}
      {(report.sources ?? []).map((src) => (
        <Note key={src.universe_id}>
          {src.name}: {src.size} constituents · {src.source}
          {src.stale ? " (stale)" : ""}
          {src.fetched_at ? ` · fetched ${timeIst(src.fetched_at)}` : ""}
          {src.error ? ` · ${src.error}` : ""}
        </Note>
      ))}
      {(report.notes ?? []).map((n) => (
        <Note key={n}>{n}</Note>
      ))}
    </>
  );
}

export function UniverseScreen() {
  const list = usePoll(["universes"], api.universes, 60_000);
  const active = usePoll(["universe-active"], api.activeUniverse, 60_000);
  const [selected, setSelected] = useState<string[]>([]);
  const preview = useMutation({ mutationFn: (ids: string[]) => api.resolveUniverse(ids) });

  function toggle(id: string) {
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id]));
  }

  return (
    <Screen testID="universe" refreshing={list.isFetching && !list.isLoading} onRefresh={() => void list.refetch()}>
      <Card title="Scanner universe (active)">
        <QueryView query={active}>{(r) => <UniverseCounts report={r} />}</QueryView>
        <Note>
          Set on the backend with SCANNER_UNIVERSES (and SCANNER_MAX_SYMBOLS); applied at startup.
          Backtests choose their own universe per run.
        </Note>
      </Card>
      <SectionTitle>Available universes</SectionTitle>
      <QueryView query={list}>
        {(l) => (
          <>
            {l.universes.map((u) => (
              <Pressable key={u.id} onPress={() => toggle(u.id)} accessibilityRole="checkbox" accessibilityState={{ checked: selected.includes(u.id) }}>
                <Card
                  title={u.name}
                  right={<Badge label={selected.includes(u.id) ? "SELECTED" : u.group} tone={selected.includes(u.id) ? "accent" : "neutral"} />}
                  style={selected.includes(u.id) ? { borderColor: colors.accent } : undefined}
                >
                  <Row label="Cached list" value={u.cached ? `${u.size} symbols · ${u.source}${u.stale ? " (stale)" : ""}` : "Not downloaded yet"} mono={false} />
                  {u.notes ? <Note>{u.notes}</Note> : null}
                </Card>
              </Pressable>
            ))}
            <Button
              title={`Count combined universe (${selected.length})`}
              disabled={!selected.length}
              busy={preview.isPending}
              onPress={() => preview.mutate(selected)}
            />
            {preview.error ? <ErrorState error={preview.error} /> : null}
            {preview.data ? (
              <Card title="Combined (duplicates removed)">
                <UniverseCounts report={preview.data} />
              </Card>
            ) : null}
            <Note>{l.note}</Note>
          </>
        )}
      </QueryView>
    </Screen>
  );
}

// --- scanner ---------------------------------------------------------------------------------

export function ScannerScreen() {
  const nav = useStackNav();
  const scan = usePoll(["scanner"], api.scanner, 15_000);
  return (
    <Screen testID="scanner" refreshing={scan.isFetching && !scan.isLoading} onRefresh={() => void scan.refetch()}>
      <QueryView query={scan}>
        {({ scan: s, status }) => {
          const u = s.universe as unknown as UniverseReport | null;
          return (
            <>
              <Card title="Latest scan" right={<Badge label={ageLabel(ageSeconds(s.scanned_at))} tone="info" />}>
                <Row label="Scanned at" value={timeIst(s.scanned_at)} />
                <Row label="Duration" value={s.duration_ms != null ? `${num(s.duration_ms, 0)} ms` : "—"} />
                <Row label="Symbols scanned" value={String(s.symbols_scanned)} />
                <Row label="Executable threshold" value={num(status.executable_threshold, 0)} />
                {u ? <UniverseCounts report={u} /> : <Note>No universe report (backend predates universes).</Note>}
                {Object.keys(s.filtered_out).length ? (
                  <Note>Not scanned: {Object.entries(s.filtered_out).map(([k, v]) => `${k} (${v})`).join(", ")}</Note>
                ) : null}
              </Card>
              <SectionTitle>Ranked candidates ({s.candidates.length})</SectionTitle>
              {s.candidates.length ? (
                s.candidates.map((c) => (
                  <Pressable
                    key={`${c.symbol}-${c.strategy}-${c.direction}`}
                    onPress={() => c.signal_id && nav.navigate("SignalDetail", { id: c.signal_id })}
                    disabled={!c.signal_id}
                  >
                    <Card
                      title={`#${c.rank} ${c.symbol}`}
                      right={<Badge label={`${c.status} ${num(c.score, 0)}`} tone={c.status === "READY" ? "good" : c.status === "WATCH" ? "info" : "neutral"} />}
                    >
                      <Row label={titleCase(c.strategy)} value={c.direction} tone={c.direction === "BUY" ? "good" : "bad"} />
                      <Row label="Entry / stop / target" value={`${num(c.entry)} / ${num(c.stop_loss)} / ${num(c.target)}`} />
                      <Row label="Reward:risk" value={num(c.risk_reward)} />
                      <Row label="Regime" value={titleCase(c.market_regime)} />
                    </Card>
                  </Pressable>
                ))
              ) : (
                <Empty title="No candidates" detail="No strategy found an eligible setup in this scan." />
              )}
              <Note>{s.ranking_note}</Note>
            </>
          );
        }}
      </QueryView>
    </Screen>
  );
}

// --- signals ---------------------------------------------------------------------------------

export function SignalsScreen() {
  const nav = useStackNav();
  const signals = usePoll(["signals"], () => api.signals(100), 20_000);
  return (
    <Screen testID="signals" refreshing={signals.isFetching && !signals.isLoading} onRefresh={() => void signals.refetch()}>
      <QueryView query={signals}>
        {(l) =>
          l.items.length ? (
            <>
              {l.items.map((item) => {
                const sig = item.signal;
                return (
                  <Pressable key={sig.id ?? `${sig.symbol}-${item.generated_at}`} onPress={() => sig.id && nav.navigate("SignalDetail", { id: sig.id })}>
                    <Card title={`${sig.symbol} · ${sig.direction}`} right={<Badge label={`${sig.category} ${num(sig.score, 0)}`} tone={sig.executable ? "good" : "neutral"} />}>
                      <Row label={titleCase(sig.strategy)} value={sig.status ?? "—"} />
                      <Row label="Entry type" value={sig.entry_type === "STOP" ? `Trigger ${num(sig.entry)}` : "Next open"} />
                      <Row label="Generated" value={timeIst(item.generated_at)} />
                    </Card>
                  </Pressable>
                );
              })}
              <Note>{l.note}</Note>
            </>
          ) : (
            <Empty title="No stored signals" detail="Signals scoring 70+ are stored as scans run." />
          )
        }
      </QueryView>
    </Screen>
  );
}

function invalidation(sig: TradingSignal): string {
  const side = sig.direction === "BUY" ? "below" : "above";
  if (sig.entry_type === "STOP") {
    return `No trade unless price trades through ${num(sig.entry)} within ${sig.entry_valid_bars ?? "?"} candles; setup invalid on a move ${side} ${num(sig.stop_loss)} first.`;
  }
  return `Setup invalid on a move ${side} the stop ${num(sig.stop_loss)}.`;
}

export function SignalDetailScreen({ route }: { route: { params: { id: string } } }) {
  const { user } = useAuth();
  const qc = useQueryClient();
  const query = usePoll(["signal", route.params.id], () => api.signal(route.params.id), 15_000);
  const execute = useMutation({
    mutationFn: () => api.executeSignal(route.params.id),
    onSuccess: (r) => {
      void qc.invalidateQueries({ queryKey: ["trading-status"] });
      Alert.alert(
        r.decision.approved ? "Paper order placed" : "Refused by the risk engine",
        r.decision.approved
          ? "A simulated order was sent to the paper broker."
          : r.decision.rejection_reasons.map((x) => titleCase(x)).join(", "),
      );
    },
    onError: (e) => Alert.alert("Not executed", describeError(e)),
  });
  const operator = user?.role === "ADMIN" || user?.role === "TRADER";

  return (
    <Screen testID="signal-detail" refreshing={query.isFetching && !query.isLoading} onRefresh={() => void query.refetch()}>
      <QueryView query={query}>
        {(stored) => {
          const sig = stored.signal;
          const fresh = ageSeconds(stored.last_seen_at);
          return (
            <>
              <Card
                title={`${sig.symbol} · NSE · ${sig.direction}`}
                right={<Badge label={sig.executable ? "EXECUTABLE" : sig.blocked_reason ?? "NOT EXECUTABLE"} tone={sig.executable ? "good" : "warn"} />}
              >
                <Row label="Strategy" value={`${titleCase(sig.strategy)} ${sig.strategy_version}`} mono={false} />
                <Row label="Signal candle" value={timeIst(sig.candle_time)} />
                <Row label="Status" value={sig.status ?? "—"} />
                <Row label="Confirmation" value={sig.entry_type === "STOP" ? "Waits for the trigger" : "Candle closed (confirmed)"} />
                <Row label="Data freshness" value={`last seen ${ageLabel(fresh)}`} tone={fresh !== null && fresh > 120 ? "warn" : "neutral"} />
              </Card>
              <Card title="Plan">
                <Row label={sig.entry_type === "STOP" ? "Entry trigger" : "Planned entry"} value={num(sig.entry)} />
                <Row label="Stop-loss" value={num(sig.stop_loss)} tone="bad" />
                <Row label="Target" value={num(sig.target)} tone="good" />
                <Row label="Reward:risk" value={num(sig.risk_reward)} />
                <Row label="Quantity candidate" value={String(sig.quantity_candidate)} />
                <Note>{sig.quantity_note}</Note>
                <Note>Invalidation: {invalidation(sig)}</Note>
              </Card>
              <Card title={`Score ${num(sig.score, 1)} (${sig.category})`} right={<Badge label="MODEL SCORE" tone="info" />}>
                {sig.breakdown.components.map((c) => (
                  <Row
                    key={c.key}
                    label={c.label}
                    value={c.applicable ? `${num(c.points, 1)} / ${num(c.effective_weight, 1)}` : "n/a"}
                    tone={c.applicable ? (c.passed ? "good" : "neutral") : "neutral"}
                  />
                ))}
                <Note>A score measures how much evidence agrees with the trade. It is not a probability of profit.</Note>
              </Card>
              <Card title="Market regime">
                <Row label="Regime" value={titleCase(sig.market_regime)} />
                <Row label="Confidence" value={num(sig.regime_confidence, 0)} />
              </Card>
              <Card title="Why (accepted / rejected checks)">
                {(sig.strategy_checks ?? []).map((c) => (
                  <Text key={c.key} style={[st.check, { color: c.passed ? colors.profit : c.required ? colors.loss : colors.muted }]}>
                    {c.passed ? "✓" : "✗"} {c.label}: {c.detail}
                  </Text>
                ))}
              </Card>
              <Button
                testID="execute-paper"
                title="Execute on paper"
                onPress={() => execute.mutate()}
                busy={execute.isPending}
                disabled={!operator || sig.status !== "ACTIVE"}
              />
              <Note>
                The backend re-runs every risk check (position size, daily loss, stale data, duplicate
                orders, market hours) before anything is placed; this button cannot skip them.
              </Note>
            </>
          );
        }}
      </QueryView>
    </Screen>
  );
}

const st = StyleSheet.create({
  check: { fontSize: 13, lineHeight: 19 },
});

