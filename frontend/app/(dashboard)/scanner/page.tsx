"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { RegimeBadge } from "@/components/market/regime-badge";
import { SignalDetail } from "@/components/signals/signal-detail";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { changeTone, formatIstTime, formatPercent, formatPrice, formatScore, humanize } from "@/lib/intelligence-format";
import { getScannerRequest } from "@/lib/market-client";
import { SCAN_STATUS, SCAN_STATUS_ORDER, arrow, scoreTone, sideTone } from "@/lib/signal-format";
import { STRATEGY_LABEL } from "@/lib/strategy-format";
import { cn } from "@/lib/utils";
import type { ScanRow, ScanStatus, ScannerResponse } from "@/types/signals";

const POLL_MS = 15_000;

function DirectionCell({ direction, bias }: { direction: string | null; bias: number | null }) {
  return (
    <span className={cn("tabular", sideTone(direction))} title={bias !== null ? `bias ${Math.round(bias)}` : undefined}>
      {arrow(direction)}
    </span>
  );
}

function RowAnalysis({ row }: { row: ScanRow }) {
  const items: [string, string][] = [
    ["Regime", `${humanize(row.regime)} (${formatScore(row.regime_confidence)}%)`],
    ["60m / 15m / 5m", `${humanize(row.context_direction)} / ${humanize(row.setup_direction)} / ${humanize(row.entry_direction)}`],
    ["Alignment", `${formatScore(row.alignment_score)} (${humanize(row.overall_bias)})`],
    ["Setup", humanize(row.setup_state)],
    ["Structure", `${humanize(row.structure_trend)} · ${humanize(row.structure_state)}`],
    ["VWAP", humanize(row.price_vs_vwap)],
    ["Trend quality", `${formatScore(row.trend_quality)} (${humanize(row.trend_direction)})`],
    ["Retracement", row.retracement_percentage === null ? "—" : `${Math.round(row.retracement_percentage)}% · ${humanize(row.retracement_zone)}`],
    ["Support", row.nearest_support ? `${row.nearest_support.label} ${formatPrice(row.nearest_support.price)}` : "—"],
    ["Resistance", row.nearest_resistance ? `${row.nearest_resistance.label} ${formatPrice(row.nearest_resistance.price)}` : "—"],
    ["Price action", row.patterns.map((p) => p.pattern).join(", ") || "—"],
    ["Volume", row.relative_volume === null ? "—" : `${row.relative_volume.toFixed(2)}×`],
    ["Momentum", formatScore(row.momentum_score)],
    ["Relative strength", `${formatScore(row.relative_strength)}${row.relative_strength_rank ? ` (rank ${row.relative_strength_rank})` : ""}`],
    ["Strategies routed", row.strategies.map((s) => STRATEGY_LABEL[s as keyof typeof STRATEGY_LABEL] ?? s).join(", ") || "none"],
  ];
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <span className="text-lg font-semibold">{row.symbol}</span>
        <Badge variant={SCAN_STATUS[row.status].variant}>{SCAN_STATUS[row.status].label}</Badge>
      </div>
      <p className="text-sm text-muted">{SCAN_STATUS[row.status].hint}.</p>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-1.5 text-xs sm:grid-cols-2">
        {items.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-3 border-b border-border/60 py-1">
            <dt className="text-muted">{k}</dt>
            <dd className="text-right">{v}</dd>
          </div>
        ))}
      </dl>
      {Object.keys(row.strategy_stages).length > 0 && (
        <p className="text-xs text-muted">
          Furthest stage:{" "}
          {Object.entries(row.strategy_stages)
            .map(([s, stage]) => `${STRATEGY_LABEL[s as keyof typeof STRATEGY_LABEL] ?? s}: ${stage ? humanize(stage) : "none"}`)
            .join(" · ")}
        </p>
      )}
    </div>
  );
}

