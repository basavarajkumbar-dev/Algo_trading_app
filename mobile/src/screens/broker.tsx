/** Settings → Broker connections → Zerodha Kite Connect.
 *
 * The user types their own API key and API secret. They are held in this
 * screen's memory only until the backend confirms it saved them (then the
 * fields are cleared), sent once over HTTPS, and never read back: the backend
 * answers with a masked key hint and "Configured". Every status shown is the
 * backend's. Paper trading works without any of this. */

import { useQueryClient } from "@tanstack/react-query";
import * as WebBrowser from "expo-web-browser";
import React, { useEffect, useState } from "react";
import { Alert, Pressable, StyleSheet, Text, TextInput, View } from "react-native";

import { currentBaseUrl, describeError } from "../api/client";
import { api, type BrokerCredentialStatus } from "../api/endpoints";
import { useAuth } from "../auth/AuthProvider";
import { Badge, Button, Card, Note, QueryView, Row, Screen } from "../components/ui";
import { APP_SCHEME } from "../config";
import { connectLabel, mayTransmitSecrets, statusTone, validateCredentials } from "../lib/credentials";
import { timeIst, titleCase } from "../lib/format";
import { usePoll, useStackNav } from "../lib/hooks";
import { colors, fonts, radius, space } from "../theme";

const STATUS_KEY = ["zerodha-credentials"];

export function BrokerConnectionsScreen() {
  const nav = useStackNav();
  const status = usePoll(STATUS_KEY, api.zerodhaCredentials, 30_000);
  return (
    <Screen testID="broker-connections" refreshing={status.isFetching} onRefresh={() => void status.refetch()}>
      <Pressable testID="broker-zerodha" accessibilityRole="button" onPress={() => nav.navigate("Zerodha")}>
        <Card
          title="Zerodha Kite Connect"
          right={
            <Badge
              label={status.data ? status.data.status_label.toUpperCase() : status.error ? "UNKNOWN" : "…"}
              tone={statusTone(status.data?.status)}
            />
          }
        >
          <Row label="API key" value={status.data?.api_key_hint ?? "Not set"} />
          <Note>Your own Kite Connect app credentials. Tap to set up, connect or change them.</Note>
        </Card>
      </Pressable>
      {status.error ? <Note>Status unavailable: {describeError(status.error)}</Note> : null}
      <Note>Paper trading never needs a broker connection.</Note>
    </Screen>
  );
}

