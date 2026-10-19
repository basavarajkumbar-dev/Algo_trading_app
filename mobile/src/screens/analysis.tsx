/** Backtesting, backtest detail, strategy analytics, performance reports,
 * strategy configuration. Every result is labelled simulated. */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import React, { useMemo, useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { describeError } from "../api/client";
import { api } from "../api/endpoints";
import { useAuth } from "../auth/AuthProvider";
import { Badge, Button, Card, Empty, Note, QueryView, Row, Screen, SectionTitle, Sparkline, Stat, StatGrid } from "../components/ui";
import { inr, num, pct, pnlTone, rMultiple, timeIst, titleCase } from "../lib/format";
import { usePoll, useStackNav } from "../lib/hooks";
import { colors, fonts, radius, space } from "../theme";

/** The fields of backend BacktestMetrics the app shows. */
interface Metrics {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  expectancy_r: number | null;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  charges: number;
  return_percent: number;
  max_drawdown: number;
  max_drawdown_percent: number;
  average_win: number | null;
  average_loss: number | null;
  trades_per_day: number | null;
  exit_reasons: Record<string, number>;
}

interface Robustness {
  enough_trades: boolean;
  mean_r: number | null;
  r_ci95: [number, number] | null;
  flags: string[];
}

function daysAgo(n: number): string {
  const d = new Date(Date.now() - n * 86_400_000);
  return d.toISOString().slice(0, 10);
}

// --- backtests -------------------------------------------------------------------------------

export function BacktestsScreen() {
  const nav = useStackNav();
  const qc = useQueryClient();
  const { user } = useAuth();
  const runs = usePoll(["backtests"], () => api.backtests(30), 10_000);
  const options = usePoll(["backtest-options"], api.backtestOptions, 300_000);
  const [strategy, setStrategy] = useState("ALL");
  const [universe, setUniverse] = useState("NIFTY50");
  const [source, setSource] = useState<"synthetic" | "zerodha">("synthetic");
  const [start, setStart] = useState(daysAgo(45));
  const [end, setEnd] = useState(daysAgo(1));
  const create = useMutation({
    mutationFn: () =>
      api.createBacktest({
        name: `Mobile ${strategy} ${universe}`,
        strategy: strategy as never,
        universes: [universe],
        start_date: start,
        end_date: end,
        data_source: { kind: source, seed: 42 } as never,
      }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["backtests"] }),
    onError: (e) => Alert.alert("Backtest not started", describeError(e)),
  });
  const operator = user?.role === "ADMIN" || user?.role === "TRADER";
  const zerodha = options.data?.data_sources.find((d) => d.kind === "zerodha");

  return (
    <Screen testID="backtests" refreshing={runs.isFetching && !runs.isLoading} onRefresh={() => void runs.refetch()}>
      <Card title="New backtest" right={<Badge label="SIMULATED RESULTS" tone="warn" />}>
        <Text style={s.label}>Strategy</Text>
        <Chips
          values={(options.data?.strategies ?? []).map((o) => ({ value: String(o.value), label: o.label }))}
          selected={strategy}
          onSelect={setStrategy}
        />
        <Text style={s.label}>Universe</Text>
        <Chips
          values={(options.data?.universes ?? []).map((u) => ({ value: String(u.id), label: String(u.name) }))}
          selected={universe}
          onSelect={setUniverse}
        />
        <Text style={s.label}>Data</Text>
        <Chips
          values={[
            { value: "synthetic", label: "Synthetic (machinery test)" },
            ...(zerodha ? [{ value: "zerodha", label: zerodha.available ? "Zerodha (real)" : "Zerodha (log in first)" }] : []),
          ]}
          selected={source}
          onSelect={(v) => setSource(v as "synthetic" | "zerodha")}
        />
        <View style={s.dates}>
          <TextInput style={s.input} value={start} onChangeText={setStart} placeholder="Start YYYY-MM-DD" placeholderTextColor={colors.faint} />
          <TextInput style={s.input} value={end} onChangeText={setEnd} placeholder="End YYYY-MM-DD" placeholderTextColor={colors.faint} />
        </View>
        <Button title="Run backtest" onPress={() => create.mutate()} busy={create.isPending} disabled={!operator} />
        <Note>
          Limit {options.data?.limits.max_symbols ?? "—"} symbols per run (BACKTEST_MAX_SYMBOLS); the run
          records requested, eligible, excluded and processed counts. Synthetic data tests the rules, not
          a market edge. A handful of trades is not evidence of an edge.
        </Note>
      </Card>
      <SectionTitle>Runs</SectionTitle>
      <QueryView query={runs}>
        {(r) =>
          r.items.length ? (
            r.items.map((run) => {
              const head = (run.headline ?? {}) as { total_trades?: number; net_pnl?: number; expectancy_r?: number };
              const synthetic = (run.data_source as { synthetic?: boolean })?.synthetic;
              return (
                <Pressable key={run.id} onPress={() => nav.navigate("BacktestDetail", { id: run.id })}>
                  <Card title={run.name ?? titleCase(run.strategy)} right={<Badge label={run.status} tone={run.status === "COMPLETED" ? "good" : run.status === "FAILED" ? "bad" : "info"} />}>
                    <Row label="Range" value={`${run.start_date} → ${run.end_date}`} />
                    <Row label="Symbols" value={String(run.symbols.length)} />
                    <Row label="Data" value={synthetic ? "Synthetic" : "Real (Zerodha/CSV)"} tone={synthetic ? "warn" : "good"} />
                    {head.total_trades !== undefined ? (
                      <Row label="Trades · net · expectancy" value={`${head.total_trades} · ${inr(head.net_pnl ?? null, 0)} · ${rMultiple(head.expectancy_r ?? null)}`} />
                    ) : null}
                    {run.status === "RUNNING" || run.status === "QUEUED" ? <Row label="Progress" value={pct(run.progress * 100, 0)} /> : null}
                  </Card>
                </Pressable>
              );
            })
          ) : (
            <Empty title="No backtests yet" />
          )
        }
      </QueryView>
    </Screen>
  );
}

