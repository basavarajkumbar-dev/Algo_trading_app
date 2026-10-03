"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { requestApprovalRequest } from "@/lib/approvals-client";
import { useTradingStatus } from "@/lib/queries";
import { executeSignalRequest } from "@/lib/trading-client";
import { REJECTION_LABEL } from "@/lib/trading-format";
import type { RiskDecision } from "@/types/trading";

/** Sends a stored signal through the risk check to the PAPER broker; in
 * APPROVAL mode it asks for approval instead. */
export function ExecuteSignal({ signalId, active }: { signalId: string; active: boolean }) {
  const { accessToken } = useAuth();
  const queryClient = useQueryClient();
  const approvalMode = useTradingStatus().data?.mode === "APPROVAL";
  const [busy, setBusy] = useState(false);
  const [requested, setRequested] = useState(false);
  const [decision, setDecision] = useState<RiskDecision | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!accessToken) return;
    setBusy(true);
    setError(null);
    if (approvalMode) {
      try {
        await requestApprovalRequest(signalId, accessToken);
        setRequested(true);
        void queryClient.invalidateQueries({ queryKey: ["approvals"] });
      } catch (err) {
        setError(err instanceof ApiError ? err.message : "Request failed");
      } finally {
        setBusy(false);
      }
      return;
    }
    try {
      const res = await executeSignalRequest(signalId, accessToken);
      setDecision(res.decision);
      queryClient.setQueryData(["trading", "status"], res.status);
      void queryClient.invalidateQueries({ queryKey: ["trading"] });
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Request failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded border border-border p-3">
      <div className="flex flex-wrap items-center gap-3">
        <Button size="sm" disabled={busy || !active || !accessToken} onClick={run}>
          {approvalMode ? "Request approval" : "Paper trade this signal"}
        </Button>
        <span className="text-[11px] text-muted">
          {approvalMode
            ? "APPROVAL mode: runs every risk check and queues the trade for a person to approve."
            : "Runs every risk check, then places a PAPER market order. No real order is sent."}
        </span>
      </div>
      {error && <p className="text-xs text-loss">{error}</p>}
      {requested && (
        <p className="text-xs text-profit">
          Waiting for approval.{" "}
          <Link href="/approvals" className="text-accent hover:underline">
            Review it on Approvals →
          </Link>
        </p>
      )}
      {decision && (
        <p className="text-xs">
          {decision.approved ? (
            <span className="text-profit">
              Approved: {decision.size?.quantity} shares, planned risk ₹{decision.size?.actual_risk.toFixed(2)}.{" "}
              <Link href="/positions" className="text-accent hover:underline">
                Follow it on Paper trading →
              </Link>
            </span>
          ) : (
            <span className="text-loss">
              Rejected: {decision.rejection_reasons.map((r) => REJECTION_LABEL[r]).join(", ")}.
            </span>
          )}
        </p>
      )}
    </div>
  );
}
