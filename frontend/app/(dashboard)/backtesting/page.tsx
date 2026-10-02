import Link from "next/link";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

/**
 * The backtesting engine is a later phase (roadmap Phase 15). This page
 * says so instead of showing invented results.
 */
export default function BacktestingPage() {
  return (
    <div className="flex flex-col gap-4">
      <div>
        <h1 className="text-xl font-semibold tracking-tight">Backtesting</h1>
        <p className="mt-1 max-w-3xl text-sm text-muted">
          Not built yet. The backtesting engine (historical, out-of-sample and walk-forward runs
          that avoid look-ahead bias) is roadmap Phase 15.
        </p>
      </div>
      <Card data-testid="backtesting-planned">
        <CardHeader>
          <CardTitle>What exists today</CardTitle>
        </CardHeader>
        <CardContent className="flex flex-col gap-2 text-sm">
          <p>
            No backtest results are shown here, because none have been computed. Results of the
            paper trading engine on the simulated feed are on{" "}
            <Link href="/analytics" className="text-accent hover:underline">
              Analytics
            </Link>
            ; they are forward paper results, not a backtest.
          </p>
          <p className="text-xs text-muted">
            Strategy versions (semver + config hash) are already recorded on every signal, so
            future backtests can be tied to the exact strategy configuration they tested.
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
