/** Splash, backend setup, login, Command Center, Settings and Zerodha. */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import React, { useState } from "react";
import { Alert, KeyboardAvoidingView, Platform, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { ApiError, describeError } from "../api/client";
import { api, type TradingStatus } from "../api/endpoints";
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
  SourceBadge,
  Stat,
  StatGrid,
} from "../components/ui";
import { BUILD_BACKEND_URL, isEncrypted, normalizeUrl } from "../config";
import { ageLabel, ageSeconds, inr, num, pnlTone, timeIst, titleCase } from "../lib/format";
import { usePoll, useStackNav } from "../lib/hooks";
import { colors, fonts, radius, space } from "../theme";

// --- splash ------------------------------------------------------------------------------

export function SplashView() {
  return (
    <View style={s.splash} testID="splash">
      <Text style={s.brand}>GAINZALGO</Text>
      <Text style={s.brandSub}>PRO MAX</Text>
      <Text style={s.splashNote}>Connecting to your backend…</Text>
    </View>
  );
}

// --- backend address -----------------------------------------------------------------------

export function BackendSetupScreen({ onDone }: { onDone?: () => void }) {
  const { backendUrl, setBackendUrl } = useAuth();
  const [value, setValue] = useState(backendUrl ?? BUILD_BACKEND_URL ?? "https://");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const normalized = normalizeUrl(value);

  async function save() {
    setError(null);
    if (!normalized) {
      setError("Enter a full address such as https://api.example.com");
      return;
    }
    setBusy(true);
    try {
      await setBackendUrl(normalized);
      const health = await api.health(); // proves the address answers
      if (!health?.app_name) throw new Error("That address did not answer like a GainzAlgo backend.");
      onDone?.();
    } catch (e) {
      setError(describeError(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Screen testID="backend-setup">
      <Text style={s.h1}>Backend address</Text>
      <Note>
        The app is a client of your GainzAlgo backend service; strategies, risk checks and orders
        run there. Enter the address it is reachable at (HTTPS for anything outside your own network).
      </Note>
      <TextInput
        testID="backend-url"
        style={s.input}
        value={value}
        onChangeText={setValue}
        autoCapitalize="none"
        autoCorrect={false}
        keyboardType="url"
        placeholder="https://api.example.com"
        placeholderTextColor={colors.faint}
      />
      {normalized && !isEncrypted(normalized) ? (
        <Badge label="NOT ENCRYPTED (http) — development / private network only" tone="warn" />
      ) : null}
      {error ? <Text style={s.error}>{error}</Text> : null}
      <Button title="Save and test connection" onPress={save} busy={busy} testID="backend-save" />
      {BUILD_BACKEND_URL ? <Note>Build default: {BUILD_BACKEND_URL}</Note> : null}
    </Screen>
  );
}

// --- login -----------------------------------------------------------------------------------

export function LoginScreen() {
  const { signIn, register, backendUrl } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [mode, setMode] = useState<"login" | "register">("login");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editUrl, setEditUrl] = useState(false);

  if (editUrl || !backendUrl) return <BackendSetupScreen onDone={() => setEditUrl(false)} />;

  async function submit() {
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await signIn(email, password);
      else await register(email, password);
    } catch (e) {
      setError(
        e instanceof ApiError && e.kind === "UNAUTHORIZED" ? "Wrong email or password." : describeError(e),
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: colors.bg }} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <Screen testID="login">
        <Text style={s.brand}>GAINZALGO</Text>
        <Text style={s.brandSub}>PRO MAX · paper trading by default</Text>
        <Card>
          <TextInput
            testID="email"
            style={s.input}
            value={email}
            onChangeText={setEmail}
            placeholder="Email"
            placeholderTextColor={colors.faint}
            autoCapitalize="none"
            keyboardType="email-address"
          />
          <TextInput
            testID="password"
            style={s.input}
            value={password}
            onChangeText={setPassword}
            placeholder="Password"
            placeholderTextColor={colors.faint}
            secureTextEntry
          />
          {error ? <Text style={s.error} testID="login-error">{error}</Text> : null}
          <Button
            testID="login-submit"
            title={mode === "login" ? "Sign in" : "Create account"}
            onPress={submit}
            busy={busy}
            disabled={!email || password.length < 8}
          />
          <Pressable onPress={() => setMode(mode === "login" ? "register" : "login")}>
            <Text style={s.link}>{mode === "login" ? "New here? Create an account" : "Have an account? Sign in"}</Text>
          </Pressable>
        </Card>
        <Pressable onPress={() => setEditUrl(true)}>
          <Text style={s.link}>
            Backend: {backendUrl} {isEncrypted(backendUrl) ? "" : "(not encrypted)"} · change
          </Text>
        </Pressable>
        <Note>
          This is your GainzAlgo account. Your Zerodha login happens on Zerodha&apos;s own page later;
          the app never asks for your Zerodha password, API key or secret.
        </Note>
      </Screen>
    </KeyboardAvoidingView>
  );
}

// --- command center --------------------------------------------------------------------------

function stateTone(status: TradingStatus): "good" | "bad" | "warn" | "neutral" {
  if (status.emergency_stop) return "bad";
  if (!status.system_healthy) return "warn";
  return status.entries_allowed ? "good" : "neutral";
}

export function DashboardScreen() {
  const nav = useStackNav();
  const trading = usePoll(["trading-status"], api.tradingStatus, 5_000);
  const market = usePoll(["market-status"], api.marketStatus, 15_000);
  const intel = usePoll(["intelligence"], api.intelligence, 15_000);
  const scan = usePoll(["scanner"], api.scanner, 15_000);
  const zerodha = usePoll(["zerodha-status"], api.zerodhaStatus, 30_000);
  const refreshing = trading.isFetching && !trading.isLoading;
  const simulated = market.data ? market.data.data_source === "SIMULATED" : null;

  return (
    <Screen
      testID="dashboard"
      refreshing={refreshing}
      onRefresh={() => {
        void trading.refetch();
        void market.refetch();
        void intel.refetch();
        void scan.refetch();
      }}
    >
      <View style={s.headerRow}>
        <SourceBadge simulated={simulated} source="ZERODHA" />
        <Badge
          label={zerodha.data ? (zerodha.data.connected ? "ZERODHA CONNECTED" : "ZERODHA NOT CONNECTED") : "ZERODHA …"}
          tone={zerodha.data?.connected ? "good" : "neutral"}
        />
      </View>
      <QueryView query={trading}>
        {(t) => (
          <Card
            title="Trading"
            right={<Badge label={t.emergency_stop ? "EMERGENCY STOP" : titleCase(t.state)} tone={stateTone(t)} />}
          >
            <StatGrid>
              <Stat label="Mode" value={t.mode} tone={t.mode === "PAPER" ? "accent" : "warn"} hint="server-side" />
              <Stat label="Auto trading" value={t.auto_trading ? "ON" : "OFF"} tone={t.auto_trading ? "warn" : "neutral"} />
              <Stat label="Equity (paper)" value={inr(t.balance.equity, 0)} hint="simulated account" />
              <Stat
                label="Day P&L (paper)"
                value={inr(t.balance.realized_pnl + t.balance.unrealized_pnl, 0)}
                tone={pnlTone(t.balance.realized_pnl + t.balance.unrealized_pnl)}
              />
            </StatGrid>
            <Row label="Market" value={t.market_open ? "Open" : t.market_detail} />
            <Row label="New entries" value={t.entries_allowed ? "Allowed" : "Blocked"} tone={t.entries_allowed ? "good" : "warn"} />
            {t.risk.blocked_reasons.length ? <Note>Blocked: {t.risk.blocked_reasons.join("; ")}</Note> : null}
            {t.active_trade ? (
              <Pressable onPress={() => nav.navigate("Positions")}>
                <Row
                  label={`${t.active_trade.side} ${t.active_trade.symbol}`}
                  value={`${inr(t.active_trade.unrealized_pnl || t.active_trade.net_pnl)} · ${titleCase(t.active_trade.status)}`}
                  tone={pnlTone(t.active_trade.unrealized_pnl || t.active_trade.net_pnl)}
                />
              </Pressable>
            ) : (
              <Row label="Open position" value="None" />
            )}
          </Card>
        )}
      </QueryView>

      <Card title="Market regime">
        {intel.error && !intel.data ? (
          <ErrorState error={intel.error} onRetry={() => void intel.refetch()} />
        ) : intel.data ? (
          <>
            <Row label="NIFTY 50 regime" value={titleCase(intel.data.snapshot.market_regime)} />
            <Row label="Direction" value={titleCase(intel.data.snapshot.market_direction)} />
            <Row label="Confidence" value={num(intel.data.snapshot.confidence, 0)} />
            <Row label="As of" value={timeIst(intel.data.snapshot.timestamp)} />
          </>
        ) : (
          <Empty title="Waiting for the first analysis" />
        )}
        <Button title="Market overview" tone="neutral" onPress={() => nav.navigate("Market")} />
      </Card>

      <Card title="Top candidates">
        {scan.data ? (
          scan.data.scan.top_candidates.length ? (
            scan.data.scan.top_candidates.slice(0, 5).map((c) => (
              <Pressable key={`${c.symbol}-${c.strategy}-${c.direction}`} onPress={() => c.signal_id && nav.navigate("SignalDetail", { id: c.signal_id })}>
                <Row
                  label={`${c.symbol} · ${titleCase(c.strategy)}`}
                  value={`${c.direction} ${num(c.score, 0)} ${c.status}`}
                  tone={c.status === "READY" ? "good" : "neutral"}
                />
              </Pressable>
            ))
          ) : (
            <Empty title="No candidates in the latest scan" detail="That is a normal outcome: most scans find none." />
          )
        ) : scan.error ? (
          <ErrorState error={scan.error} />
        ) : (
          <Empty title="No scan yet" />
        )}
        {scan.data?.scan.universe ? (
          <Note>
            Universe: {(scan.data.scan.universe as { requested_universes?: string[] }).requested_universes?.join(", ")} ·
            processed {(scan.data.scan.universe as { processed_count?: number }).processed_count} of{" "}
            {(scan.data.scan.universe as { requested_count?: number }).requested_count} · scanned{" "}
            {ageLabel(ageSeconds(scan.data.scan.scanned_at))}
          </Note>
        ) : null}
      </Card>
      <Note>Scores are model scores, not probabilities of profit. Paper results are simulated.</Note>
    </Screen>
  );
}

// --- settings & trading-mode controls ---------------------------------------------------------

export function SettingsScreen() {
  const { user, signOut, backendUrl } = useAuth();
  const nav = useStackNav();
  const qc = useQueryClient();
  const status = usePoll(["trading-status"], api.tradingStatus, 10_000);
  const live = usePoll(["live-status"], api.liveStatus, 30_000);
  const [editUrl, setEditUrl] = useState(false);
  const mode = useMutation({
    mutationFn: (m: "PAPER" | "APPROVAL") => api.setMode(m),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["trading-status"] }),
    onError: (e) => Alert.alert("Not changed", describeError(e)),
  });

  if (editUrl) return <BackendSetupScreen onDone={() => setEditUrl(false)} />;
  const operator = user?.role === "ADMIN" || user?.role === "TRADER";
  return (
    <Screen testID="settings">
      <Card title="Account">
        <Row label="Signed in as" value={user?.email ?? "—"} mono={false} />
        <Row label="Role" value={user?.role ?? "—"} />
        <Button title="Sign out" tone="neutral" onPress={() => void signOut()} />
      </Card>
      <Card title="Trading mode" right={<Badge label="SERVER-ENFORCED" tone="info" />}>
        <QueryView query={status}>
          {(t) => (
            <>
              <Row label="Current" value={t.mode} tone={t.mode === "PAPER" ? "accent" : "warn"} />
              <View style={s.modeRow}>
                {(["PAPER", "APPROVAL"] as const).map((m) => (
                  <View key={m} style={{ flex: 1 }}>
                    <Button
                      title={m === "PAPER" ? "Paper" : "Approval"}
                      tone={t.mode === m ? "accent" : "neutral"}
                      disabled={!operator || t.mode === m}
                      busy={mode.isPending && mode.variables === m}
                      onPress={() => mode.mutate(m)}
                    />
                  </View>
                ))}
              </View>
              <Note>
                Paper: simulated fills, costs and P&amp;L, no real orders. Approval: every trade waits
                for an explicit approval. Both are checked by the backend risk engine; this screen
                cannot bypass it.
              </Note>
            </>
          )}
        </QueryView>
      </Card>
      <Card title="Live trading" right={<Badge label={live.data?.arming.armed ? "ARMED" : "OFF"} tone={live.data?.arming.armed ? "bad" : "neutral"} />}>
        <Row label="Configured on the backend" value={live.data ? (live.data.configured ? "Yes" : "No") : "—"} />
        <Note>
          Live orders use real money. They are off unless the backend sets LIVE_TRADING_ENABLED and an
          administrator arms them in the web console with a typed confirmation and password. The app
          shows the status only and never turns live trading on; it never switches on after an update,
          restart or Zerodha re-login.
        </Note>
      </Card>
      <Card title="Broker connections">
        <Row label="Zerodha Kite Connect" value="Your own API credentials" mono={false} />
        <Button testID="open-broker-connections" title="Broker connections" tone="info" onPress={() => nav.navigate("BrokerConnections")} />
      </Card>
      <Card title="Backend">
        <Row label="Address" value={backendUrl ?? "—"} mono={false} />
        <Row label="Encrypted" value={isEncrypted(backendUrl) ? "Yes (HTTPS)" : "No (http)"} tone={isEncrypted(backendUrl) ? "good" : "warn"} />
        <Button title="Change backend address" tone="neutral" onPress={() => setEditUrl(true)} />
      </Card>
      <Button title="Zerodha connection" tone="neutral" onPress={() => nav.navigate("Zerodha")} />
    </Screen>
  );
}

// --- Zerodha -------------------------------------------------------------------------------

const s = StyleSheet.create({
  splash: { flex: 1, backgroundColor: colors.bg, alignItems: "center", justifyContent: "center", gap: space.sm },
  brand: { color: colors.text, fontSize: 30, fontWeight: "800", letterSpacing: 4, textAlign: "center", marginTop: space.xl },
  brandSub: { color: colors.accent, fontSize: 12, fontWeight: "700", letterSpacing: 2, textAlign: "center" },
  splashNote: { color: colors.muted, fontSize: 13, marginTop: space.lg },
  h1: { color: colors.text, fontSize: 22, fontWeight: "800" },
  input: {
    backgroundColor: colors.surfaceAlt,
    color: colors.text,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: space.md,
    paddingVertical: 12,
    fontSize: 15,
    fontFamily: fonts.mono,
  },
  error: { color: colors.loss, fontSize: 13 },
  link: { color: colors.accent, fontSize: 13, textAlign: "center", paddingVertical: space.sm },
  headerRow: { flexDirection: "row", gap: space.sm, flexWrap: "wrap" },
  modeRow: { flexDirection: "row", gap: space.sm },
});

