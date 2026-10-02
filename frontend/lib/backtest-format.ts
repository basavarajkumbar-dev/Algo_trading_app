/** Pure helpers for the Backtesting page (form → request, labels, grid parsing). */
import type {
  BacktestMode,
  BacktestOptions,
  BacktestRequest,
  CostSpec,
  ParamSpec,
  ParamValue,
  RiskSpec,
  RunStatus,
  StrategyChoice,
  ValidationSpec,
  VersionOption,
} from "@/types/backtest";

export const MODE_LABEL: Record<BacktestMode, string> = {
  HISTORICAL: "Historical",
  OUT_OF_SAMPLE: "Out-of-sample",
  WALK_FORWARD: "Walk-forward",
};

export const MODE_HELP: Record<BacktestMode, string> = {
  HISTORICAL:
    "Runs your parameters over the whole range. Grid values, if any, only produce a sensitivity table; nothing is selected.",
  OUT_OF_SAMPLE:
    "Splits the range by time. Grid values are compared on the earlier in-sample part only; the chosen set is then run once on the later, unseen part, which is what the headline reports.",
  WALK_FORWARD:
    "Rolls train/test windows forward. Each window selects on its training days and is scored on the following test days; the headline is the stitched test results.",
};

export const STATUS_VARIANT: Record<RunStatus, "neutral" | "pending" | "connected" | "disconnected" | "accent"> = {
  QUEUED: "neutral",
  RUNNING: "accent",
  COMPLETED: "connected",
  FAILED: "disconnected",
  CANCELLED: "pending",
};

export const SEGMENT_LABEL: Record<string, string> = {
  FULL: "Full range",
  IN_SAMPLE: "In-sample",
  OUT_OF_SAMPLE: "Out-of-sample",
  TRAIN: "Train",
  TEST: "Test",
};

export const isActive = (status: RunStatus) => status === "QUEUED" || status === "RUNNING";

export interface BacktestForm {
  name: string;
  mode: BacktestMode;
  strategy: StrategyChoice;
  version: string; // a VersionOption key ("" = none chosen)
  symbols: string[];
  startDate: string;
  endDate: string;
  timeframe: string;
  params: Record<string, ParamValue>;
  grid: Record<string, string>; // raw comma-separated text per parameter
  capital: number;
  risk: RiskSpec;
  costs: CostSpec;
  validation: ValidationSpec;
  dataKind: "synthetic" | "csv";
  seed: number;
}

export const versionKey = (v: VersionOption) => `${v.strategy_choice}|${v.version_ids.join(",")}`;

/** Monday–Friday dates in [start, end] (exchange holidays are not known here). */
export function weekdaysBetween(start: string, end: string): number {
  const a = new Date(`${start}T00:00:00Z`);
  const b = new Date(`${end}T00:00:00Z`);
  if (Number.isNaN(a.getTime()) || Number.isNaN(b.getTime()) || b < a) return 0;
  let n = 0;
  for (const d = new Date(a); d <= b; d.setUTCDate(d.getUTCDate() + 1)) {
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) n += 1;
  }
  return n;
}

/** The date `count` weekdays before `end` (inclusive of end). */
export function weekdaysBack(end: string, count: number): string {
  const d = new Date(`${end}T00:00:00Z`);
  let left = count - 1;
  while (left > 0) {
    d.setUTCDate(d.getUTCDate() - 1);
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) left -= 1;
  }
  return d.toISOString().slice(0, 10);
}

export function initialForm(options: BacktestOptions): BacktestForm {
  const source = options.data_sources.find((s) => s.available) ?? options.data_sources[0];
  const strategy: StrategyChoice = "ALL";
  const s = options.strategies.find((x) => x.value === strategy) ?? options.strategies[0]!;
  const end = source?.last_day ?? new Date().toISOString().slice(0, 10);
  const def = options.versions.find((v) => v.strategy_choice === strategy && v.current_default);
  return {
    name: "",
    mode: "HISTORICAL",
    strategy: s.value,
    version: def ? versionKey(def) : "",
    symbols: (source?.symbols ?? []).filter((x) => ["RELIANCE", "HDFCBANK", "INFY", "TCS", "ICICIBANK"].includes(x)),
    startDate: weekdaysBack(end, 30),
    endDate: end,
    timeframe: options.timeframes[0] ?? "5m",
    params: { ...s.defaults },
    grid: {},
    capital: 100_000,
    risk: { ...options.risk },
    costs: { ...options.costs },
    validation: { ...options.validation },
    dataKind: source?.kind ?? "synthetic",
    seed: 42,
  };
}