function Chips({ values, selected, onSelect }: { values: { value: string; label: string }[]; selected: string; onSelect: (v: string) => void }) {
  return (
    <View style={s.chips}>
      {values.map((v) => (
        <Pressable
          key={v.value}
          onPress={() => onSelect(v.value)}
          style={[s.chip, v.value === selected && { borderColor: colors.accent, backgroundColor: colors.accentDim }]}
          accessibilityRole="radio"
          accessibilityState={{ selected: v.value === selected }}
        >
          <Text style={[s.chipText, v.value === selected && { color: colors.accent }]} numberOfLines={1}>
            {v.label}
          </Text>
        </Pressable>
      ))}
    </View>
  );
}

export function BacktestDetailScreen({ route }: { route: { params: { id: string } } }) {
  const run = usePoll(["backtest", route.params.id], () => api.backtest(route.params.id), 5_000);
  return (
    <Screen testID="backtest-detail" refreshing={run.isFetching && !run.isLoading} onRefresh={() => void run.refetch()}>
      <QueryView query={run}>
        {(r) => {
          const m = r.metrics as unknown as Metrics | null;
          const rob = r.robustness as unknown as Robustness | null;
          const result = (r.result ?? {}) as { warnings?: string[]; assumptions?: string[]; universe?: Record<string, unknown> };
          const u = result.universe as { requested_count?: number; eligible_count?: number; excluded_count?: number; processed_count?: number; missing_bars_total?: number } | undefined;
          return (
            <>
              <Card title={r.name ?? titleCase(r.strategy)} right={<Badge label={r.status} tone={r.status === "COMPLETED" ? "good" : "info"} />}>
                <Row label="Mode" value={titleCase(r.mode)} />
                <Row label="Range" value={`${r.start_date} → ${r.end_date}`} />
                <Row label="Data" value={(r.data_source as { synthetic?: boolean }).synthetic ? "Synthetic (simulated prices)" : "Real history"} tone={(r.data_source as { synthetic?: boolean }).synthetic ? "warn" : "good"} />
                {r.error ? <Note>Error: {r.error}</Note> : null}
              </Card>
              {u ? (
                <Card title="Universe and coverage">
                  <Row label="Requested / eligible" value={`${u.requested_count ?? "—"} / ${u.eligible_count ?? "—"}`} />
                  <Row label="Excluded / processed" value={`${u.excluded_count ?? "—"} / ${u.processed_count ?? "—"}`} />
                  <Row label="Missing 5m bars" value={String(u.missing_bars_total ?? "—")} />
                </Card>
              ) : null}
              {m ? (
                <Card title="Results (simulated)" right={<Badge label={rob?.enough_trades ? "SAMPLE OK" : "TOO FEW TRADES"} tone={rob?.enough_trades ? "good" : "warn"} />}>
                  <StatGrid>
                    <Stat label="Trades" value={String(m.total_trades)} hint={`${m.wins} W / ${m.losses} L`} />
                    <Stat label="Win rate" value={pct(m.win_rate)} />
                    <Stat label="Net P&L" value={inr(m.net_pnl, 0)} tone={pnlTone(m.net_pnl)} />
                    <Stat label="Expectancy" value={rMultiple(m.expectancy_r)} tone={pnlTone(m.expectancy_r)} />
                  </StatGrid>
                  <Row label="Gross profit / loss" value={`${inr(m.gross_profit, 0)} / ${inr(m.gross_loss, 0)}`} />
                  <Row label="Profit factor" value={num(m.profit_factor)} />
                  <Row label="Average win / loss" value={`${inr(m.average_win, 0)} / ${inr(m.average_loss, 0)}`} />
                  <Row label="Max drawdown" value={`${inr(m.max_drawdown, 0)} (${pct(m.max_drawdown_percent)})`} tone="bad" />
                  <Row label="Costs paid" value={inr(m.charges, 0)} />
                  <Row label="Trades per day" value={num(m.trades_per_day)} />
                  {Object.entries(m.exit_reasons).map(([k, v]) => (
                    <Row key={k} label={`Exit: ${titleCase(k)}`} value={String(v)} />
                  ))}
                  {rob?.r_ci95 ? <Note>95% interval of the mean R: {num(rob.r_ci95[0])} to {num(rob.r_ci95[1])}</Note> : null}
                  {rob?.flags.map((f) => (
                    <Note key={f}>⚠ {f}</Note>
                  ))}
                </Card>
              ) : (
                <Empty title={r.status === "COMPLETED" ? "No metrics" : `Status: ${r.status}`} detail={r.message ?? undefined} />
              )}
              {result.warnings?.length ? (
                <Card title="Warnings">
                  {result.warnings.map((w) => (
                    <Note key={w}>⚠ {w}</Note>
                  ))}
                </Card>
              ) : null}
              {result.assumptions?.length ? (
                <Card title="Execution assumptions">
                  {result.assumptions.map((a) => (
                    <Note key={a}>• {a}</Note>
                  ))}
                </Card>
              ) : null}
              <Note>{r.note}</Note>
            </>
          );
        }}
      </QueryView>
    </Screen>
  );
}

