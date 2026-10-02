"use client";

import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useEffect, useState } from "react";

import { SignalDetail } from "@/components/signals/signal-detail";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { formatIstTime, formatScore } from "@/lib/intelligence-format";
import { getSignalRequest, getSignalsRequest } from "@/lib/market-client";
import { CATEGORY_VARIANT, sideTone } from "@/lib/signal-format";
import { STRATEGY_LABEL } from "@/lib/strategy-format";
import { cn } from "@/lib/utils";
import type { SignalList, StoredSignal } from "@/types/signals";

const POLL_MS = 15_000;
const STATUSES = ["", "ACTIVE", "EXPIRED"] as const;
const CATEGORIES = ["", "ELITE", "STRONG", "WATCH"] as const;

function Chips<T extends string>({
  options,
  value,
  onChange,
  label,
}: {
  options: readonly T[];
  value: T;
  onChange: (value: T) => void;
  label: (value: T) => string;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((option) => (
        <button
          key={option || "all"}
          type="button"
          onClick={() => onChange(option)}
          className={cn(
            "rounded-sm border px-2 py-0.5 text-xs",
            value === option ? "border-accent/40 bg-accent/10" : "border-border text-muted hover:text-foreground"
          )}
        >
          {label(option)}
        </button>
      ))}
    </div>
  );
}

function SignalsView() {
  const router = useRouter();
  const params = useSearchParams();
  const selectedId = params.get("id");
  const [status, setStatus] = useState<(typeof STATUSES)[number]>("");
  const [category, setCategory] = useState<(typeof CATEGORIES)[number]>("");
  const [list, setList] = useState<SignalList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detail, setDetail] = useState<StoredSignal | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    function poll() {
      getSignalsRequest({ status, category, limit: 100 })
        .then((d) => {
          if (cancelled) return;
          setList(d);
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
  }, [status, category]);

  const activeId = selectedId ?? list?.items[0]?.signal.id ?? null;

  useEffect(() => {
    if (!activeId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    getSignalRequest(activeId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        setDetailError(null);
      })
      .catch((err: unknown) => !cancelled && setDetailError(err instanceof Error ? err.message : "Request failed"));
    return () => {
      cancelled = true;
    };
  }, [activeId, list]);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold tracking-tight">Signals</h1>
          <p className="mt-1 max-w-3xl text-sm text-muted">
            Stored signals, newest first. A signal is kept from the scan where a setup first appeared;
            it stays active while later scans keep finding it and expires when they stop. Signals are
            scored candidates, not orders.
          </p>
        </div>
        <div className="flex shrink-0 flex-col gap-2 md:items-end">
          <Chips options={STATUSES} value={status} onChange={setStatus} label={(v) => (v ? v.charAt(0) + v.slice(1).toLowerCase() : "All statuses")} />
          <Chips options={CATEGORIES} value={category} onChange={setCategory} label={(v) => v || "All categories"} />
        </div>
      </div>

      {error && (
        <Card>
          <CardContent className="py-6 text-sm text-loss">Could not load signals: {error}</CardContent>
        </Card>
      )}

      <div className="grid grid-cols-1 items-start gap-4 2xl:grid-cols-[480px_minmax(0,1fr)]">
        <Card className="flex max-h-[820px] flex-col overflow-hidden">
          <CardHeader>
            <CardTitle>
              Stored signals <span className="ml-1 text-xs font-normal text-muted">{list?.total ?? 0}</span>
            </CardTitle>
          </CardHeader>
          <CardContent className="min-h-0 flex-1 overflow-y-auto p-0">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface">
                <tr className="border-b border-border text-left text-[10px] uppercase tracking-wide text-muted">
                  <th className="px-3 py-2 font-medium">Generated</th>
                  <th className="px-2 py-2 font-medium">Stock</th>
                  <th className="px-2 py-2 font-medium">Side</th>
                  <th className="px-2 py-2 font-medium">Strategy</th>
                  <th className="px-2 py-2 text-right font-medium">Score</th>
                  <th className="px-3 py-2 font-medium">Status</th>
                </tr>
              </thead>
              <tbody>
                {list && list.items.length === 0 && (
                  <tr>
                    <td colSpan={6} className="px-3 py-6 text-center text-muted">
                      No stored signals match. Signals are stored when a scan produces a candidate
                      scoring at or above the storage score.
                    </td>
                  </tr>
                )}
                {list?.items.map(({ signal: s }) => (
                  <tr
                    key={s.id}
                    onClick={() => router.replace(`/signals?id=${s.id}`)}
                    className={cn(
                      "cursor-pointer border-b border-border last:border-0 hover:bg-surface-alt",
                      activeId === s.id && "bg-surface-alt"
                    )}
                  >
                    <td className="tabular px-3 py-1.5 text-muted">{formatIstTime(s.timestamp)}</td>
                    <td className="px-2 py-1.5 font-medium">{s.symbol}</td>
                    <td className={cn("px-2 py-1.5 font-semibold", sideTone(s.direction))}>{s.direction}</td>
                    <td className="px-2 py-1.5 text-muted">{STRATEGY_LABEL[s.strategy]}</td>
                    <td className="tabular px-2 py-1.5 text-right">
                      <Badge variant={CATEGORY_VARIANT[s.category]} className="text-[10px]">
                        {formatScore(s.score)}
                      </Badge>
                    </td>
                    <td className="px-3 py-1.5 text-muted">{s.status.toLowerCase()}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Signal details</CardTitle>
          </CardHeader>
          <CardContent>
            {detailError && <p className="text-sm text-loss">{detailError}</p>}
            {!detailError && !detail && (
              <p className="text-sm text-muted">{activeId ? "Loading…" : "Select a signal."}</p>
            )}
            {detail && (
              <SignalDetail
                signal={detail.signal}
                record={{
                  generated_at: detail.generated_at,
                  last_seen_at: detail.last_seen_at,
                  expired_at: detail.expired_at,
                  last_score: detail.last_score,
                  last_rank: detail.last_rank,
                  times_seen: detail.times_seen,
                }}
              />
            )}
          </CardContent>
        </Card>
      </div>
      {list && !detail && <p className="text-[11px] text-muted">{list.note}</p>}
    </div>
  );
}

export default function SignalsPage() {
  // useSearchParams needs a Suspense boundary for static rendering.
  return (
    <Suspense fallback={<p className="text-sm text-muted">Loading…</p>}>
      <SignalsView />
    </Suspense>
  );
}
