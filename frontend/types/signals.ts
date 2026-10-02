/**
 * Mirrors backend/app/signals/* and backend/app/scanner/*. Hand-maintained.
 * Signals are scored candidates, never orders.
 */

import type { MarketRegime } from "@/types/intelligence";
import type { TimeframeValue } from "@/types/market";
import type { FeatureValue, StrategyCheck, StrategyName } from "@/types/strategies";

export type SignalDirection = "BUY" | "SELL" | "NONE";
export type SignalCategory = "ELITE" | "STRONG" | "WATCH" | "IGNORE";
export type SignalStatus = "ACTIVE" | "EXPIRED";
export type CandidateStatus = "READY" | "WATCH" | "IGNORE";
export type ScanStatus = "READY" | "WATCH" | "IGNORE" | "NO_SETUP" | "NO_ROUTE" | "NO_DATA";

export interface ScoreComponent {
  key: string;
  label: string;
  weight: number;
  applicable: boolean;
  effective_weight: number;
  score: number | null;
  points: number;
  passed: boolean;
  detail: string;
}

export interface ScoreBreakdown {
  total: number;
  category: SignalCategory;
  executable: boolean;
  executable_threshold: number;
  components: ScoreComponent[];
}

export interface TradingSignal {
  id: string | null;
  symbol: string;
  direction: SignalDirection;
  strategy: StrategyName;
  strategy_version: string;
  timeframe: TimeframeValue;
  entry: number;
  stop_loss: number;
  target: number;
  risk_reward: number;
  quantity_candidate: number;
  quantity_note: string;
  score: number;
  category: SignalCategory;
  executable: boolean;
  confidence: number;
  relative_strength: number | null;
  trend_quality: number | null;
  relative_volume: number | null;
  liquidity: number | null;
  market_regime: MarketRegime;
  regime_confidence: number;
  breakdown: ScoreBreakdown;
  reasons: string[];
  strategy_reasons: string[];
  strategy_checks: StrategyCheck[];
  features: Record<string, FeatureValue>;
  candle_time: string;
  timestamp: string;
  status: SignalStatus;
  note: string;
}

export interface StoredSignal {
  signal: TradingSignal;
  generated_at: string;
  last_seen_at: string;
  expired_at: string | null;
  last_score: number;
  last_rank: number | null;
  times_seen: number;
}

export interface SignalList {
  items: StoredSignal[];
  total: number;
  limit: number;
  offset: number;
  note: string;
}

export interface TradeCandidate {
  rank: number;
  signal_id: string | null;
  symbol: string;
  direction: SignalDirection;
  strategy: StrategyName;
  score: number;
  category: SignalCategory;
  status: CandidateStatus;
  entry: number;
  stop_loss: number;
  target: number;
  risk_reward: number;
  quantity_candidate: number;
  relative_strength: number | null;
  liquidity: number | null;
  trend_quality: number | null;
  relative_volume: number | null;
  strategy_confidence: number;
  market_regime: MarketRegime;
}

export interface MarketContext {
  benchmark_symbol: string;
  available: boolean;
  regime: MarketRegime | null;
  regime_confidence: number | null;
  bias_score: number | null;
  direction: string | null;
  price: number | null;
  change_percent: number | null;
}

export interface LevelRef {
  label: string;
  price: number;
  strength: number;
}

export interface ScanRow {
  rank: number | null;
  symbol: string;
  name: string | null;
  price: number | null;
  change_percent: number | null;
  status: ScanStatus;
  signal_id: string | null;
  signal_direction: SignalDirection | null;
  signal_strategy: StrategyName | null;
  score: number | null;
  category: SignalCategory | null;
  regime: MarketRegime | null;
  regime_confidence: number | null;
  context_direction: string | null;
  context_bias: number | null;
  setup_direction: string | null;
  setup_bias: number | null;
  entry_direction: string | null;
  entry_bias: number | null;
  overall_bias: string | null;
  alignment_score: number | null;
  setup_state: string | null;
  structure_trend: string | null;
  structure_state: string | null;
  price_vs_vwap: string | null;
  trend_quality: number | null;
  trend_direction: string | null;
  retracement_direction: string | null;
  retracement_percentage: number | null;
  retracement_zone: string | null;
  nearest_support: LevelRef | null;
  nearest_resistance: LevelRef | null;
  patterns: { pattern: string; direction: string; confidence: number }[];
  relative_volume: number | null;
  momentum_score: number | null;
  relative_strength: number | null;
  relative_strength_rank: number | null;
  strategies: string[];
  strategy_stages: Record<string, string | null>;
  data_as_of: string | null;
}

export interface ScanResult {
  market_context: MarketContext;
  rows: ScanRow[];
  candidates: TradeCandidate[];
  signals: TradingSignal[];
  top_candidates: TradeCandidate[];
  status_counts: Record<ScanStatus, number>;
  symbols_scanned: number;
  filtered_out: Record<string, string>;
  scanned_at: string;
  note: string;
  ranking_note: string;
}

export interface SignalServiceStatus {
  is_running: boolean;
  scan_count: number;
  last_scan_at: string | null;
  last_compute_ms: number | null;
  last_error: string | null;
  persistence_ok: boolean;
  persistence_error: string | null;
  last_persisted: { created: number; updated: number; expired: number } | null;
  signals_in_scan: number;
  strategy_refresh: number;
  executable_threshold: number;
}

export interface ScannerResponse {
  scan: ScanResult;
  status: SignalServiceStatus;
}

export interface SignalEngineConfig {
  scoring: { weights: Record<string, number>; pass_threshold: number };
  categories: { elite: number; strong: number; watch: number; executable_threshold: number };
  quantity: { capital: number; risk_per_trade_percent: number; max_position_value: number };
  ranking: { top_n: number; min_category_for_top: string; liquidity_window: number };
  store_min_score: number;
}
