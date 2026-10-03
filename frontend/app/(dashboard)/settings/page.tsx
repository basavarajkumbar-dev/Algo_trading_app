"use client";

import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { ZerodhaCard } from "@/components/settings/zerodha-card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { ApiError } from "@/lib/api";
import { getMySettingsRequest, updateMySettingsRequest } from "@/lib/auth-client";
import type { TradingSettingsResponse } from "@/types/auth";

export default function SettingsPage() {
  const { accessToken } = useAuth();
  const [settingsData, setSettingsData] = useState<TradingSettingsResponse | null>(null);
  const [paperCapitalInput, setPaperCapitalInput] = useState("");
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [saveError, setSaveError] = useState<string | null>(null);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    if (!accessToken) return;
    let cancelled = false;

    getMySettingsRequest(accessToken)
      .then((data) => {
        if (cancelled) return;
        setSettingsData(data);
        setPaperCapitalInput(String(data.paper_capital));
        setStatus("ready");
      })
      .catch(() => {
        if (!cancelled) setStatus("error");
      });

    return () => {
      cancelled = true;
    };
  }, [accessToken]);

  async function handleSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!accessToken) return;
    setSaveError(null);
    setIsSaving(true);

    try {
      const updated = await updateMySettingsRequest(accessToken, {
        paper_capital: Number(paperCapitalInput),
      });
      setSettingsData(updated);
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : "Could not save settings.");
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Settings</h1>
        <p className="mt-1 text-sm text-muted">
          Foundation only — most controls described in the project brief (strategy
          enable/disable, risk limits, trading hours, broker settings, emergency controls) will be
          added as the phases that give them real meaning are built. See{" "}
          <code className="tabular text-xs">docs/development-roadmap.md</code>.
        </p>
      </div>

      <Card className="max-w-xl">
        <CardHeader>
          <CardTitle>Trading Settings</CardTitle>
        </CardHeader>
        <CardContent>
          {status === "loading" && <p className="text-sm text-muted">Loading…</p>}
          {status === "error" && (
            <p className="text-sm text-loss">Could not load your settings. Try reloading.</p>
          )}

          {status === "ready" && settingsData && (
            <form className="flex flex-col gap-5" onSubmit={handleSave}>
              <SettingRow label="Trading mode">
                <Badge variant="accent">{settingsData.trading_mode}</Badge>
                <span className="ml-2 text-xs text-muted">
                  Not editable yet — see PROJECT_CONTRACT.md section 9.
                </span>
              </SettingRow>

              <SettingRow label="Auto trading">
                <Badge variant="neutral">
                  {settingsData.auto_trading_enabled ? "Enabled" : "Disabled"}
                </Badge>
                <span className="ml-2 text-xs text-muted">
                  Not editable yet — no trading loop exists.
                </span>
              </SettingRow>

              <SettingRow label="Emergency stop">
                <Badge variant={settingsData.emergency_stop ? "disconnected" : "connected"}>
                  {settingsData.emergency_stop ? "Active" : "Not active"}
                </Badge>
              </SettingRow>

              <label className="flex flex-col gap-1.5 text-xs text-muted">
                Paper capital (₹)
                <input
                  type="number"
                  min={1}
                  step="0.01"
                  value={paperCapitalInput}
                  onChange={(e) => setPaperCapitalInput(e.target.value)}
                  className="tabular w-full max-w-xs rounded border border-border bg-surface-alt px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
                />
              </label>

              <SettingRow label="Signal score threshold">
                <span className="tabular text-sm">{settingsData.signal_score_threshold}</span>
                <span className="ml-2 text-xs text-muted">(read-only in this phase)</span>
              </SettingRow>

              <SettingRow label="Trading window">
                <span className="tabular text-sm">
                  {settingsData.trading_start_time} – {settingsData.trading_end_time}
                </span>
                <span className="ml-2 text-xs text-muted">(read-only in this phase)</span>
              </SettingRow>

              {saveError && <p className="text-xs text-loss">{saveError}</p>}

              <div>
                <Button type="submit" disabled={isSaving}>
                  {isSaving ? "Saving…" : "Save changes"}
                </Button>
              </div>
            </form>
          )}
        </CardContent>
      </Card>

      <ZerodhaCard />
    </div>
  );
}

function SettingRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 border-b border-border pb-3 text-sm last:border-0 last:pb-0">
      <span className="text-xs uppercase tracking-wide text-muted">{label}</span>
      <div className="flex items-center">{children}</div>
    </div>
  );
}
