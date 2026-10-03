"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { ApprovalCard, secondsLeft } from "@/components/approvals/approval-card";
import { useAuth } from "@/components/auth/auth-provider";
import { Panel, errorText } from "@/components/dashboard/panel";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { ApiError } from "@/lib/api";
import { approveRequest, rejectRequest, setTradingModeRequest } from "@/lib/approvals-client";
import { formatIstTime } from "@/lib/intelligence-format";
import { useApprovals, useTradingStatus } from "@/lib/queries";
import { qk } from "@/lib/query-keys";
import { useNow } from "@/lib/use-now";
import { cn } from "@/lib/utils";
import type { ApprovalRequest, ApprovalStatus } from "@/types/approvals";

const STATUS_VARIANT: Record<ApprovalStatus, "neutral" | "pending" | "connected" | "disconnected" | "accent"> = {
  PENDING: "accent",
  APPROVED: "pending",
  EXECUTED: "connected",
  FAILED: "disconnected",
  REJECTED: "disconnected",
  EXPIRED: "neutral",
  CANCELLED: "neutral",
};

function decidedBy(a: ApprovalRequest): { who: string; when: string | null } {
  if (a.rejected_by_email) return { who: a.rejected_by_email, when: a.rejected_at };
  if (a.approved_by_email) return { who: a.approved_by_email, when: a.approved_at };
  if (a.status === "EXPIRED") return { who: "system", when: a.expired_at };
  if (a.status === "CANCELLED") return { who: "system", when: null };
  return { who: "—", when: null };
}

function ModeSwitch({ mode, canChange }: { mode: string; canChange: boolean }) {
  const { accessToken } = useAuth();
  const qc = useQueryClient();
  const change = useMutation({
    mutationFn: (m: "PAPER" | "APPROVAL") => setTradingModeRequest(m, accessToken ?? ""),
    onSuccess: (status) => {
      qc.setQueryData(qk.tradingStatus, status);
      void qc.invalidateQueries({ queryKey: qk.approvals });
    },
  });
  return (
    <div className="flex flex-col items-end gap-1">
      <div className="flex items-center gap-1 rounded border border-border p-0.5" role="radiogroup" aria-label="Trading mode">
        {(["PAPER", "APPROVAL"] as const).map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={mode === m}
            disabled={!canChange || change.isPending}
            onClick={() => mode !== m && change.mutate(m)}
            className={cn(
              "rounded-sm px-3 py-1 text-xs font-medium transition-colors disabled:opacity-60",
              mode === m ? "bg-accent text-accent-foreground" : "text-muted hover:text-foreground"
            )}
            data-testid={`mode-${m}`}
          >
            {m}
          </button>
        ))}
      </div>
      {change.error && <span className="text-[11px] text-loss">{(change.error as Error).message}</span>}
      {!canChange && <span className="text-[11px] text-muted">TRADER or ADMIN role needed to change</span>}
    </div>
  );
}

