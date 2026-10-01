/**
 * Mirrors backend/app/market_intelligence/* and backend/app/regime/schemas.py.
 * Hand-maintained mirror, same rationale as types/system.ts.
 */

import type { TimeframeValue } from "@/types/market";

export type MarketRegime =
  | "STRONG_BULLISH"
  | "STRONG_BEARISH"
  | "RANGE"
  | "BREAKOUT"
  | "HIGH_VOLATILITY"
  | "UNKNOWN";

export type TrendValue = "UP" | "DOWN" | "SIDEWAYS";
export type RelationshipValue = "ABOVE" | "BELOW" | "AT";
export type VolatilityLevel = "LOW" | "NORMAL" | "HIGH" | "EXTREME";
export type VolumeTrendValue = "INCREASING" | "DECREASING" | "STABLE";
export type FactorScale = "directional" | "intensity";

export type FeatureValue = number | string | boolean | null;

export interface RegimeResult {
  symbol: string;
  timeframe: TimeframeValue;
  regime: MarketRegime;
  confidence: number;
  reasons: string[];
  counter_reasons: string[];
  features: Record<string, FeatureValue>;
  scores: Partial<Record<MarketRegime, number>>;
  candles_analyzed: number;
  data_as_of: string | null;
  timestamp: string;
  note: string;
}

export interface FactorScore {
  key: string;
  label: string;
  score: number | null;
  scale: FactorScale;
  interpretation: string;
  detail: string | null;
}

export interface MarketBreadth {
  symbols_counted: number;
  advancing: number;
  declining: number;
  unchanged: number;
  above_vwap: number;
  below_vwap: number;
  percent_above_vwap: number | null;
  advance_decline_ratio: number | null;
  bullish_regimes: number;
  bearish_regimes: number;
}

export interface MarketIntelligenceSnapshot {
  benchmark_symbol: string;
  timeframe: TimeframeValue;
  market_direction: TrendValue;
  market_regime: MarketRegime;
  confidence: number;
  volatility: VolatilityLevel | null;
  trend_strength: number | null;
  momentum: number | null;
  breadth: MarketBreadth;
  regime_distribution: Record<MarketRegime, number>;
  factors: FactorScore[];
  regime: RegimeResult;
  timestamp: string;
  note: string;
}

export interface SymbolRegimeSummary {
  symbol: string;
  name: string | null;
  price: number | null;
  change_percent: number | null;
  regime: MarketRegime;
  confidence: number;
  top_reason: string | null;
  trend_quality_score: number | null;
  trend_direction: TrendValue | null;
  momentum_score: number | null;
  relative_strength_score: number | null;
  relative_strength_rank: number | null;
  price_vs_vwap: RelationshipValue | null;
  volatility_level: VolatilityLevel | null;
  relative_volume: number | null;
  data_as_of: string | null;
}

export interface IntelligenceServiceStatus {
  is_running: boolean;
  timeframe: TimeframeValue;
  refresh_seconds: number;
  refresh_count: number;
  last_refresh_at: string | null;
  last_compute_ms: number | null;
  last_error: string | null;
  symbols_analyzed: number;
}

export interface MarketIntelligenceResponse {
  snapshot: MarketIntelligenceSnapshot;
  symbols: SymbolRegimeSummary[];
  status?: IntelligenceServiceStatus;
}

// --- Engine outputs (only the fields the dashboard displays) -------------------

export interface VWAPResult {
  vwap: number;
  vwap_slope_percent: number | null;
  price_vs_vwap: RelationshipValue;
  distance_from_vwap_percent: number;
  recent_crossings: number;
  anchor_candles: number;
}

export interface MovingAverageResult {
  ema_fast_period: number;
  ema_slow_period: number;
  ma_period: number;
  ema_fast: number | null;
  ema_slow: number | null;
  ma: number | null;
  price_vs_ema_fast: RelationshipValue | null;
  price_vs_ema_slow: RelationshipValue | null;
  price_vs_ma: RelationshipValue | null;
  distance_from_ema_fast_percent: number | null;
  ema_fast_slope_percent: number | null;
  separation_percent: number | null;
  separation_state: "CONVERGING" | "DIVERGING" | "STABLE" | null;
  alignment: "BULLISH" | "BEARISH" | "MIXED";
  trend: TrendValue;
}

export interface VolatilityResult {
  atr: number;
  atr_percent: number;
  atr_ratio: number;
  volatility_level: VolatilityLevel;
  is_expanding: boolean;
  average_wick_ratio: number;
  direction_changes: number;
  character_window: number;
}

export interface MomentumResult {
  momentum_score: number;
  direction: TrendValue;
  rate_of_change_percent: Record<string, number>;
}

export interface VolumeResult {
  relative_volume: number;
  relative_volume_score: number;
  is_spike: boolean;
  volume_trend: VolumeTrendValue;
}

export interface SwingPoint {
  type: "HIGH" | "LOW";
  index: number;
  price: number;
  timestamp: string;
  label: "HIGHER_HIGH" | "LOWER_HIGH" | "HIGHER_LOW" | "LOWER_LOW" | null;
}

export interface StructureResult {
  trend: TrendValue;
  structure_state: string;
  last_event: "BREAK_OF_STRUCTURE" | "STRUCTURE_SHIFT" | "NONE";
  confidence: number;
  latest_swing_high: number | null;
  latest_swing_low: number | null;
  swings: SwingPoint[];
}

export interface RangeResult {
  window: number;
  range_high: number;
  range_low: number;
  compression_ratio: number;
  is_compressed: boolean;
  breakout_direction: TrendValue | null;
  efficiency_ratio: number;
}

export interface RelativeStrengthResult {
  lookback: number;
  symbol_return_percent: number;
  benchmark_return_percent: number;
  relative_return_percent: number;
  relative_strength_score: number;
}

export interface TrendQualityResult {
  direction: TrendValue;
  score: number;
  components: Record<string, number>;
}

export interface SymbolAnalysis {
  symbol: string;
  timeframe: TimeframeValue;
  candles_analyzed: number;
  price: number | null;
  data_as_of: string | null;
  vwap: VWAPResult | null;
  moving_averages: MovingAverageResult | null;
  volatility: VolatilityResult | null;
  momentum: MomentumResult | null;
  volume: VolumeResult | null;
  structure: StructureResult | null;
  range: RangeResult | null;
  relative_strength: RelativeStrengthResult | null;
  trend_quality: TrendQualityResult | null;
  unavailable: Record<string, string>;
}

export interface SymbolIntelligence {
  symbol: string;
  name: string | null;
  price: number | null;
  change_percent: number | null;
  regime: RegimeResult;
  factors: FactorScore[];
  relative_strength_rank: number | null;
  analysis: SymbolAnalysis;
}

// --- WebSocket messages ---------------------------------------------------------

export interface WsMarketIntelligenceMessage extends Omit<MarketIntelligenceResponse, "status"> {
  type: "market_intelligence";
}

export interface WsMarketRegimeMessage {
  type: "market_regime";
  symbol: string;
  regime: MarketRegime;
  previous_regime: MarketRegime | null;
  changed: boolean;
  confidence: number;
  reasons: string[];
  timestamp: string;
}
