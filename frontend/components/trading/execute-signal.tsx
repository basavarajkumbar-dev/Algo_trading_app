"use client";

import { useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/api";
import { executeSignalRequest } from "@/lib/trading-client";
import { REJECTION_LABEL } from "@/lib/trading-format";
import type { RiskDecision } from "@/types/trading";

/** Sends a stored signal through the risk check to the PAPER broker. */
export function ExecuteSignal({ signalId, active }: { signalId: string; active: boolean }) {
  const { accessToken } = useAuth();
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState(false);
  const [decision, setDecision] = useState<RiskDecision | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    if (!accessToken) return;
    setBusy(true);
    setError(null);
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
          Paper trade this signal
        </Button>
        <span className="text-[11px] text-muted">
          Runs every risk check, then places a PAPER market order. No real order is sent.
        </span>
      </div>
      {error && <p className="text-xs text-loss">{error}</p>}
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
