/** Phase 10 backtesting API types (mirror backend/app/backtesting/schemas.py and api/v1/backtests.py). */
import type { GroupStats } from "@/types/dashboard";

export type BacktestMode = "HISTORICAL" | "OUT_OF_SAMPLE" | "WALK_FORWARD";
export type StrategyChoice =
  | "ALL"
  | "ALL_EXTENDED"
  | "TREND_CONTINUATION"
  | "BREAKOUT_MOMENTUM"
  | "MEAN_REVERSION"
  | "RANGE_TRADING"
  | "BREAKOUT_TRADING"
  | "VOLUME_TRADING"
  | "MA_CROSSOVER";
export type RunStatus = "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
export type ParamValue = number | string;

export interface ParamSpec {
  key: string;
  label: string;
  scope: "strategy" | "execution";
  kind: "float" | "int" | "choice";
  default: ParamValue;
  minimum: number | null;
  maximum: number | null;
  step: number | null;
  choices: ParamValue[] | null;
  path: string | null;
  description: string;
}

export interface StrategyOption {
  value: StrategyChoice;
  label: string;
  parameters: ParamSpec[];
  defaults: Record<string, ParamValue>;
}

export interface VersionOption {
  strategy_choice: StrategyChoice;
  label: string;
  version_ids: string[];
  params: Record<string, ParamValue>;
  current_default: boolean;
  stored: boolean;
}

export interface DataSourceOption {
  kind: "synthetic" | "csv" | "zerodha";
  label: string;
  available: boolean;
  synthetic: boolean;
  first_day: string | null;
  last_day: string | null;
  symbols: string[];
  note: string;
}

export interface RiskSpec {
  risk_per_trade_percent: number;
  daily_loss_limit_percent: number;
  max_trades_per_day: number;
  max_consecutive_losses: number;
  max_position_value: number;
  max_exposure: number;
  margin_multiplier: number;
  allow_short: boolean;
}

export interface CostSpec {
  slippage_bps: number;
  brokerage_rate: number;
  brokerage_cap_per_order: number;
  stt_sell_rate: number;
  exchange_rate: number;
  sebi_per_crore: number;
  stamp_buy_rate: number;
  gst_rate: number;
}

export interface ValidationSpec {
  oos_fraction: number;
  wf_train_days: number;
  wf_test_days: number;
  wf_anchored: boolean;
  min_trades_for_selection: number;
  null_control: boolean;
}

export interface BacktestOptions {
  strategies: StrategyOption[];
  versions: VersionOption[];
  data_sources: DataSourceOption[];
  timeframes: string[];
  modes: BacktestMode[];
  limits: { max_symbols: number; max_days: number; max_grid_combinations: number };
  risk: RiskSpec;
  costs: CostSpec;
  validation: ValidationSpec;
  assumptions: string[];
  note: string;
}

export interface BacktestRequest {
  name?: string | null;
  mode: BacktestMode;
  strategy: StrategyChoice;
  version?: string | null;
  symbols: string[];
  start_date: string;
  end_date: string;
  timeframe: string;
  parameters: Record<string, ParamValue>;
  grid?: Record<string, ParamValue[]> | null;
  capital: number;
  risk: RiskSpec;
  costs: CostSpec;
  validation: ValidationSpec;
  data_source: { kind: "synthetic" | "csv" | "zerodha"; seed: number };
  warmup_days?: number;
}

export interface BacktestMetrics {
  total_trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  profit_factor: number | null;
  expectancy: number | null;
  expectancy_r: number | null;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  charges: number;
  return_percent: number;
  max_drawdown: number;
  max_drawdown_percent: number;
  average_win: number | null;
  average_loss: number | null;
  payoff_ratio: number | null;
  average_planned_rr: number | null;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  sharpe: number | null;
  sortino: number | null;
  sharpe_note: string;
  trading_days: number;
  trades_per_day: number | null;
  average_holding_minutes: number | null;
  exit_reasons: Record<string, number>;
  starting_capital: number;
  ending_equity: number;
}

export interface Distribution {
  p5: number;
  p50: number;
  p95: number;
}

