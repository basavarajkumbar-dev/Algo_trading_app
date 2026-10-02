"use client";

import { signalKey } from "@/components/dashboard/live-scanner";
import { Figure, Panel, errorText } from "@/components/dashboard/panel";
import { ExecuteSignal } from "@/components/trading/execute-signal";
import { Badge } from "@/components/ui/badge";
import { strategyLabel } from "@/lib/dashboard-format";
import { formatPrice, formatScore } from "@/lib/intelligence-format";
import { useScanner } from "@/lib/queries";
import { CATEGORY_VARIANT, sideTone } from "@/lib/signal-format";
import { useUiStore } from "@/lib/stores/ui-store";
import { cn } from "@/lib/utils";
import type { ScoreComponent, TradingSignal } from "@/types/signals";

function WhyRow({ c }: { c: ScoreComponent }) {
  const pct = c.applicable && c.effective_weight > 0 ? (c.points / c.effective_weight) * 100 : 0;
  return (
    <li className="grid grid-cols-[14px_minmax(0,1fr)_auto] items-start gap-2 py-1" data-testid="why-row">
      <span
        className={cn("mt-0.5 text-xs font-semibold", !c.applicable ? "text-muted" : c.passed ? "text-profit" : "text-loss")}
        aria-label={!c.applicable ? "not applicable" : c.passed ? "passed" : "failed"}
      >
        {!c.applicable ? "–" : c.passed ? "✓" : "✕"}
      </span>
      <span className="min-w-0">
        <span className="text-xs font-medium">{c.label}</span>
        <span className="block text-[11px] leading-snug text-muted">{c.applicable ? c.detail : "not applicable to this strategy"}</span>
        {c.applicable && (
          <span className="mt-1 block h-1 overflow-hidden rounded-full bg-surface-alt">
            <span className="block h-full bg-accent" style={{ width: `${Math.max(0, Math.min(100, pct))}%` }} />
          </span>
        )}
      </span>
      <span className="tabular text-right text-xs">
        {c.applicable ? c.points.toFixed(1) : "—"}
        <span className="text-muted"> / {c.applicable ? c.effective_weight.toFixed(1) : c.weight}</span>
      </span>
    </li>
  );
}

export function pickSignal(signals: TradingSignal[], key: string | null): TradingSignal | null {
  if (key) {
    const match = signals.find(
      (s) => signalKey({ symbol: s.symbol, signal_strategy: s.strategy, signal_direction: s.direction }) === key
    );
    if (match) return match;
  }
  return signals[0] ?? null;
}

/**
 * The selected (or top-ranked) signal from the latest scan with its full
 * score breakdown: every factor, whether it passed, and the points it
 * contributed. A signal is a scored candidate, not an order.
 */
export function SignalPanel() {
  const scanner = useScanner();
  const key = useUiStore((s) => s.dashboardSignalKey);
  const signal = pickSignal(scanner.data?.scan.signals ?? [], key);

  return (
    <Panel
      title="Signal"
      href="/signals"
      linkLabel="All signals"
      loading={scanner.isLoading}
      error={errorText(scanner.error)}
      testId="signal-panel"
    >
      {!signal ? (
        <p className="text-sm text-muted">
          The latest scan has no scored signal. Signals appear when a routed strategy finds a setup.
        </p>
      ) : (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-lg font-semibold" data-testid="signal-symbol">
              {signal.symbol}
            </span>
            <span className={cn("text-sm font-semibold", sideTone(signal.direction))}>{signal.direction}</span>
            <Badge variant={CATEGORY_VARIANT[signal.category]}>{signal.category}</Badge>
            {signal.executable && <Badge variant="accent">READY</Badge>}
            <span className="text-xs text-muted">{strategyLabel(signal.strategy)}</span>
          </div>
          <div className="grid grid-cols-3 gap-4 sm:grid-cols-6">
            <Figure label="Score">{formatScore(signal.score)}</Figure>
            <Figure label="Entry">{formatPrice(signal.entry)}</Figure>
            <Figure label="SL">{formatPrice(signal.stop_loss)}</Figure>
            <Figure label="Target">{formatPrice(signal.target)}</Figure>
            <Figure label="Quantity" hint="candidate">
              {signal.quantity_candidate}
            </Figure>
            <Figure label="Risk : reward">{signal.risk_reward.toFixed(2)}</Figure>
          </div>
          <section>
            <h4 className="mb-1 text-[11px] uppercase tracking-wide text-muted">
              Why? {formatScore(signal.breakdown.total)} / 100, executable at {signal.breakdown.executable_threshold}
            </h4>
            <ul className="divide-y divide-border">
              {signal.breakdown.components.map((c) => (
                <WhyRow key={c.key} c={c} />
              ))}
            </ul>
          </section>
          {signal.id && <ExecuteSignal key={signal.id} signalId={signal.id} active={signal.status === "ACTIVE"} />}
          <p className="text-[10px] leading-snug text-muted">
            The quantity is a candidate from default capital; the risk engine sizes any paper order
            itself. A score is not proof of profitability.
          </p>
        </div>
      )}
    </Panel>
  );
}