export default function ScannerPage() {
  const [data, setData] = useState<ScannerResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<ScanStatus | "ALL">("ALL");
  const [selected, setSelected] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      getScannerRequest()
        .then((d) => {
          if (cancelled) return;
          setData(d);
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

  const scan = data?.scan;
  const rows = useMemo(
    () => (scan?.rows ?? []).filter((r) => filter === "ALL" || r.status === filter),
    [scan, filter]
  );
  const symbol = selected ?? scan?.rows[0]?.symbol ?? null;
  const row = scan?.rows.find((r) => r.symbol === symbol) ?? null;
  const signal = scan?.signals.find((s) => s.symbol === symbol) ?? null;
  const market = scan?.market_context;
  const status = data?.status;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">NIFTY 50 Scanner</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            Every NIFTY 50 stock through regime, multi-timeframe, price action and strategy analysis,
            with scored and ranked candidates. Simulated data. A rank orders the current evidence; it
            is not a forecast and not proof of profitability.
          </p>
        </div>
        <div className="flex shrink-0 flex-col gap-1 text-[11px] text-muted md:items-end">
          <span>
            Scan {status?.scan_count ?? "—"} · {formatIstTime(scan?.scanned_at, true)}
            {status?.last_compute_ms != null ? ` · ${Math.round(status.last_compute_ms)} ms` : ""}
          </span>
          <span>
            Executable threshold {status?.executable_threshold ?? "—"} ·{" "}
            {status?.persistence_ok ? "signals stored" : (
              <span className="text-warning">signals not stored{status?.persistence_error ? `: ${status.persistence_error}` : ""}</span>
            )}
          </span>
        </div>
      </div>

      {error && !data && (
        <Card>
          <CardContent className="py-6 text-sm text-loss">Could not load the scanner: {error}</CardContent>
        </Card>
      )}

      {scan && (
        <>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
            <Card>
              <CardHeader>
                <CardTitle>Market context</CardTitle>
              </CardHeader>
              <CardContent className="flex flex-col gap-3 text-sm">
                {market?.available ? (
                  <>
                    <div className="flex items-baseline justify-between">
                      <span>NIFTY 50</span>
                      <span className="tabular">
                        {formatPrice(market.price)}{" "}
                        <span className={changeTone(market.change_percent)}>{formatPercent(market.change_percent)}</span>
                      </span>
                    </div>
                    <div className="flex items-center justify-between">
                      {market.regime && <RegimeBadge regime={market.regime} />}
                      <span className={cn("text-xs", sideTone(market.direction))}>
                        {humanize(market.direction)} bias {market.bias_score !== null ? Math.round(market.bias_score) : "—"}
                      </span>
                    </div>
                  </>
                ) : (
                  <p className="text-muted">Index context unavailable.</p>
                )}
                <div className="flex flex-wrap gap-1.5 border-t border-border pt-3">
                  <button
                    type="button"
                    onClick={() => setFilter("ALL")}
                    className={cn(
                      "rounded-sm border px-2 py-0.5 text-xs",
                      filter === "ALL" ? "border-accent/40 bg-accent/10" : "border-border text-muted"
                    )}
                  >
                    All {scan.rows.length}
                  </button>
                  {SCAN_STATUS_ORDER.map((s) => (
                    <button
                      key={s}
                      type="button"
                      onClick={() => setFilter(s)}
                      title={SCAN_STATUS[s].hint}
                      disabled={scan.status_counts[s] === 0}
                      className={cn(
                        "rounded-sm border px-2 py-0.5 text-xs disabled:opacity-40",
                        filter === s ? "border-accent/40 bg-accent/10" : "border-border text-muted"
                      )}
                    >
                      {SCAN_STATUS[s].label} {scan.status_counts[s]}
                    </button>
                  ))}
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>Top candidates</CardTitle>
              </CardHeader>
              <CardContent>
                {scan.top_candidates.length === 0 ? (
                  <p className="text-sm text-muted">
                    No candidate scores in the watch band or above right now. Most of the time most
                    stocks should have no setup.
                    {scan.candidates.length > 0 && ` ${scan.candidates.length} lower-scoring candidate(s) are in the table.`}
                  </p>
                ) : (
                  <ol className="flex flex-col gap-2">
                    {scan.top_candidates.map((c) => (
                      <li key={`${c.symbol}-${c.strategy}`}>
                        <button
                          type="button"
                          onClick={() => setSelected(c.symbol)}
                          className="flex w-full items-center gap-3 rounded border border-border px-3 py-2 text-left text-sm hover:bg-surface-alt"
                        >
                          <span className="tabular w-6 text-muted">{c.rank}</span>
                          <span className="w-24 font-medium">{c.symbol}</span>
                          <span className={cn("w-10 text-xs font-semibold", sideTone(c.direction))}>{c.direction}</span>
                          <span className="tabular w-8">{formatScore(c.score)}</span>
                          <span className="flex-1 text-xs text-muted">{STRATEGY_LABEL[c.strategy]}</span>
                          <Badge variant={c.status === "READY" ? "accent" : "pending"}>{c.status}</Badge>
                        </button>
                      </li>
                    ))}
                  </ol>
                )}
                <p className="mt-3 text-[11px] text-muted">{scan.ranking_note}</p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Scanner</CardTitle>
            </CardHeader>
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-xs">
                  <thead>
                    <tr className="border-b border-border text-left text-[10px] uppercase tracking-wide text-muted">
                      <th className="px-2 py-2 font-medium">Rank</th>
                      <th className="px-2 py-2 font-medium">Stock</th>
                      <th className="px-2 py-2 text-right font-medium">Price</th>
                      <th className="px-2 py-2 text-right font-medium">Change</th>
                      <th className="px-2 py-2 font-medium">Signal</th>
                      <th className="px-2 py-2 font-medium">Score</th>
                      <th className="px-2 py-2 font-medium">Strategy</th>
                      <th className="px-2 py-2 font-medium">Status</th>
                      <th className="px-2 py-2 font-medium">Regime</th>
                      <th className="px-2 py-2 text-center font-medium" title="60m context / 15m setup / 5m entry">60·15·5</th>
                      <th className="px-2 py-2 font-medium">Struct.</th>
                      <th className="px-2 py-2 font-medium">VWAP</th>
                      <th className="px-2 py-2 text-right font-medium">Trend Q</th>
                      <th className="px-2 py-2 text-right font-medium">Retr.</th>
                      <th className="px-2 py-2 font-medium">PA</th>
                      <th className="px-2 py-2 text-right font-medium">Vol</th>
                      <th className="px-2 py-2 text-right font-medium">Mom</th>
                      <th className="px-2 py-2 text-right font-medium">RS</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r) => (
                      <tr
                        key={r.symbol}
                        onClick={() => setSelected(r.symbol)}
                        className={cn(
                          "cursor-pointer border-b border-border last:border-0 hover:bg-surface-alt",
                          symbol === r.symbol && "bg-surface-alt"
                        )}
                      >
                        <td className="tabular px-2 py-1.5 text-muted">{r.rank ?? "—"}</td>
                        <td className="px-2 py-1.5 font-medium">{r.symbol}</td>
                        <td className="tabular px-2 py-1.5 text-right">{formatPrice(r.price)}</td>
                        <td className={cn("tabular px-2 py-1.5 text-right", changeTone(r.change_percent))}>
                          {formatPercent(r.change_percent)}
                        </td>
                        <td className={cn("px-2 py-1.5 font-semibold", sideTone(r.signal_direction))}>
                          {r.signal_direction ?? "—"}
                        </td>
                        <td className="px-2 py-1.5">
                          {r.score === null ? (
                            <span className="text-muted">—</span>
                          ) : (
                            <div className="flex items-center gap-1.5">
                              <span className="tabular w-6">{formatScore(r.score)}</span>
                              <div className="h-1 w-10 overflow-hidden rounded-sm bg-surface-alt">
                                <div className={cn("h-full", scoreTone(r.score))} style={{ width: `${r.score}%` }} />
                              </div>
                            </div>
                          )}
                        </td>
                        <td className="px-2 py-1.5 text-muted">
                          {r.signal_strategy ? STRATEGY_LABEL[r.signal_strategy] : "—"}
                        </td>
                        <td className="px-2 py-1.5">
                          <Badge variant={SCAN_STATUS[r.status].variant} className="text-[10px]">
                            {SCAN_STATUS[r.status].label}
                          </Badge>
                        </td>
                        <td className="px-2 py-1.5">{r.regime && <RegimeBadge regime={r.regime} short className="text-[10px]" />}</td>
                        <td className="px-2 py-1.5 text-center">
                          <DirectionCell direction={r.context_direction} bias={r.context_bias} />{" "}
                          <DirectionCell direction={r.setup_direction} bias={r.setup_bias} />{" "}
                          <DirectionCell direction={r.entry_direction} bias={r.entry_bias} />
                        </td>
                        <td className={cn("px-2 py-1.5", sideTone(r.structure_trend))}>{arrow(r.structure_trend)}</td>
                        <td className={cn("px-2 py-1.5", r.price_vs_vwap === "ABOVE" ? "text-profit" : r.price_vs_vwap === "BELOW" ? "text-loss" : "text-muted")}>
                          {humanize(r.price_vs_vwap)}
                        </td>
                        <td className="tabular px-2 py-1.5 text-right">{formatScore(r.trend_quality)}</td>
                        <td className="tabular px-2 py-1.5 text-right" title={humanize(r.retracement_zone)}>
                          {r.retracement_percentage === null ? "—" : `${Math.round(r.retracement_percentage)}%`}
                        </td>
                        <td className="px-2 py-1.5">
                          {r.patterns.map((p) => (
                            <span key={p.pattern} className={cn("mr-1 text-[10px] font-medium", sideTone(p.direction))}>
                              {p.pattern}
                            </span>
                          ))}
                        </td>
                        <td className="tabular px-2 py-1.5 text-right">{r.relative_volume?.toFixed(1) ?? "—"}</td>
                        <td className="tabular px-2 py-1.5 text-right">{formatScore(r.momentum_score)}</td>
                        <td className="tabular px-2 py-1.5 text-right">{formatScore(r.relative_strength)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <CardTitle>{signal ? "Signal details" : "Stock analysis"}</CardTitle>
              {signal?.id && (
                <Link href={`/signals?id=${signal.id}`} className="text-xs text-accent hover:underline">
                  Open stored signal →
                </Link>
              )}
            </CardHeader>
            <CardContent>
              {signal ? (
                <SignalDetail signal={signal} />
              ) : row ? (
                <RowAnalysis row={row} />
              ) : (
                <p className="text-sm text-muted">Select a stock.</p>
              )}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}