export interface RobustnessReport {
  trades: number;
  enough_trades: boolean;
  mean_r: number | null;
  r_std: number | null;
  r_ci95: [number, number] | null;
  t_stat: number | null;
  mc_net_pnl: Distribution | null;
  mc_max_drawdown: Distribution | null;
  mc_probability_of_loss: number | null;
  net_pnl_double_costs: number | null;
  net_pnl_extra_slippage: number | null;
  extra_slippage_bps: number;
  top3_share_of_profit: number | null;
  net_without_best_trade: number | null;
  by_regime: GroupStats[];
  by_strategy: GroupStats[];
  by_exit_reason: GroupStats[];
  flags: string[];
}

export interface Segment {
  label: string;
  kind: "FULL" | "IN_SAMPLE" | "OUT_OF_SAMPLE" | "TRAIN" | "TEST";
  start: string;
  end: string;
  days: number;
  params_hash: string;
  params: Record<string, ParamValue>;
  objective: number | null;
  metrics: BacktestMetrics;
}

export interface GridResult {
  params_hash: string;
  params: Record<string, ParamValue>;
  varied: Record<string, ParamValue>;
  objective: number | null;
  smoothed: number | null;
  trades: number;
  net_pnl: number;
  profit_factor: number | null;
  expectancy_r: number | null;
  selected: boolean;
  window: string;
}

export interface WalkForwardSummary {
  windows: number;
  profitable_windows: number;
  efficiency: number | null;
  parameter_changes: number;
  note: string;
}

export interface StrategyVersionInfo {
  strategy: string;
  version_id: string;
  semver: string;
  config_hash: string;
  config: Record<string, unknown>;
}

/** Stored result (trades and equity are served by their own endpoints). */
export interface BacktestResult {
  mode: BacktestMode;
  primary_label: string;
  metrics: BacktestMetrics;
  robustness: RobustnessReport;
  segments: Segment[];
  grid: GridResult[];
  combinations_tested: number;
  selected_params: Record<string, ParamValue>;
  selected_params_hash: string;
  walk_forward: WalkForwardSummary | null;
  in_sample_metrics: BacktestMetrics | null;
  degradation: number | null;
  null_control: BacktestMetrics | null;
  strategy_versions: StrategyVersionInfo[];
  data: Record<string, unknown>;
  replay: Record<string, unknown>;
  execution: Record<string, unknown>;
  warnings: string[];
  assumptions: string[];
  note: string;
}

export interface RunHeadline {
  total_trades: number;
  win_rate: number | null;
  profit_factor: number | null;
  net_pnl: number;
  max_drawdown: number;
  expectancy_r: number | null;
}

export interface RunSummary {
  id: string;
  name: string | null;
  mode: BacktestMode;
  status: RunStatus;
  progress: number;
  message: string | null;
  error: string | null;
  strategy: StrategyChoice;
  timeframe: string;
  start_date: string;
  end_date: string;
  symbols: string[];
  capital: number;
  data_source: { kind: string; synthetic: boolean; fingerprint: string; seed: number | null };
  headline: RunHeadline | null;
  flags: number | null;
  created_at: string;
  finished_at: string | null;
  duration_ms: number | null;
  estimated_seconds?: number | null;
}

export interface RunDetail extends RunSummary {
  request: BacktestRequest;
  metrics: BacktestMetrics | null;
  robustness: RobustnessReport | null;
  result: BacktestResult | null;
  note: string;
}

export interface RunList {
  items: RunSummary[];
  total: number;
  queued: number;
  note: string;
}

export interface BacktestTrade {
  id: string;
  segment: string;
  symbol: string;
  side: "BUY" | "SELL";
  strategy: string | null;
  strategy_version: string | null;
  regime: string | null;
  score: number | null;
  decision_time: string | null;
  opened_at: string | null;
  closed_at: string | null;
  quantity: number;
  entry_price: number | null;
  exit_price: number | null;
  stop_loss: number;
  trailing_stop: number | null;
  target: number;
  exit_reason: string | null;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  r_multiple: number | null;
  planned_rr: number | null;
  reasons: string[];
}

export interface BacktestTradeList {
  items: BacktestTrade[];
  total: number;
}

export interface BacktestEquityPoint {
  at: string;
  equity: number;
  drawdown: number | null;
  drawdown_percent: number | null;
  net_pnl: number | null;
}

export interface BacktestEquity {
  trades: BacktestEquityPoint[];
  days: BacktestEquityPoint[];
}