export function ZerodhaScreen() {
  const { user } = useAuth();
  const qc = useQueryClient();
  const status = usePoll(STATUS_KEY, api.zerodhaCredentials, 15_000);
  const [apiKey, setApiKey] = useState("");
  const [apiSecret, setApiSecret] = useState("");
  const [showSecret, setShowSecret] = useState(false);
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<{ text: string; tone: "good" | "bad" | "info" } | null>(null);

  // the typed values never outlive the screen
  useEffect(
    () => () => {
      setApiKey("");
      setApiSecret("");
    },
    [],
  );

  const canManage = user?.role === "ADMIN" || user?.role === "TRADER";
  const z = status.data;
  const showForm = !!z && z.credentials_source !== "ENVIRONMENT" && (!z.configured || editing);
  const storageOk = z?.storage === "ENCRYPTED";

  function clearForm() {
    setApiKey("");
    setApiSecret("");
    setShowSecret(false);
  }

  async function run(name: string, action: () => Promise<BrokerCredentialStatus | void>, done?: string) {
    setMessage(null);
    setBusy(name);
    try {
      const next = await action();
      if (next) qc.setQueryData(STATUS_KEY, next);
      if (done) setMessage({ text: done, tone: "good" });
    } catch (e) {
      setMessage({ text: describeError(e), tone: "bad" });
    } finally {
      setBusy(null);
      void status.refetch();
    }
  }

  async function save() {
    const problem = validateCredentials(apiKey, apiSecret);
    if (problem) {
      setMessage({ text: problem, tone: "bad" });
      return;
    }
    if (!mayTransmitSecrets(currentBaseUrl(), __DEV__)) {
      setMessage({
        text: "Credentials can only be sent to an HTTPS backend. Change the backend address in Settings.",
        tone: "bad",
      });
      return;
    }
    setMessage(null);
    setBusy("save");
    try {
      const next = await api.saveZerodhaCredentials(apiKey.trim(), apiSecret.trim());
      qc.setQueryData(STATUS_KEY, next);
      clearForm();
      setEditing(false);
      setMessage({ text: "Credentials saved on the server (encrypted). Now connect to Zerodha.", tone: "good" });
    } catch (e) {
      // nothing is claimed as saved; the secret is kept only for a retry
      setMessage({ text: `Not saved: ${describeError(e)}`, tone: "bad" });
    } finally {
      setBusy(null);
      void status.refetch();
    }
  }

  async function connect() {
    setMessage(null);
    setBusy("connect");
    try {
      const { login_url } = await api.connectZerodha();
      const result = await WebBrowser.openAuthSessionAsync(login_url, `${APP_SCHEME}://zerodha`);
      if (result.type === "success") {
        const query = new URL(result.url).searchParams;
        setMessage(
          query.get("result") === "connected"
            ? { text: "Zerodha login finished. Status below is confirmed by the server.", tone: "info" }
            : { text: `Zerodha login did not complete (${query.get("reason") ?? "unknown"}).`, tone: "bad" },
        );
      } else {
        setMessage({ text: "Login window closed. The status below is the server's.", tone: "info" });
      }
    } catch (e) {
      setMessage({ text: describeError(e), tone: "bad" });
    } finally {
      setBusy(null);
      void status.refetch();
    }
  }

  function confirmRemove() {
    Alert.alert(
      "Remove Zerodha credentials?",
      "The server deletes your saved API key, API secret and session. You can enter them again later.",
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Remove",
          style: "destructive",
          onPress: () => void run("remove", api.removeZerodhaCredentials, "Credentials removed from the server."),
        },
      ],
    );
  }

  async function test() {
    setMessage(null);
    setBusy("test");
    try {
      const result = await api.testZerodhaCredentials();
      qc.setQueryData(STATUS_KEY, result.status);
      setMessage({ text: result.message, tone: result.ok ? (result.verified ? "good" : "info") : "bad" });
    } catch (e) {
      setMessage({ text: describeError(e), tone: "bad" });
    } finally {
      setBusy(null);
      void status.refetch();
    }
  }

  return (
    <Screen testID="zerodha" refreshing={status.isFetching} onRefresh={() => void status.refetch()}>
      <QueryView query={status}>
        {(s) => (
          <Card
            title="Zerodha Kite Connect"
            right={<Badge label={s.status_label.toUpperCase()} tone={statusTone(s.status)} />}
          >
            <Row label="Status" value={s.status_label} tone={statusTone(s.status)} mono={false} />
            <Row label="API key" value={s.api_key_hint ?? "Not set"} />
            <Row label="API secret" value={s.api_secret ?? "Not set"} mono={false} />
            {s.connected ? <Row label="Zerodha user" value={s.user_id ?? "—"} /> : null}
            {s.connected ? <Row label="Session expires" value={timeIst(s.session_expires_at)} /> : null}
            <Row label="Market data" value={titleCase(s.market_data)} />
            {s.detail ? <Note testID="zerodha-detail">{s.detail}</Note> : null}
            {s.market_data_note ? <Note>{s.market_data_note}</Note> : null}
            {s.status === "SESSION_EXPIRED" ? (
              <Note>Zerodha sessions end every day (about 6 AM IST). Your credentials are kept: just reconnect.</Note>
            ) : null}
          </Card>
        )}
      </QueryView>

      {message ? (
        <Text testID="zerodha-message" style={[st.message, { color: message.tone === "bad" ? colors.loss : message.tone === "good" ? colors.profit : colors.text }]}>
          {message.text}
        </Text>
      ) : null}

      {z && !storageOk ? (
        <Note testID="storage-unavailable">
          {z.storage_problem ?? "The server cannot store credentials securely, so saving is disabled."}
        </Note>
      ) : null}

      {showForm ? (
        <Card title={z?.configured ? "Update credentials" : "Enter your credentials"}>
          <Text style={st.label}>API Key</Text>
          <TextInput
            testID="api-key"
            value={apiKey}
            onChangeText={setApiKey}
            placeholder="Your Kite Connect API key"
            placeholderTextColor={colors.muted}
            autoCapitalize="none"
            autoCorrect={false}
            autoComplete="off"
            importantForAutofill="no"
            editable={canManage && storageOk}
            style={st.input}
          />
          <Text style={st.label}>API Secret</Text>
          <View style={st.secretRow}>
            <TextInput
              testID="api-secret"
              value={apiSecret}
              onChangeText={setApiSecret}
              placeholder="Your Kite Connect API secret"
              placeholderTextColor={colors.muted}
              secureTextEntry={!showSecret}
              autoCapitalize="none"
              autoCorrect={false}
              autoComplete="off"
              importantForAutofill="no"
              textContentType="none"
              editable={canManage && storageOk}
              style={[st.input, st.secretInput]}
            />
            <Pressable
              testID="toggle-secret"
              accessibilityRole="button"
              accessibilityLabel={showSecret ? "Hide API secret" : "Show API secret"}
              onPress={() => setShowSecret((v) => !v)}
              style={st.toggle}
            >
              <Text style={st.toggleText}>{showSecret ? "Hide" : "Show"}</Text>
            </Pressable>
          </View>
          <Button
            testID="save-credentials"
            title="Save Credentials"
            onPress={() => void save()}
            busy={busy === "save"}
            disabled={!canManage || !storageOk || busy !== null}
          />
          {z?.configured ? (
            <Button
              title="Cancel"
              tone="neutral"
              onPress={() => {
                clearForm();
                setEditing(false);
              }}
            />
          ) : null}
          <Note>
            Sent once over HTTPS to your backend, which encrypts them. They are never shown again, never stored on
            this phone and never sent to Zerodha from here.
          </Note>
        </Card>
      ) : null}

      {z?.configured ? (
        <View style={st.actions}>
          <Button
            testID="zerodha-connect"
            title={connectLabel(z.status)}
            onPress={() => void connect()}
            busy={busy === "connect"}
            disabled={!canManage || busy !== null || z.status === "CONNECTED"}
          />
          <Button
            testID="zerodha-test"
            title="Test Connection"
            tone="info"
            onPress={() => void test()}
            busy={busy === "test"}
            disabled={!canManage || busy !== null}
          />
          <Button
            testID="zerodha-disconnect"
            title="Disconnect"
            tone="warn"
            onPress={() => void run("disconnect", api.disconnectZerodha, "Disconnected from Zerodha. Credentials kept.")}
            busy={busy === "disconnect"}
            disabled={!canManage || busy !== null || !(z.connected || z.status === "AWAITING_LOGIN")}
          />
          {z.credentials_source !== "ENVIRONMENT" ? (
            <>
              <Button
                testID="update-credentials"
                title="Update Credentials"
                tone="neutral"
                onPress={() => {
                  clearForm();
                  setEditing(true);
                }}
                disabled={!canManage || busy !== null || editing}
              />
              <Button
                testID="remove-credentials"
                title="Remove Credentials"
                tone="bad"
                onPress={confirmRemove}
                busy={busy === "remove"}
                disabled={!canManage || busy !== null}
              />
            </>
          ) : null}
        </View>
      ) : null}

      {!canManage ? <Note>Your role is read-only: a trader or administrator manages broker credentials.</Note> : null}
      {z && !z.redirect_url_configured ? (
        <Note>The server has no Kite redirect URL configured, so the Zerodha login cannot complete yet.</Note>
      ) : null}
      <Note>
        You sign in on Zerodha&apos;s own page; the backend exchanges the login for a session. Live orders stay
        disabled unless the server enables live trading, an administrator arms it and every risk check passes.
        Paper trading works without Zerodha.
      </Note>
    </Screen>
  );
}

const st = StyleSheet.create({
  label: { color: colors.muted, fontSize: 12, fontWeight: "700", letterSpacing: 0.5 },
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
  secretRow: { flexDirection: "row", alignItems: "center", gap: space.sm },
  secretInput: { flex: 1 },
  toggle: { paddingHorizontal: space.md, paddingVertical: 12, borderRadius: radius.md, borderWidth: 1, borderColor: colors.border },
  toggleText: { color: colors.accent, fontWeight: "700" },
  message: { fontSize: 14, paddingHorizontal: space.xs },
  actions: { gap: space.sm },
});
