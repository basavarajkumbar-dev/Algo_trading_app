"use client";

import { useEffect, useMemo, useState } from "react";

import { RegimeBadge } from "@/components/market/regime-badge";
import { StrategyResultCard } from "@/components/strategies/strategy-result-card";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { REGIME_ORDER, formatIstTime, formatPrice, formatScore } from "@/lib/intelligence-format";
import {
  getStrategiesRequest,
  getStrategyConfigRequest,
  getSymbolStrategiesRequest,
} from "@/lib/market-client";
import { STRATEGY_LABEL, directionTone } from "@/lib/strategy-format";
import { cn } from "@/lib/utils";
import type { StrategyEngineConfig, StrategyOverview, SymbolStrategies } from "@/types/strategies";

const POLL_MS = 15_000;

export default function StrategiesPage() {
  const [overview, setOverview] = useState<StrategyOverview | null>(null);
  const [config, setConfig] = useState<StrategyEngineConfig | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [showUnrouted, setShowUnrouted] = useState(false);
  const [detail, setDetail] = useState<SymbolStrategies | null>(null);

  useEffect(() => {
    let cancelled = false;
    getStrategyConfigRequest().then((c) => !cancelled && setConfig(c)).catch(() => undefined);
    function poll() {
      getStrategiesRequest()
        .then((data) => {
          if (cancelled) return;
          setOverview(data);
          setError(null);
        })
        .catch((err: unknown) => !cancelled && setError(err instanceof Error ? err.message : "Request failed"));
    }
    poll();
    const interval = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const rows = useMemo(
    () =>
      [...(overview?.symbols ?? [])].sort(
        (a, b) => b.candidates.length - a.candidates.length || a.symbol.localeCompare(b.symbol)
      ),
    [overview]
  );
  const candidates = rows.flatMap((row) => row.candidates.map((c) => ({ row, c })));
  const symbol = selected ?? rows[0]?.symbol ?? null;

  useEffect(() => {
    if (!symbol) return;
    let cancelled = false;
    function load() {
      getSymbolStrategiesRequest(symbol!, showUnrouted)
        .then((d) => !cancelled && setDetail(d))
        .catch(() => undefined);
    }
    load();
    const interval = setInterval(load, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [symbol, showUnrouted]);

  const routedNames = new Set(detail?.evaluation.decision.eligible_strategies ?? []);
  const status = overview?.status;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">Strategies</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            Strategy candidates on simulated data. Each strategy is a hypothesis to be tested, not a
            proven edge. Candidates are not orders: they are not sized, not risk-checked and never
            sent to a broker.
          </p>
        </div>
        <div className="flex shrink-0 flex-col gap-1 text-[11px] text-muted md:items-end">
          <span>
            {status ? `${status.candidates} candidate(s) across ${status.symbols_evaluated} symbols` : "—"}
          </span>
          <span>
            Updated {formatIstTime(status?.last_refresh_at, true)}
            {status?.last_compute_ms != null ? ` · ${Math.round(status.last_compute_ms)} ms` : ""}
          </span>
          {status?.last_error && <span className="text-loss">{status.last_error}</span>}
        </div>
      </div>

      {error && !overview && (
        <Card>
          <CardContent className="py-6 text-sm text-loss">Could not load strategies: {error}</CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Current candidates</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-border text-left text-[11px] uppercase tracking-wide text-muted">
                  <th className="px-3 py-2 font-medium">Symbol</th>
                  <th className="px-3 py-2 font-medium">Strategy</th>
                  <th className="px-3 py-2 font-medium">Side</th>
                  <th className="px-3 py-2 text-right font-medium">Entry</th>
                  <th className="px-3 py-2 text-right font-medium">Stop</th>
                  <th className="px-3 py-2 text-right font-medium">Target</th>
                  <th className="px-3 py-2 text-right font-medium">R:R</th>
                  <th className="px-3 py-2 text-right font-medium">Conf.</th>
                </tr>
              </thead>
              <tbody>
                {candidates.length === 0 && (
                  <tr>
                    <td colSpan={8} className="px-3 py-6 text-center text-muted">
                      No strategy has an eligible setup right now. Most of the time most strategies
                      should be waiting.
                    </td>
                  </tr>
                )}
                {candidates.map(({ row, c }) => (
                  <tr
                    key={`${row.symbol}-${c.strategy}`}
                    onClick={() => setSelected(row.symbol)}
                    className={cn(
                      "cursor-pointer border-b border-border last:border-0 hover:bg-surface-alt",
                      symbol === row.symbol && "bg-surface-alt"
                    )}
                  >
                    <td className="px-3 py-1.5 font-medium">{row.symbol}</td>
                    <td className="px-3 py-1.5">{STRATEGY_LABEL[c.strategy]}</td>
                    <td className={cn("px-3 py-1.5 text-xs font-medium", directionTone(c.direction))}>{c.direction}</td>
                    <td className="tabular px-3 py-1.5 text-right">{formatPrice(c.entry_candidate)}</td>
                    <td className="tabular px-3 py-1.5 text-right">{formatPrice(c.stop_candidate)}</td>
                    <td className="tabular px-3 py-1.5 text-right">{formatPrice(c.target_candidate)}</td>
                    <td className="tabular px-3 py-1.5 text-right">{c.reward_risk?.toFixed(2) ?? "—"}</td>
                    <td className="tabular px-3 py-1.5 text-right">{formatScore(c.confidence)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Router</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-col gap-2 text-xs">
            <p className="text-muted">
              The regime decides which strategies may run. The order shown is evaluation order, not a
              ranking; no strategy is assumed to be better than another.
            </p>
            {config &&
              REGIME_ORDER.map((regime) => {
                const routes = config.router.routes[regime] ?? [];
                const count = rows.filter((r) => r.regime === regime).length;
                return (
                  <div key={regime} className="flex items-start justify-between gap-2 border-t border-border pt-2">
                    <span className="flex items-center gap-2">
                      <RegimeBadge regime={regime} short />
                      <span className="tabular text-muted">{count}</span>
                    </span>
                    <span className="text-right">
                      {routes.length === 0
                        ? "No trade (defensive)"
                        : routes
                            .map(
                              (r) =>
                                `${STRATEGY_LABEL[r.strategy]}${
                                  r.directions.length === 1 ? ` (${r.directions[0]!.toLowerCase()} only)` : ""
                                }`
                            )
                            .join(", ")}
                    </span>
                  </div>
                );
              })}
            {config && (
              <p className="border-t border-border pt-2 text-muted">
                Minimum regime confidence {config.router.min_regime_confidence}. Breakout opening
                range {config.breakout_momentum.opening_range_minutes}m from 09:15 IST.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[320px_minmax(0,1fr)]">
        <Card className="flex max-h-[720px] flex-col overflow-hidden">
          <CardHeader>
            <CardTitle>Symbols</CardTitle>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 overflow-y-auto p-0">
            <table className="w-full text-xs">
              <tbody>
                {rows.map((row) => (
                  <tr
                    key={row.symbol}
                    onClick={() => setSelected(row.symbol)}
                    className={cn(
                      "cursor-pointer border-b border-border last:border-0 hover:bg-surface-alt",
                      symbol === row.symbol && "bg-surface-alt"
                    )}
                  >
                    <td className="px-3 py-1.5">
                      <div className="flex flex-col">
                        <span className="font-medium">{row.symbol}</span>
                        <span className="tabular text-[10px] text-muted">{formatPrice(row.price)}</span>
                      </div>
                    </td>
                    <td className="px-2 py-1.5">
                      <RegimeBadge regime={row.regime} short className="text-[10px]" />
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      {row.candidates.length > 0 ? (
                        <span className="font-medium text-accent">{row.candidates.length} candidate</span>
                      ) : (
                        <span className="text-muted">
                          {row.eligible_strategies.length === 0 ? "no route" : "waiting"}
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row flex-wrap items-center justify-between gap-3">
            <CardTitle>
              {symbol ?? "—"}
              {detail?.name && <span className="ml-2 text-xs font-normal text-muted">{detail.name}</span>}
            </CardTitle>
            <label className="flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={showUnrouted}
                onChange={(e) => setShowUnrouted(e.target.checked)}
                className="accent-[hsl(var(--accent))]"
              />
              Also show strategies the router did not select (research)
            </label>
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            {detail && detail.symbol === symbol ? (
              <>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <RegimeBadge regime={detail.evaluation.decision.regime} />
                  <span className="text-muted">
                    {formatScore(detail.evaluation.decision.regime_confidence)}% ·{" "}
                    {detail.evaluation.decision.reasons.slice(1).join(" ") ||
                      `routes to ${detail.evaluation.decision.eligible_strategies.map((s) => STRATEGY_LABEL[s]).join(", ")}`}
                  </span>
                </div>
                {detail.evaluation.results.length === 0 && !showUnrouted && (
                  <p className="text-sm text-muted">
                    No strategy is routed for this regime. Tick the box above to see what each strategy
                    would say anyway.
                  </p>
                )}
                {(showUnrouted && detail.unrouted ? detail.unrouted : detail.evaluation.results).map((result) => (
                  <StrategyResultCard
                    key={result.strategy}
                    result={result}
                    routed={!showUnrouted || routedNames.has(result.strategy)}
                  />
                ))}
                <p className="text-[11px] text-muted">
                  Last candle {formatIstTime(detail.evaluation.data_as_of)}. {detail.evaluation.note}
                </p>
              </>
            ) : (
              <p className="text-sm text-muted">Loading…</p>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