// --- performance ------------------------------------------------------------------------------

export function PerformanceScreen() {
  const perf = usePoll(["performance"], api.performance, 30_000);
  return (
    <Screen testID="performance" refreshing={perf.isFetching && !perf.isLoading} onRefresh={() => void perf.refetch()}>
      <Badge label="PAPER TRADING PERFORMANCE (SIMULATED)" tone="warn" />
      <QueryView query={perf}>
        {(p) => {
          const s0 = p.summary;
          return (
            <>
              <Card title="Summary">
                <StatGrid>
                  <Stat label="Closed trades" value={String(s0.closed_trades)} hint={`${s0.wins} W / ${s0.losses} L`} />
                  <Stat label="Win rate" value={pct(s0.win_rate)} />
                  <Stat label="Net P&L" value={inr(s0.net_pnl, 0)} tone={pnlTone(s0.net_pnl)} />
                  <Stat label="Average R" value={rMultiple(s0.average_r)} tone={pnlTone(s0.average_r)} />
                </StatGrid>
                <Row label="Profit factor" value={num(s0.profit_factor)} />
                <Row label="Expectancy" value={inr(s0.expectancy)} />
                <Row label="Average win / loss" value={`${inr(s0.average_win, 0)} / ${inr(s0.average_loss, 0)}`} />
                <Row label="Max drawdown" value={`${inr(s0.max_drawdown, 0)} (${pct(s0.max_drawdown_percent)})`} />
                <Row label="Charges" value={inr(s0.charges, 0)} />
                <Row label="Return" value={pct(s0.return_percent)} tone={pnlTone(s0.return_percent)} />
              </Card>
              {p.equity_curve.length > 1 ? (
                <Card title="Equity curve">
                  <Sparkline values={p.equity_curve.map((e) => e.equity)} baseline={s0.starting_capital} />
                </Card>
              ) : null}
              <Card title="By exit reason">
                {p.by_exit_reason.length ? (
                  p.by_exit_reason.map((g) => <Row key={g.key} label={titleCase(g.key)} value={`${g.trades} · ${inr(g.net_pnl, 0)}`} tone={pnlTone(g.net_pnl)} />)
                ) : (
                  <Empty title="No closed trades" />
                )}
              </Card>
              <Note>{p.note}</Note>
            </>
          );
        }}
      </QueryView>
    </Screen>
  );
}

// --- strategy analytics -----------------------------------------------------------------------

