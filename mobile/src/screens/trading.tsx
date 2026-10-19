/** Paper trading controls, open positions, orders, trade history, risk. */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import React from "react";
import { Alert } from "react-native";

import { describeError } from "../api/client";
import { api, type PaperTrade } from "../api/endpoints";
import { useAuth } from "../auth/AuthProvider";
import { Badge, Button, Card, Empty, Note, QueryView, Row, Screen, SectionTitle, Stat, StatGrid } from "../components/ui";
import { inr, num, pct, pnlTone, rMultiple, timeIst, titleCase } from "../lib/format";
import { usePoll } from "../lib/hooks";

function useOperator(): boolean {
  const { user } = useAuth();
  return user?.role === "ADMIN" || user?.role === "TRADER";
}

function confirm(title: string, message: string, action: () => void) {
  Alert.alert(title, message, [
    { text: "Cancel", style: "cancel" },
    { text: "Confirm", style: "destructive", onPress: action },
  ]);
}

// --- paper trading ---------------------------------------------------------------------------

export function PaperTradingScreen() {
  const qc = useQueryClient();
  const operator = useOperator();
  const status = usePoll(["trading-status"], api.tradingStatus, 5_000);
  const done = { onSuccess: () => void qc.invalidateQueries({ queryKey: ["trading-status"] }), onError: (e: unknown) => Alert.alert("Not done", describeError(e)) };
  const auto = useMutation({ mutationFn: (on: boolean) => api.setAuto(on), ...done });
  const exit = useMutation({ mutationFn: () => api.exitPosition(), ...done });
  const stop = useMutation({ mutationFn: () => api.emergencyStop("stopped from the Android app"), ...done });
  const reset = useMutation({ mutationFn: () => api.resetEmergency(), ...done });

  return (
    <Screen testID="paper" refreshing={status.isFetching && !status.isLoading} onRefresh={() => void status.refetch()}>
      <QueryView query={status}>
        {(t) => (
          <>
            <Card title="Simulated account" right={<Badge label="PAPER · NOT REAL MONEY" tone="warn" />}>
              <StatGrid>
                <Stat label="Equity" value={inr(t.balance.equity, 0)} />
                <Stat label="Available" value={inr(t.balance.available, 0)} />
                <Stat label="Realised P&L" value={inr(t.balance.realized_pnl, 0)} tone={pnlTone(t.balance.realized_pnl)} />
                <Stat label="Charges paid" value={inr(t.balance.charges_paid, 0)} />
              </StatGrid>
              <Row label="Starting capital" value={inr(t.balance.starting_capital, 0)} />
              <Row label="Slippage model" value={`${num(t.broker.slippage_bps, 1)} bps per fill`} />
            </Card>
            <Card title="Engine" right={<Badge label={t.emergency_stop ? "EMERGENCY STOP" : titleCase(t.state)} tone={t.emergency_stop ? "bad" : "accent"} />}>
              <Row label="Mode" value={t.mode} />
              <Row label="Auto trading" value={t.auto_trading ? "ON (top READY signal, risk-checked)" : "OFF"} mono={false} />
              <Row label="System" value={t.system_healthy ? "Healthy" : t.health_detail} tone={t.system_healthy ? "good" : "warn"} />
              <Row label="Session" value={t.market_detail} mono={false} />
              {t.emergency_reason ? <Note>Reason: {t.emergency_reason}</Note> : null}
              <Button
                testID="auto-toggle"
                title={t.auto_trading ? "Turn auto trading off" : "Turn auto trading on"}
                tone={t.auto_trading ? "neutral" : "accent"}
                disabled={!operator || t.emergency_stop}
                busy={auto.isPending}
                onPress={() =>
                  t.auto_trading
                    ? auto.mutate(false)
                    : confirm("Auto trading (paper)", "The paper engine will act on READY signals by itself. Continue?", () => auto.mutate(true))
                }
              />
              {t.active_trade ? (
                <Button
                  title={`Exit ${t.active_trade.symbol} at market`}
                  tone="warn"
                  disabled={!operator}
                  busy={exit.isPending}
                  onPress={() => confirm("Exit position", "Close the open paper position at market?", () => exit.mutate())}
                />
              ) : null}
              {t.emergency_stop ? (
                <Button title="Reset emergency stop" tone="neutral" disabled={!operator} busy={reset.isPending} onPress={() => reset.mutate()} />
              ) : (
                <Button
                  testID="emergency-stop"
                  title="Emergency stop"
                  tone="bad"
                  disabled={!operator}
                  busy={stop.isPending}
                  onPress={() => confirm("Emergency stop", "Stop all new entries now?", () => stop.mutate())}
                />
              )}
            </Card>
            <Note>{t.note}</Note>
          </>
        )}
      </QueryView>
    </Screen>
  );
}

