"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import type { ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Figure, Panel, errorText } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { formatIstTime } from "@/lib/intelligence-format";
import {
  acknowledgeRecoveryRequest,
  armLiveRequest,
  disarmLiveRequest,
  getLiveAuditRequest,
  killSwitchRequest,
  liveAutoRequest,
  liveExecuteRequest,
  liveExitRequest,
  livePreflightRequest,
  resetKillSwitchRequest,
} from "@/lib/live-client";
import { canExecute, checkLabel, liveState } from "@/lib/live-format";
import { useLiveStatus, useScanner } from "@/lib/queries";
import { qk } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import type { LiveTrade, PreTradeReport } from "@/types/live";

const inputCls =
  "w-full rounded border border-border bg-surface-alt px-3 py-2 text-sm text-foreground outline-none focus:border-accent";

function msg(err: unknown): string {
  return err instanceof ApiError ? err.message : "Request failed.";
}

export default function LivePage() {
  const { accessToken, user } = useAuth();
  const qc = useQueryClient();
  const q = useLiveStatus();
  const s = q.data;
  const isAdmin = user?.role === "ADMIN";
  const state = liveState(s);
  const refresh = () => void qc.invalidateQueries({ queryKey: ["live"] });
  const token = accessToken ?? "";

  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Live Trading</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Real orders at Zerodha. Off by default: it needs{" "}
          <code className="text-xs">EXECUTION_MODE=ZERODHA</code> and{" "}
          <code className="text-xs">LIVE_TRADING_ENABLED=true</code> on the
          backend, an admin arming it for the session, and every pre-trade check
          passing. If any check fails, no order is placed. One active trade at a
          time. Nothing here predicts or guarantees profit.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,5fr)_minmax(0,4fr)]">
        <Panel
          title="Live status"
          loading={q.isLoading}
          error={errorText(q.error)}
          testId="live-status"
        >
          {s && (
            <div className="flex flex-col gap-4">
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
                <Figure label="State" testId="live-state" hint={state.detail}>
                  <Badge variant={state.variant}>{state.text}</Badge>
                </Figure>
                <Figure label="Execution">
                  <Badge variant={s.configured ? "caution" : "accent"}>
                    {s.configured ? "ZERODHA (configured)" : "PAPER"}
                  </Badge>
                </Figure>
                <Figure label="Armed until">
                  {s.arming.armed && s.arming.expires_at
                    ? `${formatIstTime(s.arming.expires_at)} IST`
                    : "—"}
                </Figure>
                <Figure label="Live auto trading">
                  <Badge variant={s.auto_trading ? "caution" : "neutral"}>
                    {s.auto_trading ? "ON" : "off"}
                  </Badge>
                </Figure>
                <Figure
                  label="Broker health"
                  hint={
                    s.health.age_seconds != null
                      ? `${s.health.age_seconds}s ago`
                      : undefined
                  }
                >
                  <Badge
                    variant={
                      s.health.status === "HEALTHY"
                        ? "connected"
                        : "disconnected"
                    }
                  >
                    {s.health.status}
                  </Badge>
                </Figure>
                <Figure label="Max active trades">
                  {s.max_active_positions}
                </Figure>
                <Figure label="Max per trade">
                  ₹{Number(s.limits.max_position_value).toLocaleString("en-IN")}
                </Figure>
                <Figure label="Kill switch closes positions">
                  {s.limits.close_positions_on_kill ? "yes" : "no"}
                </Figure>
              </div>
              {s.unresolved && (
                <Recovery
                  text={s.unresolved}
                  isAdmin={isAdmin}
                  token={token}
                  onDone={refresh}
                />
              )}
              {s.arm_blockers.length > 0 && !s.arming.armed && (
                <div
                  className="text-xs text-warning"
                  data-testid="live-arm-blockers"
                >
                  Cannot arm: {s.arm_blockers.join("; ")}
                </div>
              )}
              {s.last_error && (
                <p className="text-xs text-loss">Last error: {s.last_error}</p>
              )}
            </div>
          )}
        </Panel>

        <KillSwitch
          statusEngaged={s?.kill_switch.engaged ?? false}
          reason={s?.kill_switch.reason ?? null}
          isAdmin={isAdmin}
          token={token}
          onDone={refresh}
        />
      </div>

      {s && (
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <ArmPanel
            phrase={s.confirmation_phrase}
            armed={s.arming.armed}
            configured={s.configured}
            auto={s.auto_trading}
            isAdmin={isAdmin}
            token={token}
            onDone={refresh}
          />
          <ActiveTrade
            trade={s.active_trade}
            recent={s.recent_trades}
            token={token}
            onDone={refresh}
          />
        </div>
      )}

      <Preflight
        armedStatus={s}
        isAdmin={isAdmin}
        token={token}
        onDone={refresh}
      />

      {s && (
        <Panel title="Broker health checks" testId="live-health">
          <ul className="grid grid-cols-1 gap-1 text-sm sm:grid-cols-2">
            {s.health.checks.map((c) => (
              <li key={c.name} className="flex items-center gap-2">
                <span
                  className={cn(
                    "h-2 w-2 rounded-full",
                    c.ok ? "bg-profit" : "bg-loss",
                  )}
                  aria-hidden
                />
                <span className="w-36 text-muted">{c.name}</span>
                <span>{c.status}</span>
              </li>
            ))}
          </ul>
        </Panel>
      )}

      <Audit token={token} />
    </div>
  );
}