export function StrategyAnalyticsScreen() {
  const perf = usePoll(["performance"], api.performance, 30_000);
  const overview = usePoll(["strategies"], api.strategies, 30_000);
  const counts = useMemo(() => {
    const out: Record<string, { routed: number; eligible: number }> = {};
    for (const sym of overview.data?.symbols ?? []) {
      for (const name of sym.eligible_strategies) {
        out[name] ??= { routed: 0, eligible: 0 };
        out[name].routed += 1;
      }
      for (const c of sym.candidates ?? []) {
        const k = String(c.strategy);
        out[k] ??= { routed: 0, eligible: 0 };
        out[k].eligible += 1;
      }
    }
    return out;
  }, [overview.data]);

  return (
    <Screen testID="strategy-analytics" refreshing={perf.isFetching && !perf.isLoading} onRefresh={() => { void perf.refetch(); void overview.refetch(); }}>
      <Card title="Right now (latest evaluation)">
        {Object.keys(counts).length ? (
          Object.entries(counts).map(([k, v]) => <Row key={k} label={titleCase(k)} value={`${v.eligible} eligible of ${v.routed} routed`} />)
        ) : (
          <Empty title="No strategy was routed" detail="High volatility or unclear regimes route nothing by design." />
        )}
        {overview.data ? <Note>{overview.data.status.symbols_evaluated} symbols evaluated · updated {timeIst(overview.data.status.last_refresh_at)}</Note> : null}
      </Card>
      <SectionTitle>Paper results by strategy (simulated)</SectionTitle>
      <QueryView query={perf}>
        {(p) =>
          p.by_strategy.length ? (
            p.by_strategy.map((g) => (
              <Card key={g.key} title={titleCase(g.key)}>
                <Row label="Trades" value={`${g.trades} (${g.wins} W / ${g.losses} L)`} />
                <Row label="Win rate" value={pct(g.win_rate)} />
                <Row label="Net P&L" value={inr(g.net_pnl, 0)} tone={pnlTone(g.net_pnl)} />
                <Row label="Profit factor" value={num(g.profit_factor)} />
                <Row label="Average R" value={rMultiple(g.average_r)} tone={pnlTone(g.average_r)} />
              </Card>
            ))
          ) : (
            <Empty title="No closed paper trades yet" />
          )
        }
      </QueryView>
      <Note>Implemented strategies are hypotheses. None is assumed to be profitable.</Note>
    </Screen>
  );
}

// --- strategy configuration -----------------------------------------------------------------

export function StrategyConfigScreen() {
  const config = usePoll(["strategy-config"], api.strategyConfig, 300_000);
  const signals = usePoll(["signal-config"], api.signalConfig, 300_000);
  return (
    <Screen testID="strategy-config">
      <QueryView query={config}>
        {(c) => {
          const strategies: [string, { enabled: boolean } | undefined][] = [
            ["Trend continuation", c.trend_continuation],
            ["Breakout momentum", c.breakout_momentum],
            ["Mean reversion", c.mean_reversion],
            ["Range trading (PDF)", c.range_trading],
            ["Breakout trading, confirmed (PDF)", c.breakout_trading],
            ["Volume-based (PDF)", c.volume_trading],
            ["MA crossover (PDF)", c.ma_crossover],
          ];
          return (
            <Card title="Strategies">
              {strategies.map(([label, cfg]) => (
                <Row key={label} label={label} value={cfg?.enabled ? "Enabled" : "Off"} tone={cfg?.enabled ? "good" : "neutral"} />
              ))}
              <Row label="Min regime confidence" value={num(c.router?.min_regime_confidence, 0)} />
              <Note>
                Configuration lives on the backend (versioned: every signal carries its strategy version).
                Enable the PDF strategies with STRATEGY_EXTRA_ENABLED; try them first in backtests.
              </Note>
            </Card>
          );
        }}
      </QueryView>
      <QueryView query={signals}>
        {(s0) => (
          <Card title="Signal scoring">
            <Row label="Executable threshold" value={num(s0.categories?.executable_threshold, 0)} />
            <Row label="Store signals from" value={num(s0.store_min_score, 0)} />
            {Object.entries(s0.scoring?.weights ?? {}).map(([k, w]) => (
              <Row key={k} label={titleCase(k)} value={num(w as number, 0)} />
            ))}
          </Card>
        )}
      </QueryView>
    </Screen>
  );
}

const s = StyleSheet.create({
  label: { color: colors.muted, fontSize: 12, marginTop: space.xs },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: space.xs },
  chip: {
    borderWidth: 1,
    borderColor: colors.border,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 6,
    maxWidth: "100%",
  },
  chipText: { color: colors.text, fontSize: 12 },
  dates: { flexDirection: "row", gap: space.sm },
  input: {
    flex: 1,
    backgroundColor: colors.surfaceAlt,
    color: colors.text,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: space.md,
    paddingVertical: 10,
    fontFamily: fonts.mono,
  },
});