// --- positions --------------------------------------------------------------------------------

function TradeCard({ t }: { t: PaperTrade }) {
  const pnl = t.status === "CLOSED" ? t.net_pnl : t.unrealized_pnl || t.net_pnl;
  return (
    <Card title={`${t.side} ${t.symbol}`} right={<Badge label={titleCase(t.status)} tone={t.status === "OPEN" ? "accent" : "neutral"} />}>
      <Row label="Strategy" value={titleCase(t.strategy)} />
      <Row label="Qty filled / open" value={`${t.filled_quantity} / ${t.open_quantity}`} />
      <Row label="Planned entry → fill" value={`${num(t.planned_entry)} → ${num(t.entry_price)}`} />
      <Row label="Stop / target" value={`${num(t.trailing_stop ?? t.stop_loss)} / ${num(t.target)}`} />
      <Row label="Exit" value={t.exit_price != null ? `${num(t.exit_price)} · ${titleCase(t.exit_reason)}` : "—"} />
      <Row label="Net P&L (after costs)" value={inr(pnl)} tone={pnlTone(pnl)} />
      <Row label="Charges" value={inr(t.charges)} />
      <Row label="R (fill to stop)" value={rMultiple(t.r_multiple)} tone={pnlTone(t.r_multiple)} />
      <Row label="Opened / closed" value={`${timeIst(t.opened_at)} / ${timeIst(t.closed_at)}`} />
    </Card>
  );
}

export function PositionsScreen() {
  const positions = usePoll(["positions"], api.positions, 5_000);
  const status = usePoll(["trading-status"], api.tradingStatus, 5_000);
  return (
    <Screen testID="positions" refreshing={positions.isFetching && !positions.isLoading} onRefresh={() => void positions.refetch()}>
      <Badge label="PAPER POSITIONS (SIMULATED)" tone="warn" />
      {status.data?.active_trade ? <TradeCard t={status.data.active_trade} /> : null}
      <QueryView query={positions}>
        {(p) =>
          p.positions.length ? (
            <>
              {p.positions.map((pos) => (
                <Card key={pos.symbol} title={pos.symbol}>
                  <Row label="Quantity" value={String(pos.quantity)} />
                  <Row label="Average price" value={num(pos.average_price)} />
                  <Row label="Last price" value={num(pos.last_price)} />
                  <Row label="Unrealised" value={inr(pos.unrealized_pnl)} tone={pnlTone(pos.unrealized_pnl)} />
                  <Row label="Realised" value={inr(pos.realized_pnl)} tone={pnlTone(pos.realized_pnl)} />
                </Card>
              ))}
              <Note>{p.note}</Note>
            </>
          ) : (
            <Empty title="No open positions" />
          )
        }
      </QueryView>
    </Screen>
  );
}

// --- orders -----------------------------------------------------------------------------------

export function OrdersScreen() {
  const orders = usePoll(["orders"], () => api.orders(100), 10_000);
  return (
    <Screen testID="orders" refreshing={orders.isFetching && !orders.isLoading} onRefresh={() => void orders.refetch()}>
      <Badge label="PAPER BROKER ORDERS (SIMULATED)" tone="warn" />
      <QueryView query={orders}>
        {(o) =>
          o.items.length ? (
            <>
              {o.items.map(({ order, purpose }) => (
                <Card
                  key={order.id}
                  title={`${order.side} ${order.quantity} ${order.symbol}`}
                  right={<Badge label={titleCase(order.status)} tone={order.status === "FILLED" ? "good" : order.status === "REJECTED" ? "bad" : "neutral"} />}
                >
                  <Row label="Type" value={`${order.order_type}${purpose ? ` · ${titleCase(purpose)}` : ""}`} />
                  {order.trigger_price ? <Row label="Trigger" value={num(order.trigger_price)} /> : null}
                  {order.price ? <Row label="Limit" value={num(order.price)} /> : null}
                  <Row label="Filled" value={`${order.filled_quantity} @ ${num(order.average_price)}`} />
                  <Row label="Placed" value={timeIst(order.placed_at)} />
                  {order.rejection_reason ? <Note>Rejected: {order.rejection_reason}</Note> : null}
                </Card>
              ))}
              <Note>{o.note}</Note>
            </>
          ) : (
            <Empty title="No orders yet" />
          )
        }
      </QueryView>
    </Screen>
  );
}