/** "55, 65,75" → [55, 65, 75]; choices are matched case-insensitively. Throws on bad input. */
export function parseGridValues(raw: string, spec: ParamSpec): ParamValue[] {
  const parts = raw
    .split(/[,\s]+/)
    .map((p) => p.trim())
    .filter(Boolean);
  const out: ParamValue[] = [];
  for (const p of parts) {
    let v: ParamValue;
    if (spec.kind === "choice") {
      const hit = (spec.choices ?? []).find((c) => String(c).toLowerCase() === p.toLowerCase());
      if (hit === undefined) throw new Error(`${spec.label}: "${p}" is not one of ${(spec.choices ?? []).join(", ")}`);
      v = hit;
    } else {
      const n = Number(p);
      if (!Number.isFinite(n)) throw new Error(`${spec.label}: "${p}" is not a number`);
      if (spec.kind === "int" && !Number.isInteger(n)) throw new Error(`${spec.label}: "${p}" must be a whole number`);
      if (spec.minimum !== null && n < spec.minimum) throw new Error(`${spec.label}: ${p} is below ${spec.minimum}`);
      if (spec.maximum !== null && n > spec.maximum) throw new Error(`${spec.label}: ${p} is above ${spec.maximum}`);
      v = n;
    }
    if (!out.includes(v)) out.push(v);
  }
  return out;
}

/** Grid from the raw inputs: only keys with 2+ values count. */
export function buildGrid(
  raw: Record<string, string>,
  specs: ParamSpec[]
): { grid: Record<string, ParamValue[]>; combinations: number; error: string | null } {
  const grid: Record<string, ParamValue[]> = {};
  try {
    for (const spec of specs) {
      const text = raw[spec.key];
      if (!text || !text.trim()) continue;
      const values = parseGridValues(text, spec);
      if (values.length >= 2) grid[spec.key] = values;
    }
  } catch (e) {
    return { grid: {}, combinations: 0, error: (e as Error).message };
  }
  const keys = Object.keys(grid);
  const combinations = keys.length ? keys.reduce((n, k) => n * (grid[k]?.length ?? 1), 1) : 0;
  return { grid, combinations, error: null };
}

/** Strategy-scope parameters of the form (what defines a strategy version). */
export function strategyScopeParams(form: BacktestForm, specs: ParamSpec[]): Record<string, ParamValue> {
  const out: Record<string, ParamValue> = {};
  for (const s of specs) if (s.scope === "strategy") out[s.key] = form.params[s.key] ?? s.default;
  return out;
}

/** The version whose parameters equal the form's strategy parameters, if any. */
export function matchingVersion(
  form: BacktestForm,
  specs: ParamSpec[],
  versions: VersionOption[]
): VersionOption | null {
  const mine = strategyScopeParams(form, specs);
  return (
    versions.find(
      (v) =>
        v.strategy_choice === form.strategy &&
        Object.keys(mine).every((k) => String(v.params[k] ?? "") === String(mine[k]))
    ) ?? null
  );
}

export function buildRequest(
  form: BacktestForm,
  specs: ParamSpec[],
  versions: VersionOption[]
): { body: BacktestRequest | null; error: string | null } {
  if (form.symbols.length === 0) return { body: null, error: "Pick at least one symbol." };
  if (!form.startDate || !form.endDate) return { body: null, error: "Pick a date range." };
  if (form.endDate < form.startDate) return { body: null, error: "The end date is before the start date." };
  const { grid, error } = buildGrid(form.grid, specs);
  if (error) return { body: null, error };
  if (form.mode === "WALK_FORWARD" && Object.keys(grid).length === 0) {
    return { body: null, error: "Walk-forward selects among grid values: give at least one parameter 2+ values." };
  }
  const version = matchingVersion(form, specs, versions);
  const parameters: Record<string, ParamValue> = {};
  for (const s of specs) parameters[s.key] = form.params[s.key] ?? s.default;
  return {
    error: null,
    body: {
      name: form.name.trim() || null,
      mode: form.mode,
      strategy: form.strategy,
      // the server re-derives the versions from the parameters and checks this one is among them
      version: version ? version.version_ids[0] : null,
      symbols: form.symbols,
      start_date: form.startDate,
      end_date: form.endDate,
      timeframe: form.timeframe,
      parameters,
      grid: Object.keys(grid).length ? grid : null,
      capital: form.capital,
      risk: form.risk,
      costs: form.costs,
      validation: form.validation,
      data_source: { kind: form.dataKind, seed: form.seed },
    },
  };
}

export function formatSeconds(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  if (s < 60) return `${Math.round(s)}s`;
  const m = Math.floor(s / 60);
  return `${m}m ${String(Math.round(s % 60)).padStart(2, "0")}s`;
}

/** 0.23 → "23%" (fractions from the robustness report). */
export function formatShare(value: number | null | undefined, digits = 0): string {
  return value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;
}

export function paramDisplay(value: ParamValue | undefined): string {
  if (value === undefined) return "—";
  return typeof value === "number" ? String(Number(value.toFixed(4))) : value;
}

/** Keys whose value differs from the defaults, e.g. "min_score 65 · trailing_mode ATR". */
export function changedParams(
  params: Record<string, ParamValue>,
  defaults: Record<string, ParamValue>
): string {
  const diff = Object.keys(params).filter((k) => String(params[k]) !== String(defaults[k]));
  return diff.length ? diff.map((k) => `${k} ${paramDisplay(params[k])}`).join(" · ") : "defaults";
}