function Recovery({
  text,
  isAdmin,
  token,
  onDone,
}: {
  text: string;
  isAdmin: boolean;
  token: string;
  onDone: () => void;
}) {
  const [note, setNote] = useState("");
  const m = useMutation({
    mutationFn: () => acknowledgeRecoveryRequest(note, token),
    onSuccess: onDone,
  });
  return (
    <div
      className="rounded border border-loss/40 bg-loss/10 p-3 text-xs"
      data-testid="live-recovery"
    >
      <p className="text-loss">{text}</p>
      {isAdmin && (
        <div className="mt-2 flex gap-2">
          <input
            className={inputCls}
            placeholder="What you verified / did in Kite"
            value={note}
            onChange={(e) => setNote(e.target.value)}
          />
          <Button
            size="sm"
            disabled={note.length < 3 || m.isPending}
            onClick={() => m.mutate()}
          >
            Acknowledge
          </Button>
        </div>
      )}
      {m.error && <p className="mt-1 text-loss">{msg(m.error)}</p>}
    </div>
  );
}

function KillSwitch({
  statusEngaged,
  reason,
  isAdmin,
  token,
  onDone,
}: {
  statusEngaged: boolean;
  reason: string | null;
  isAdmin: boolean;
  token: string;
  onDone: () => void;
}) {
  const [close, setClose] = useState(false);
  const engage = useMutation({
    mutationFn: () =>
      killSwitchRequest(
        "kill switch from the Live page",
        close ? true : null,
        token,
      ),
    onSuccess: onDone,
  });
  const reset = useMutation({
    mutationFn: () => resetKillSwitchRequest(token),
    onSuccess: onDone,
  });
  return (
    <Panel title="Kill switch" testId="live-kill-switch">
      <div className="flex flex-col gap-3 text-sm">
        <p className="text-xs text-muted">
          Stops new live orders, disarms, cancels a pending entry and stops
          paper trading. An open position keeps its protective stop and target
          and is not closed unless you tick the box (or it is configured).
        </p>
        {statusEngaged ? (
          <>
            <Badge variant="disconnected" className="w-fit">
              ENGAGED{reason ? `: ${reason}` : ""}
            </Badge>
            {isAdmin && (
              <Button
                variant="outline"
                onClick={() => reset.mutate()}
                disabled={reset.isPending}
                data-testid="live-kill-reset"
              >
                Reset kill switch (does not re-arm)
              </Button>
            )}
          </>
        ) : (
          <>
            <label className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                checked={close}
                onChange={(e) => setClose(e.target.checked)}
              />
              Also close an open live position at market
            </label>
            <button
              type="button"
              onClick={() => engage.mutate()}
              disabled={engage.isPending}
              className="h-11 rounded bg-loss px-4 text-sm font-semibold text-white hover:bg-loss/90"
              data-testid="live-kill-engage"
            >
              ENGAGE KILL SWITCH
            </button>
          </>
        )}
        {(engage.error || reset.error) && (
          <p className="text-xs text-loss">
            {msg(engage.error ?? reset.error)}
          </p>
        )}
      </div>
    </Panel>
  );
}