// --- trade history ----------------------------------------------------------------------------

export function TradeHistoryScreen() {
  const trades = usePoll(["trades"], () => api.trades(200), 20_000);
  return (
    <Screen testID="history" refreshing={trades.isFetching && !trades.isLoading} onRefresh={() => void trades.refetch()}>
      <Badge label="PAPER TRADES (SIMULATED FILLS AND COSTS)" tone="warn" />
      <QueryView query={trades}>
        {(t) =>
          t.items.length ? (
            <>
              {t.items.map((trade) => (
                <TradeCard key={trade.id} t={trade} />
              ))}
              <Note>{t.note}</Note>
            </>
          ) : (
            <Empty title="No trades yet" detail="Paper trades appear here once the engine takes one." />
          )
        }
      </QueryView>
    </Screen>
  );
}

// --- risk -------------------------------------------------------------------------------------

export function RiskScreen() {
  const risk = usePoll(["risk"], api.risk, 10_000);
  return (
    <Screen testID="risk" refreshing={risk.isFetching && !risk.isLoading} onRefresh={() => void risk.refetch()}>
      <QueryView query={risk}>
        {(r) => (
          <>
            <Card title="Today" right={<Badge label={r.snapshot.entries_blocked ? "ENTRIES BLOCKED" : "ENTRIES ALLOWED"} tone={r.snapshot.entries_blocked ? "bad" : "good"} />}>
              <StatGrid>
                <Stat label="Daily loss left" value={inr(r.snapshot.daily_loss_remaining, 0)} hint={`of ${inr(r.snapshot.daily_loss_limit_amount, 0)}`} />
                <Stat label="Trades left" value={String(r.snapshot.trades_remaining)} />
                <Stat label="Risk / trade" value={inr(r.snapshot.risk_per_trade_amount, 0)} />
                <Stat label="Losses until lockout" value={String(r.snapshot.consecutive_losses_remaining)} />
              </StatGrid>
              {r.snapshot.blocked_reasons.map((b) => (
                <Note key={b}>Blocked: {b}</Note>
              ))}
            </Card>
            <SectionTitle>Limits (server-enforced)</SectionTitle>
            <Card>
              <Row label="Risk per trade" value={pct(r.limits.risk_per_trade_percent, 2)} />
              <Row label="Daily loss limit" value={pct(r.limits.daily_loss_limit_percent, 2)} />
              <Row label="Max trades / day" value={String(r.limits.max_trades_per_day)} />
              <Row label="Max consecutive losses" value={String(r.limits.max_consecutive_losses)} />
              <Row label="Max position value" value={inr(r.limits.max_position_value, 0)} />
              <Row label="Max exposure" value={inr(r.limits.max_exposure, 0)} />
              <Row label="Max order quantity" value={String(r.limits.max_order_quantity)} />
              <Row label="Max entry slippage" value={`${num(r.limits.max_entry_deviation_r, 2)} R`} />
              <Row label="Max data age" value={`${num(r.limits.max_data_age_seconds, 0)} s`} />
              <Row label="Min signal score" value={num(r.limits.min_signal_score, 0)} />
              <Row label="Short selling" value={r.limits.allow_short ? "Allowed" : "Off"} />
              <Row label="Open positions" value="1 at a time" />
            </Card>
            <Note>{r.note}</Note>
            <Note>Limits are set on the backend; the app cannot loosen them.</Note>
          </>
        )}
      </QueryView>
    </Screen>
  );
}
