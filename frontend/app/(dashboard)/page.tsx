"use client";

import { useAuth } from "@/components/auth/auth-provider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { SystemStatusPanel } from "@/components/system-status-panel";

export default function DashboardPage() {
  const { user } = useAuth();

  return (
    <div className="flex flex-col gap-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">GAINZALGO PRO MAX</h1>
        <p className="mt-1 text-sm text-muted">
          {user ? `Signed in as ${user.email}. ` : ""}Algorithmic intraday trading platform —
          running in PAPER / demo mode.
        </p>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <SystemStatusPanel />

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Command Center</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-sm text-muted">
              This is a Phase 1 placeholder. The market regime card, AI Market Brain panel, live
              scanner, active signal, active position, and trade history widgets described in the
              project brief will be built in the phases that implement market data, the strategy
              engine, and the trading loop (see{" "}
              <code className="tabular text-xs">docs/development-roadmap.md</code>). Nothing on
              this page represents live market data or a real trade.
            </p>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
