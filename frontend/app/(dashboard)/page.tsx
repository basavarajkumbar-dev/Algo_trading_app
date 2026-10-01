"use client";

import { useAuth } from "@/components/auth/auth-provider";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { MarketRegimeCard } from "@/components/market/market-regime-card";
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

        <MarketRegimeCard className="lg:col-span-2" />
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Command Center</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-muted">
            The scanner, AI Market Brain, active signal, active position and trade history widgets
            arrive with the strategy engine and paper trading loop (see{" "}
            <code className="tabular text-xs">docs/development-roadmap.md</code>). Nothing on this
            page represents live market data or a real trade.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
