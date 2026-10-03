"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";
import { useMemo, useState, type ReactNode } from "react";

import { useAuth } from "@/components/auth/auth-provider";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { createBacktestRequest } from "@/lib/backtest-client";
import {
  MODE_HELP,
  MODE_LABEL,
  buildGrid,
  buildRequest,
  initialForm,
  matchingVersion,
  versionKey,
  weekdaysBetween,
  type BacktestForm,
} from "@/lib/backtest-format";
import { qk } from "@/lib/query-keys";
import { cn } from "@/lib/utils";
import type { BacktestOptions, ParamSpec, ParamValue, RunSummary, StrategyChoice } from "@/types/backtest";

const inputCls =
  "tabular w-full rounded border border-border bg-surface-alt px-2.5 py-1.5 text-sm text-foreground outline-none focus:border-accent disabled:opacity-50";

function Field({ label, hint, children, className }: { label: string; hint?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <label className={cn("flex min-w-0 flex-col gap-1 text-xs text-muted", className)}>
      {label}
      {children}
      {hint && <span className="text-[11px] leading-snug text-muted/80">{hint}</span>}
    </label>
  );
}

function NumberInput({
  value,
  onChange,
  min,
  max,
  step,
  testId,
}: {
  value: number;
  onChange: (v: number) => void;
  min?: number;
  max?: number;
  step?: number | string;
  testId?: string;
}) {
  return (
    <input
      type="number"
      className={inputCls}
      value={Number.isFinite(value) ? value : ""}
      min={min}
      max={max}
      step={step ?? "any"}
      data-testid={testId}
      onChange={(e) => onChange(e.target.value === "" ? Number.NaN : Number(e.target.value))}
    />
  );
}

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-border pt-4 first:border-t-0 first:pt-0">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

function ParamRow({
  spec,
  value,
  grid,
  onValue,
  onGrid,
}: {
  spec: ParamSpec;
  value: ParamValue;
  grid: string;
  onValue: (v: ParamValue) => void;
  onGrid: (v: string) => void;
}) {
  const range =
    spec.kind === "choice"
      ? (spec.choices ?? []).join(" / ")
      : `${spec.minimum ?? "…"} – ${spec.maximum ?? "…"}${spec.step ? `, step ${spec.step}` : ""}`;
  return (
    <tr className="border-b border-border/60 last:border-0" data-testid={`param-${spec.key}`}>
      <td className="py-1.5 pr-2 align-middle">
        <div className="text-xs text-foreground">{spec.label}</div>
        <div className="text-[10px] text-muted">
          {spec.scope === "execution" ? "execution" : "strategy"} · {range}
        </div>
      </td>
      <td className="w-28 py-1.5 pr-2 align-middle">
        {spec.kind === "choice" ? (
          <select className={inputCls} value={String(value)} onChange={(e) => onValue(e.target.value)} aria-label={spec.label}>
            {(spec.choices ?? []).map((c) => (
              <option key={String(c)} value={String(c)}>
                {String(c)}
              </option>
            ))}
          </select>
        ) : (
          <input
            type="number"
            className={inputCls}
            aria-label={spec.label}
            value={value as number}
            min={spec.minimum ?? undefined}
            max={spec.maximum ?? undefined}
            step={spec.step ?? "any"}
            onChange={(e) => onValue(e.target.value === "" ? spec.default : Number(e.target.value))}
          />
        )}
      </td>
      <td className="w-36 py-1.5 align-middle">
        <input
          type="text"
          className={cn(inputCls, "text-xs")}
          placeholder={spec.kind === "choice" ? "e.g. NONE, ATR" : "e.g. 55, 65, 75"}
          aria-label={`${spec.label} grid values`}
          value={grid}
          onChange={(e) => onGrid(e.target.value)}
        />
      </td>
    </tr>
  );
}