function ArmPanel({
  phrase,
  armed,
  configured,
  auto,
  isAdmin,
  token,
  onDone,
}: {
  phrase: string;
  armed: boolean;
  configured: boolean;
  auto: boolean;
  isAdmin: boolean;
  token: string;
  onDone: () => void;
}) {
  const [confirmation, setConfirmation] = useState("");
  const [password, setPassword] = useState("");
  const arm = useMutation({
    mutationFn: () => armLiveRequest(confirmation, password, token),
    onSuccess: () => {
      setConfirmation("");
    },
    onSettled: () => {
      setPassword("");
      onDone(); // refusals are audited too
    },
  });
  const disarm = useMutation({
    mutationFn: () => disarmLiveRequest(token),
    onSuccess: onDone,
  });
  const autoM = useMutation({
    mutationFn: (on: boolean) => liveAutoRequest(on, token),
    onSuccess: onDone,
  });
  return (
    <Panel title="Arm live trading" testId="live-arm">
      <div className="flex flex-col gap-3 text-sm">
        {armed ? (
          <>
            <p className="text-xs text-warning">
              Live trading is ARMED for this session. Orders go to your Zerodha
              account.
            </p>
            <div className="flex flex-wrap gap-2">
              <Button
                variant="outline"
                onClick={() => disarm.mutate()}
                disabled={disarm.isPending}
                data-testid="live-disarm"
              >
                Disarm
              </Button>
              {isAdmin && (
                <Button
                  variant="outline"
                  onClick={() => autoM.mutate(!auto)}
                  disabled={autoM.isPending}
                  data-testid="live-auto"
                >
                  {auto
                    ? "Turn live auto trading off"
                    : "Turn live auto trading on"}
                </Button>
              )}
            </div>
          </>
        ) : !configured ? (
          <p className="text-xs text-muted">
            Not available: the backend runs in PAPER mode. Live trading is
            enabled only by configuration on the server, never from here.
          </p>
        ) : !isAdmin ? (
          <p className="text-xs text-muted">An admin arms live trading.</p>
        ) : (
          <form
            className="flex flex-col gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              arm.mutate();
            }}
          >
            <label className="text-xs text-muted">
              Type <span className="font-mono text-foreground">{phrase}</span>
              <input
                className={inputCls}
                value={confirmation}
                onChange={(e) => setConfirmation(e.target.value)}
                data-testid="live-confirmation"
              />
            </label>
            <label className="text-xs text-muted">
              Your password
              <input
                type="password"
                className={inputCls}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                data-testid="live-password"
              />
            </label>
            <Button
              type="submit"
              disabled={confirmation !== phrase || !password || arm.isPending}
              data-testid="live-arm-submit"
            >
              Arm live trading for this session
            </Button>
          </form>
        )}
        {(arm.error || disarm.error || autoM.error) && (
          <p className="text-xs text-loss" data-testid="live-arm-error">
            {msg(arm.error ?? disarm.error ?? autoM.error)}
          </p>
        )}
      </div>
    </Panel>
  );
}