export default function ApprovalsPage() {
  const { accessToken, user } = useAuth();
  const qc = useQueryClient();
  const approvals = useApprovals();
  const trading = useTradingStatus();
  const now = useNow(500);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [message, setMessage] = useState<{ tone: "ok" | "bad"; text: string } | null>(null);
  const canDecide = user?.role === "ADMIN" || user?.role === "TRADER";

  const data = approvals.data;
  const mode = trading.data?.mode ?? data?.mode ?? "PAPER";
  const pending = data?.items.filter((a) => a.status === "PENDING") ?? [];
  const history = data?.items.filter((a) => a.status !== "PENDING") ?? [];
  const fetchedAt = approvals.dataUpdatedAt;

  const decide = useMutation({
    mutationFn: async (args: { id: string; action: "approve" | "reject"; reason?: string }) =>
      args.action === "approve"
        ? approveRequest(args.id, accessToken ?? "")
        : rejectRequest(args.id, args.reason ?? "", accessToken ?? ""),
    onMutate: (args) => {
      setBusyId(args.id);
      setMessage(null);
    },
    onSuccess: (a) => {
      const text =
        a.status === "EXECUTED"
          ? `Approved: paper order placed for ${a.symbol}.`
          : a.status === "FAILED"
            ? `Approved, but not executed: ${a.reason}`
            : a.status === "REJECTED"
              ? `Rejected ${a.symbol}.`
              : `${a.symbol}: ${a.status.toLowerCase()}`;
      setMessage({ tone: a.status === "EXECUTED" || a.status === "REJECTED" ? "ok" : "bad", text });
    },
    onError: (err) => setMessage({ tone: "bad", text: err instanceof ApiError ? err.message : "Request failed" }),
    onSettled: () => {
      setBusyId(null);
      void qc.invalidateQueries({ queryKey: qk.approvals });
      void qc.invalidateQueries({ queryKey: ["trading"] });
    },
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Approvals</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            In APPROVAL mode the algo never trades on its own: each READY signal that passes the risk check waits
            here for a person. A request expires after {data?.ttl_seconds ?? "…"} seconds and an expired one is never
            executed. Approved trades go to the PAPER broker (simulated, no real money).
          </p>
        </div>
        <ModeSwitch mode={mode} canChange={canDecide} />
      </div>

      {mode !== "APPROVAL" && (
        <Card className="px-4 py-3 text-sm text-muted" data-testid="approvals-mode-note">
          Trading mode is <span className="text-foreground">{mode}</span>: trades do not wait for approval. Switch to
          APPROVAL to review every algo trade before it is placed.
        </Card>
      )}
      {mode === "APPROVAL" && trading.data && !trading.data.auto_trading && (
        <Card className="px-4 py-3 text-sm text-muted" data-testid="approvals-algo-note">
          The algo is stopped, so no new requests are proposed. Start it on{" "}
          <Link href="/positions" className="text-accent hover:underline">
            Positions
          </Link>
          ; it will then queue trades here instead of placing them.
        </Card>
      )}

      {message && (
        <Card
          className={cn("px-4 py-3 text-sm", message.tone === "ok" ? "text-profit" : "text-warning")}
          data-testid="approval-message"
        >
          {message.text}
        </Card>
      )}
      {approvals.error && <Card className="px-4 py-3 text-sm text-loss">{errorText(approvals.error)}</Card>}

      <section className="flex flex-col gap-3" data-testid="pending-approvals">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-medium">Waiting for a decision</h2>
          <Badge variant={pending.length ? "accent" : "neutral"} data-testid="pending-count">
            {pending.length}
          </Badge>
        </div>
        {data && pending.length === 0 && (
          <Card className="px-4 py-8 text-center text-sm text-muted" data-testid="approvals-empty">
            Nothing to review.
          </Card>
        )}
        {pending.map((a) => (
          <ApprovalCard
            key={a.id}
            approval={a}
            remaining={secondsLeft(a, fetchedAt, now)}
            ttl={data?.ttl_seconds ?? 120}
            canDecide={canDecide && mode === "APPROVAL"}
            busy={busyId === a.id}
            onApprove={() => decide.mutate({ id: a.id, action: "approve" })}
            onReject={(reason) => decide.mutate({ id: a.id, action: "reject", reason })}
          />
        ))}
      </section>

      <Panel title="Decisions" testId="approval-history" aside={<span className="text-[11px] text-muted">newest first · every decision is audited</span>}>
        {history.length === 0 ? (
          <p className="py-4 text-center text-sm text-muted">No decisions yet.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Requested</TableHead>
                <TableHead>Stock</TableHead>
                <TableHead>Direction</TableHead>
                <TableHead className="text-right">Qty</TableHead>
                <TableHead className="text-right">Score</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>By</TableHead>
                <TableHead>At</TableHead>
                <TableHead>Reason</TableHead>
                <TableHead>Trade</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {history.map((a) => {
                const d = decidedBy(a);
                return (
                  <TableRow key={a.id} data-testid="history-row">
                    <TableCell className="tabular whitespace-nowrap text-xs">{formatIstTime(a.created_at, true)}</TableCell>
                    <TableCell className="font-medium">{a.symbol}</TableCell>
                    <TableCell className={a.direction === "BUY" ? "text-profit" : "text-loss"}>{a.direction}</TableCell>
                    <TableCell className="tabular text-right">{a.quantity}</TableCell>
                    <TableCell className="tabular text-right">{a.score?.toFixed(1) ?? "—"}</TableCell>
                    <TableCell>
                      <Badge variant={STATUS_VARIANT[a.status]}>{a.status.toLowerCase()}</Badge>
                    </TableCell>
                    <TableCell className="max-w-[180px] truncate text-xs">{d.who}</TableCell>
                    <TableCell className="tabular whitespace-nowrap text-xs">{d.when ? formatIstTime(d.when, true) : "—"}</TableCell>
                    <TableCell className="max-w-[320px] truncate text-xs text-muted" title={a.reason ?? a.note ?? ""}>
                      {a.reason ?? a.note ?? "—"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {a.trade_id ? (
                        <Link href="/trades" className="text-accent hover:underline">
                          view
                        </Link>
                      ) : (
                        "—"
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </Panel>
      {data && <p className="text-xs text-muted">{data.note}</p>}
    </div>
  );
}