export function BacktestFormCard({
  options,
  onSubmitted,
}: {
  options: BacktestOptions;
  onSubmitted: (run: RunSummary) => void;
}) {
  const { accessToken, user } = useAuth();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<BacktestForm>(() => initialForm(options));
  const [symbolFilter, setSymbolFilter] = useState("");
  const [showCharges, setShowCharges] = useState(false);

  const strategy = options.strategies.find((s) => s.value === form.strategy) ?? options.strategies[0]!;
  const specs = strategy.parameters;
  const source = options.data_sources.find((s) => s.kind === form.dataKind) ?? options.data_sources[0];
  const versions = options.versions.filter((v) => v.strategy_choice === form.strategy);
  const matched = matchingVersion(form, specs, options.versions);
  const grid = useMemo(() => buildGrid(form.grid, specs), [form.grid, specs]);
  const days = weekdaysBetween(form.startDate, form.endDate);
  const canRun = user?.role === "ADMIN" || user?.role === "TRADER";

  const set = <K extends keyof BacktestForm>(key: K, value: BacktestForm[K]) => setForm((f) => ({ ...f, [key]: value }));

  const pickStrategy = (value: StrategyChoice) => {
    const s = options.strategies.find((x) => x.value === value)!;
    const def = options.versions.find((v) => v.strategy_choice === value && v.current_default);
    setForm((f) => ({ ...f, strategy: value, params: { ...s.defaults }, grid: {}, version: def ? versionKey(def) : "" }));
  };

  const pickVersion = (key: string) => {
    const v = options.versions.find((x) => versionKey(x) === key);
    setForm((f) => ({ ...f, version: key, params: v ? { ...f.params, ...v.params } : f.params }));
  };

  const toggleSymbol = (sym: string) =>
    setForm((f) => {
      if (f.symbols.includes(sym)) return { ...f, symbols: f.symbols.filter((s) => s !== sym) };
      if (f.symbols.length >= options.limits.max_symbols) return f;
      return { ...f, symbols: [...f.symbols, sym] };
    });

  const built = buildRequest(form, specs, options.versions);
  const tooManyCombos = grid.combinations > options.limits.max_grid_combinations;

  const submit = useMutation({
    mutationFn: () => {
      if (!built.body) throw new Error(built.error ?? "Invalid form");
      return createBacktestRequest(built.body, accessToken ?? "");
    },
    onSuccess: (run) => {
      void queryClient.invalidateQueries({ queryKey: qk.backtestRuns });
      onSubmitted(run);
    },
  });

  const shownSymbols = (source?.symbols ?? []).filter((s) => s.toLowerCase().includes(symbolFilter.toLowerCase()));

  return (
    <Card data-testid="backtest-form">
      <CardHeader>
        <CardTitle>New backtest</CardTitle>
        {source && (
          <Badge variant={source.synthetic ? "pending" : "neutral"} title={source.note}>
            {source.synthetic ? "synthetic data" : source.kind === "zerodha" ? "real NSE data" : "your data"}
          </Badge>
        )}
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <Section title="Data">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Data source" className="col-span-2">
              <select
                className={inputCls}
                value={form.dataKind}
                onChange={(e) => {
                  const kind = e.target.value as BacktestForm["dataKind"];
                  const next = options.data_sources.find((x) => x.kind === kind);
                  // keep only the selected symbols the new source has
                  setForm((f) => ({
                    ...f,
                    dataKind: kind,
                    symbols: next ? f.symbols.filter((sym) => next.symbols.includes(sym)) : f.symbols,
                  }));
                }}
                aria-label="Data source"
              >
                {options.data_sources.map((s) => (
                  <option key={s.kind} value={s.kind} disabled={!s.available}>
                    {s.label}
                  </option>
                ))}
              </select>
            </Field>
            {source && <p className="col-span-2 -mt-1 text-[11px] text-muted">{source.note}</p>}
            <Field label="Start date">
              <input
                type="date"
                className={inputCls}
                value={form.startDate}
                min={source?.first_day ?? undefined}
                max={source?.last_day ?? undefined}
                onChange={(e) => set("startDate", e.target.value)}
                data-testid="bt-start"
              />
            </Field>
            <Field label="End date" hint={`≈ ${days} weekdays (max ${options.limits.max_days})`}>
              <input
                type="date"
                className={inputCls}
                value={form.endDate}
                min={source?.first_day ?? undefined}
                max={source?.last_day ?? undefined}
                onChange={(e) => set("endDate", e.target.value)}
                data-testid="bt-end"
              />
            </Field>
            <Field label="Entry timeframe" hint="Decisions at the close of each bar; higher timeframes for context.">
              <select className={inputCls} value={form.timeframe} onChange={(e) => set("timeframe", e.target.value)} aria-label="Timeframe">
                {options.timeframes.map((t) => (
                  <option key={t}>{t}</option>
                ))}
              </select>
            </Field>
            {source?.synthetic && (
              <Field label="Seed" hint="Same seed, same prices.">
                <NumberInput value={form.seed} min={0} step={1} onChange={(v) => set("seed", Math.max(0, Math.round(v || 0)))} />
              </Field>
            )}
          </div>
          <div className="flex flex-col gap-2">
            <div className="flex items-center justify-between gap-2 text-xs text-muted">
              <span>
                Symbols{" "}
                <span className="tabular text-foreground" data-testid="bt-symbol-count">
                  {form.symbols.length}
                </span>
                /{options.limits.max_symbols}
              </span>
              <div className="flex gap-2">
                <button type="button" className="text-accent hover:underline" onClick={() => set("symbols", [])}>
                  Clear
                </button>
              </div>
            </div>
            <input
              type="search"
              className={cn(inputCls, "text-xs")}
              placeholder="Filter symbols"
              value={symbolFilter}
              onChange={(e) => setSymbolFilter(e.target.value)}
              aria-label="Filter symbols"
            />
            <div className="flex max-h-32 flex-wrap gap-1 overflow-y-auto" data-testid="bt-symbols">
              {shownSymbols.map((s) => {
                const on = form.symbols.includes(s);
                return (
                  <button
                    key={s}
                    type="button"
                    onClick={() => toggleSymbol(s)}
                    aria-pressed={on}
                    className={cn(
                      "rounded-sm border px-1.5 py-0.5 text-[11px] transition-colors",
                      on ? "border-accent bg-accent/15 text-accent" : "border-border text-muted hover:text-foreground"
                    )}
                  >
                    {s}
                  </button>
                );
              })}
            </div>
          </div>
        </Section>

        <Section title="Strategy">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Strategy">
              <select
                className={inputCls}
                value={form.strategy}
                onChange={(e) => pickStrategy(e.target.value as StrategyChoice)}
                aria-label="Strategy"
              >
                {options.strategies.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field
              label="Version"
              hint={
                matched ? (
                  <span data-testid="bt-version-match">
                    {matched.version_ids.join(" / ")}
                  </span>
                ) : (
                  <span data-testid="bt-version-match">Edited parameters: a new version is recorded with the run.</span>
                )
              }
            >
              <select className={inputCls} value={matched ? versionKey(matched) : form.version} onChange={(e) => pickVersion(e.target.value)} aria-label="Version">
                {versions.map((v) => (
                  <option key={versionKey(v)} value={versionKey(v)}>
                    {v.label}
                  </option>
                ))}
              </select>
            </Field>
          </div>
        </Section>

        <Section
          title="Parameters"
          aside={
            <span className={cn("text-[11px]", tooManyCombos || grid.error ? "text-loss" : "text-muted")} data-testid="bt-grid-count">
              {grid.error
                ? grid.error
                : grid.combinations
                  ? `${grid.combinations} combinations (max ${options.limits.max_grid_combinations})`
                  : "no grid"}
            </span>
          }
        >
          <p className="-mt-1 text-[11px] leading-snug text-muted">
            Value = what the run uses. Grid = optional alternatives (comma-separated). In historical mode the grid is a
            sensitivity table only; in out-of-sample and walk-forward modes a set is chosen on training data by mean R
            minus one standard error, averaged with its neighbours, not by the highest profit.
          </p>
          <table className="w-full text-left">
            <thead>
              <tr className="text-[10px] uppercase tracking-wide text-muted">
                <th className="pb-1 font-normal">Parameter</th>
                <th className="pb-1 font-normal">Value</th>
                <th className="pb-1 font-normal">Grid</th>
              </tr>
            </thead>
            <tbody>
              {specs.map((spec) => (
                <ParamRow
                  key={spec.key}
                  spec={spec}
                  value={form.params[spec.key] ?? spec.default}
                  grid={form.grid[spec.key] ?? ""}
                  onValue={(v) => setForm((f) => ({ ...f, params: { ...f.params, [spec.key]: v } }))}
                  onGrid={(v) => setForm((f) => ({ ...f, grid: { ...f.grid, [spec.key]: v } }))}
                />
              ))}
            </tbody>
          </table>
        </Section>

        <Section title="Validation">
          <div className="grid grid-cols-3 gap-1 rounded border border-border p-0.5" role="radiogroup" aria-label="Mode">
            {options.modes.map((m) => (
              <button
                key={m}
                type="button"
                role="radio"
                aria-checked={form.mode === m}
                onClick={() => set("mode", m)}
                className={cn(
                  "rounded-sm px-2 py-1.5 text-xs transition-colors",
                  form.mode === m ? "bg-surface-alt text-foreground" : "text-muted hover:text-foreground"
                )}
              >
                {MODE_LABEL[m]}
              </button>
            ))}
          </div>
          <p className="text-[11px] leading-snug text-muted" data-testid="bt-mode-help">
            {MODE_HELP[form.mode]}
          </p>
          <div className="grid grid-cols-2 gap-3">
            {form.mode === "OUT_OF_SAMPLE" && (
              <Field label="Out-of-sample share" hint="The last part of the range, never used for selection.">
                <NumberInput
                  value={form.validation.oos_fraction}
                  min={0.1}
                  max={0.6}
                  step={0.05}
                  onChange={(v) => set("validation", { ...form.validation, oos_fraction: v })}
                />
              </Field>
            )}
            {form.mode === "WALK_FORWARD" && (
              <>
                <Field label="Train days">
                  <NumberInput value={form.validation.wf_train_days} min={5} step={1} onChange={(v) => set("validation", { ...form.validation, wf_train_days: v })} />
                </Field>
                <Field label="Test days">
                  <NumberInput value={form.validation.wf_test_days} min={2} step={1} onChange={(v) => set("validation", { ...form.validation, wf_test_days: v })} />
                </Field>
                <label className="col-span-2 flex items-center gap-2 text-xs text-muted">
                  <input
                    type="checkbox"
                    checked={form.validation.wf_anchored}
                    onChange={(e) => set("validation", { ...form.validation, wf_anchored: e.target.checked })}
                    className="accent-[hsl(var(--accent))]"
                  />
                  Anchored (training always starts at the first day)
                </label>
              </>
            )}
            {form.mode !== "HISTORICAL" && (
              <Field label="Min trades to select" hint="Sets with fewer training trades are not eligible.">
                <NumberInput
                  value={form.validation.min_trades_for_selection}
                  min={2}
                  step={1}
                  onChange={(v) => set("validation", { ...form.validation, min_trades_for_selection: v })}
                />
              </Field>
            )}
          </div>
          <label className="flex items-start gap-2 text-xs text-muted">
            <input
              type="checkbox"
              checked={form.validation.null_control}
              onChange={(e) => set("validation", { ...form.validation, null_control: e.target.checked })}
              className="mt-0.5 accent-[hsl(var(--accent))]"
              data-testid="bt-null-control"
            />
            <span>
              Also run on no-edge control data (random-walk prices, same volatility). Rules that profit there are
              suspect. Roughly doubles the run time.
            </span>
          </label>
        </Section>

        <Section title="Capital &amp; risk">
          <div className="grid grid-cols-2 gap-3">
            <Field label="Capital (₹)">
              <NumberInput value={form.capital} min={10_000} step={1000} onChange={(v) => set("capital", v)} testId="bt-capital" />
            </Field>
            <Field label="Risk per trade (%)">
              <NumberInput value={form.risk.risk_per_trade_percent} min={0.1} max={5} step={0.1} onChange={(v) => set("risk", { ...form.risk, risk_per_trade_percent: v })} />
            </Field>
            <Field label="Daily loss limit (%)">
              <NumberInput value={form.risk.daily_loss_limit_percent} min={0.5} max={20} step={0.5} onChange={(v) => set("risk", { ...form.risk, daily_loss_limit_percent: v })} />
            </Field>
            <Field label="Max trades / day">
              <NumberInput value={form.risk.max_trades_per_day} min={1} step={1} onChange={(v) => set("risk", { ...form.risk, max_trades_per_day: v })} />
            </Field>
            <Field label="Max consecutive losses">
              <NumberInput value={form.risk.max_consecutive_losses} min={1} step={1} onChange={(v) => set("risk", { ...form.risk, max_consecutive_losses: v })} />
            </Field>
            <Field label="Max position value (₹)">
              <NumberInput value={form.risk.max_position_value} min={1000} step={1000} onChange={(v) => set("risk", { ...form.risk, max_position_value: v, max_exposure: Math.max(form.risk.max_exposure, v) })} />
            </Field>
            <label className="col-span-2 flex items-center gap-2 text-xs text-muted">
              <input
                type="checkbox"
                checked={form.risk.allow_short}
                onChange={(e) => set("risk", { ...form.risk, allow_short: e.target.checked })}
                className="accent-[hsl(var(--accent))]"
              />
              Allow short (intraday) trades
            </label>
          </div>
        </Section>

        <Section
          title="Costs"
          aside={
            <button type="button" className="text-[11px] text-accent hover:underline" onClick={() => setShowCharges((v) => !v)}>
              {showCharges ? "Hide" : "Show"} statutory charges
            </button>
          }
        >
          <div className="grid grid-cols-2 gap-3">
            <Field label="Slippage (bps per fill)" hint="Applied against you on every fill.">
              <NumberInput value={form.costs.slippage_bps} min={0} max={200} step={0.5} onChange={(v) => set("costs", { ...form.costs, slippage_bps: v })} testId="bt-slippage" />
            </Field>
            <Field label="Brokerage cap / order (₹)">
              <NumberInput value={form.costs.brokerage_cap_per_order} min={0} step={1} onChange={(v) => set("costs", { ...form.costs, brokerage_cap_per_order: v })} />
            </Field>
            {showCharges &&
              (
                [
                  ["brokerage_rate", "Brokerage rate"],
                  ["stt_sell_rate", "STT (sell side)"],
                  ["exchange_rate", "Exchange charges"],
                  ["sebi_per_crore", "SEBI (₹ per crore)"],
                  ["stamp_buy_rate", "Stamp duty (buy)"],
                  ["gst_rate", "GST"],
                ] as const
              ).map(([key, label]) => (
                <Field key={key} label={label}>
                  <NumberInput value={form.costs[key]} min={0} onChange={(v) => set("costs", { ...form.costs, [key]: v })} />
                </Field>
              ))}
          </div>
        </Section>

        <Section title="Run">
          <Field label="Name (optional)">
            <input type="text" className={inputCls} value={form.name} maxLength={120} onChange={(e) => set("name", e.target.value)} />
          </Field>
          {(built.error || submit.error) && (
            <p className="text-xs text-loss" data-testid="bt-form-error">
              {built.error ?? (submit.error as Error).message}
            </p>
          )}
          {!canRun && <p className="text-xs text-warning">Running backtests needs the TRADER or ADMIN role.</p>}
          <Button
            type="button"
            onClick={() => submit.mutate()}
            disabled={!canRun || !!built.error || tooManyCombos || submit.isPending}
            data-testid="bt-run"
          >
            <Play className="h-4 w-4" aria-hidden />
            {submit.isPending ? "Queueing…" : "Run backtest"}
          </Button>
          <p className="text-[11px] leading-snug text-muted">
            Runs in the background on the server; paper trading keeps running. Results describe this history under the
            stated assumptions and do not predict future performance.
          </p>
        </Section>
      </CardContent>
    </Card>
  );
}