function ActiveTrade({
  trade,
  recent,
  token,
  onDone,
}: {
  trade: LiveTrade | null;
  recent: LiveTrade[];
  token: string;
  onDone: () => void;
}) {
  const exit = useMutation({
    mutationFn: () => liveExitRequest(token),
    onSuccess: onDone,
  });
  return (
    <Panel title="Live trade" testId="live-trade">
      {trade ? (
        <div className="flex flex-col gap-3 text-sm">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Figure label="Trade">
              {trade.side} {trade.quantity} {trade.symbol}
            </Figure>
            <Figure label="Status">
              <Badge variant="caution">{trade.status}</Badge>
            </Figure>
            <Figure label="Entry">
              {trade.average_entry ?? trade.planned_entry}
            </Figure>
            <Figure label="Stop / target">
              {trade.stop_loss} / {trade.target}
            </Figure>
            <Figure label="Filled">{trade.filled_quantity}</Figure>
            <Figure label="Exited">{trade.exited_quantity}</Figure>
          </div>
          <Button
            variant="outline"
            onClick={() => exit.mutate()}
            disabled={exit.isPending}
            data-testid="live-exit"
          >
            Exit at market
          </Button>
          {exit.error && <p className="text-xs text-loss">{msg(exit.error)}</p>}
        </div>
      ) : (
        <p className="text-sm text-muted">No active live trade.</p>
      )}
      {recent.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1 border-t border-border pt-3 text-xs">
          {recent.map((t) => (
            <li key={t.id} className="flex justify-between gap-2">
              <span>
                {t.side} {t.quantity} {t.symbol} · {t.status}{" "}
                {t.exit_reason ? `(${t.exit_reason})` : ""}
              </span>
              <span
                className={cn(
                  "tabular",
                  (t.gross_pnl ?? 0) >= 0 ? "text-profit" : "text-loss",
                )}
              >
                {t.gross_pnl != null ? `₹${t.gross_pnl.toFixed(2)}` : "—"}
              </span>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

function Preflight({
  armedStatus,
  isAdmin,
  token,
  onDone,
}: {
  armedStatus: ReturnType<typeof useLiveStatus>["data"];
  isAdmin: boolean;
  token: string;
  onDone: () => void;
}) {
  const scanner = useScanner().data;
  const ready = (scanner?.scan.candidates ?? []).filter(
    (c) => c.signal_id && c.status === "READY",
  );
  const [signalId, setSignalId] = useState("");
  const [report, setReport] = useState<PreTradeReport | null>(null);
  const check = useMutation({
    mutationFn: () => livePreflightRequest(signalId, token),
    onSuccess: (r) => setReport(r.report),
  });
  const run = useMutation({
    mutationFn: () => liveExecuteRequest(signalId, token),
    onSuccess: (r) => {
      setReport(r.report);
      onDone();
    },
  });
  const executable = canExecute(armedStatus, report?.checks);
  return (
    <Panel title="Pre-flight check" testId="live-preflight">
      <div className="flex flex-col gap-3 text-sm">
        <div className="flex flex-wrap items-end gap-2">
          <label className="min-w-64 flex-1 text-xs text-muted">
            READY signal
            <select
              className={inputCls}
              value={signalId}
              onChange={(e) => {
                setSignalId(e.target.value);
                setReport(null);
              }}
              data-testid="live-signal"
            >
              <option value="">Choose…</option>
              {ready.map((c) => (
                <option key={c.signal_id} value={c.signal_id ?? ""}>
                  {c.direction} {c.symbol} · {c.strategy} · score{" "}
                  {c.score.toFixed(0)}
                </option>
              ))}
            </select>
          </label>
          <Button
            variant="outline"
            disabled={!signalId || check.isPending}
            onClick={() => check.mutate()}
            data-testid="live-run-preflight"
          >
            Run pre-flight (no order)
          </Button>
          {isAdmin && (
            <Button
              disabled={!signalId || !executable || run.isPending}
              onClick={() => run.mutate()}
              data-testid="live-execute"
            >
              Place LIVE order
            </Button>
          )}
        </div>
        {(check.error || run.error) && (
          <p className="text-xs text-loss">{msg(check.error ?? run.error)}</p>
        )}
        {report && <Checklist report={report} />}
        {!report && (
          <p className="text-xs text-muted">
            Run the pre-flight to see every check. A LIVE order is offered only
            when all pass and live is armed.
          </p>
        )}
      </div>
    </Panel>
  );
}

function Checklist({ report }: { report: PreTradeReport }) {
  return (
    <div data-testid="live-checklist">
      <p
        className={cn(
          "mb-2 text-xs font-medium",
          report.ok ? "text-profit" : "text-loss",
        )}
      >
        {report.ok
          ? `All ${report.checks.length} checks passed · quantity ${report.quantity}`
          : "Blocked: no order will be placed"}
      </p>
      <ul className="grid grid-cols-1 gap-1 text-xs md:grid-cols-2">
        {report.checks.map((c) => (
          <Row key={c.name} ok={c.passed} label={checkLabel(c)}>
            {c.detail}
          </Row>
        ))}
      </ul>
    </div>
  );
}

function Row({
  ok,
  label,
  children,
}: {
  ok: boolean;
  label: string;
  children: ReactNode;
}) {
  return (
    <li className="flex gap-2">
      <span
        className={cn(
          "mt-1 h-2 w-2 shrink-0 rounded-full",
          ok ? "bg-profit" : "bg-loss",
        )}
        aria-hidden
      />
      <span className="w-44 shrink-0">{label}</span>
      <span className="text-muted">{children}</span>
    </li>
  );
}

function Audit({ token }: { token: string }) {
  const q = useQuery({
    queryKey: qk.liveAudit,
    queryFn: () => getLiveAuditRequest(token),
    enabled: Boolean(token),
    refetchInterval: 10_000,
  });
  return (
    <Panel
      title="Live audit trail"
      loading={q.isLoading}
      error={errorText(q.error)}
      testId="live-audit"
    >
      <ul className="flex flex-col gap-1 text-xs">
        {(q.data?.items ?? []).map((a, i) => (
          <li key={`${a.at}-${i}`} className="flex gap-3">
            <span className="tabular w-20 shrink-0 text-muted">
              {formatIstTime(a.at)}
            </span>
            <span className="w-48 shrink-0 font-medium">{a.action}</span>
            <span className="truncate text-muted">
              {JSON.stringify(a.details)}
            </span>
          </li>
        ))}
        {q.data && q.data.items.length === 0 && (
          <li className="text-muted">No live actions yet.</li>
        )}
      </ul>
    </Panel>
  );
}
