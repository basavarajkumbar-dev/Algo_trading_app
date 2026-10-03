"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import { formatIstTime } from "@/lib/intelligence-format";
import { getZerodhaLoginUrlRequest, zerodhaLogoutRequest } from "@/lib/market-data-client";
import { authLabel, executionLabel, marketDataLabel } from "@/lib/market-data-format";
import { useMarketDataStatus, useZerodhaStatus } from "@/lib/queries";
import { qk } from "@/lib/query-keys";

const CALLBACK_MESSAGES: Record<string, string> = {
  state: "The login link expired or was not started here. Start the login again.",
  login: "Zerodha did not complete the login.",
  token_exchange: "Zerodha refused the login token. Start the login again.",
  not_configured: "Zerodha is not configured on the backend.",
};

/**
 * Daily Kite login. The browser goes to Zerodha and back to the backend
 * callback; the access token stays on the backend (never in this page,
 * local storage or the URL). Logging in changes market data, never execution.
 */
export function ZerodhaCard() {
  const { accessToken, user } = useAuth();
  const qc = useQueryClient();
  const z = useZerodhaStatus().data;
  const md = useMarketDataStatus().data;
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<{ text: string; error: boolean } | null>(null);
  const isAdmin = user?.role === "ADMIN";

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const result = params.get("zerodha");
    if (!result) return;
    setMessage(
      result === "connected"
        ? { text: "Logged in to Zerodha. Market data will start streaming.", error: false }
        : {
            text: CALLBACK_MESSAGES[params.get("reason") ?? ""] ?? "Zerodha login failed.",
            error: true,
          }
    );
    window.history.replaceState(null, "", window.location.pathname);
    void qc.invalidateQueries({ queryKey: ["system"] });
  }, [qc]);

  async function login() {
    if (!accessToken) return;
    setBusy(true);
    setMessage(null);
    try {
      const { login_url } = await getZerodhaLoginUrlRequest(accessToken);
      window.location.assign(login_url);
    } catch (err) {
      setMessage({
        text: err instanceof ApiError ? err.message : "Could not start the login.",
        error: true,
      });
      setBusy(false);
    }
  }

  async function logout() {
    if (!accessToken) return;
    setBusy(true);
    try {
      await zerodhaLogoutRequest(accessToken);
      setMessage({ text: "Logged out of Zerodha.", error: false });
      await qc.invalidateQueries({ queryKey: qk.zerodhaStatus });
      await qc.invalidateQueries({ queryKey: qk.marketData });
    } catch (err) {
      setMessage({ text: err instanceof ApiError ? err.message : "Logout failed.", error: true });
    } finally {
      setBusy(false);
    }
  }

  const auth = authLabel(z);
  const data = marketDataLabel(md);
  const exec = executionLabel(z);

  return (
    <Card className="max-w-xl" data-testid="zerodha-card">
      <CardHeader>
        <CardTitle>Zerodha (Kite Connect)</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4 text-sm">
        {!z ? (
          <p className="text-muted">Loading…</p>
        ) : !z.configured ? (
          <p className="text-muted" data-testid="zerodha-not-configured">
            Not configured. Set <code className="text-xs">KITE_API_KEY</code>,{" "}
            <code className="text-xs">KITE_API_SECRET</code> and{" "}
            <code className="text-xs">KITE_REDIRECT_URL</code> in the backend environment (never in
            the frontend). Until then the app runs on simulated data.
          </p>
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant={auth.variant} data-testid="zerodha-auth">
                {auth.text}
              </Badge>
              <Badge variant={data.variant}>Market data: {data.text}</Badge>
              <Badge variant={exec.variant}>Execution: {exec.text}</Badge>
            </div>
            {z.connected && (
              <p className="text-xs text-muted">
                User {z.user_id} {z.user_name ? `(${z.user_name})` : ""}
                {z.expires_at ? ` · session expires ${formatIstTime(z.expires_at)} IST` : ""}
              </p>
            )}
            {!z.redirect_url_configured && (
              <p className="text-xs text-warning">
                KITE_REDIRECT_URL is not set: it must match the redirect URL registered for the app
                on developers.kite.trade (…/api/v1/broker/zerodha/callback).
              </p>
            )}
            <p className="text-xs text-muted">
              Kite sessions end at 06:00 IST: log in once each trading day. Logging in streams real
              market data; orders still go to the paper broker.
            </p>
            {isAdmin ? (
              <div className="flex gap-2">
                <Button onClick={login} disabled={busy} data-testid="zerodha-login">
                  {z.connected ? "Log in again" : "Log in to Zerodha"}
                </Button>
                {z.connected && (
                  <Button
                    variant="outline"
                    onClick={logout}
                    disabled={busy}
                    data-testid="zerodha-logout"
                  >
                    Log out
                  </Button>
                )}
              </div>
            ) : (
              <p className="text-xs text-muted">An admin logs in to Zerodha.</p>
            )}
          </>
        )}
        {message && (
          <p
            className={message.error ? "text-xs text-loss" : "text-xs text-profit"}
            data-testid="zerodha-message"
          >
            {message.text}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
