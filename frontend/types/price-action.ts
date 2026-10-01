/**
 * Mirrors backend/app/price_action/*. Hand-maintained, same rationale
 * as types/system.ts. Only the fields the UI reads are typed strictly.
 */

import type { MarketRegime, RelationshipValue, TrendValue } from "@/types/intelligence";
import type { CandleResponse, TimeframeValue } from "@/types/market";

export type PatternType = "COG" | "NR" | "NRB" | "WRB" | "BT" | "TT";
export type PatternDirection = "BULLISH" | "BEARISH" | "NEUTRAL";
export type LevelLocation =
  | "AT_SUPPORT"
  | "AT_RESISTANCE"
  | "THROUGH_LEVEL"
  | "BETWEEN_LEVELS"
  | "NO_LEVELS";

export type LevelType =
  | "PREVIOUS_DAY_HIGH"
  | "PREVIOUS_DAY_LOW"
  | "PREVIOUS_CLOSE"
  | "INTRADAY_HIGH"
  | "INTRADAY_LOW"
  | "SWING_HIGH"
  | "SWING_LOW"
  | "RANGE_HIGH"
  | "RANGE_LOW"
  | "BREAKOUT_LEVEL"
  | "BREAKDOWN_LEVEL"
  | "VWAP"
  | "EMA"
  | "MA"
  | "RETRACEMENT";

export type RetracementZone =
  | "SHALLOW"
  | "HEALTHY_PULLBACK"
  | "DEEP_PULLBACK"
  | "WEAKENING"
  | "MAJOR_WEAKNESS"
  | "INVALIDATION_CONTEXT";

export type RetracementQuality =
  | "STRONG"
  | "HEALTHY"
  | "ACCEPTABLE"
  | "WEAK"
  | "VERY_WEAK"
  | "INVALIDATED";

export type SetupState =
  | "WITH_TREND"
  | "PULLBACK_IN_TREND"
  | "WEAKENING_PULLBACK"
  | "IMPULSE_INVALIDATED"
  | "COUNTER_TREND"
  | "NO_DIRECTIONAL_CONTEXT"
  | "UNCLEAR";

export interface PatternContext {
  prior_trend: TrendValue;
  prior_move_atr: number | null;
  atr: number | null;
  range_vs_average: number | null;
  relative_volume: number | null;
  location: LevelLocation;
  nearest_level: { label: string; price: number; distance_atr: number } | null;
  levels_considered: boolean;
}

export interface PatternResult {
  pattern: PatternType;
  name: string;
  detected: boolean;
  direction: PatternDirection;
  confidence: number;
  strength: number;
  context: PatternContext;
  reasons: string[];
  metrics: Record<string, number>;
  candle_index: number;
  timestamp: string;
}

export interface PatternMarker {
  pattern: PatternType;
  direction: PatternDirection;
  confidence: number;
  strength: number;
  timestamp: string;
  price: number;
}

export interface Pivot {
  type: "HIGH" | "LOW";
  index: number;
  price: number;
  timestamp: string;
  confirmed: boolean;
}

export interface SwingLeg {
  direction: TrendValue;
  start: Pivot;
  end: Pivot;
  size: number;
  size_atr: number;
  candles: number;
  complete: boolean;
}

export interface SwingStructureResult {
  atr: number;
  reversal_threshold: number;
  pivots: Pivot[];
  legs: SwingLeg[];
  latest_swing_high: Pivot | null;
  latest_swing_low: Pivot | null;
  current_leg: SwingLeg | null;
  reasons: string[];
}

export interface RetracementLevel {
  percent: number;
  price: number;
}

export interface RetracementResult {
  detected: boolean;
  direction: TrendValue | null;
  swing_high: number | null;
  swing_low: number | null;
  swing_high_time: string | null;
  swing_low_time: string | null;
  impulse_start: number | null;
  impulse_end: number | null;
  impulse_size: number | null;
  impulse_size_atr: number | null;
  impulse_candles: number | null;
  retracement_percentage: number | null;
  max_retracement_percentage: number | null;
  zone: RetracementZone | null;
  max_zone: RetracementZone | null;
  quality: RetracementQuality | null;
  levels: RetracementLevel[];
  strength: number;
  confidence: number;
  candles_since_extreme: number | null;
  reasons: string[];
  note: string;
}

export interface PriceLevel {
  label: string;
  type: LevelType;
  price: number;
  role: "SUPPORT" | "RESISTANCE";
  distance_percent: number;
  distance_atr: number;
  strength: number;
  touch_count: number;
  timeframe: string;
  recency: number | null;
  confluence: string[];
  components: Record<string, number>;
  dynamic: boolean;
}

export interface SupportResistanceResult {
  price: number;
  atr: number;
  timeframe: string;
  session_date: string | null;
  previous_session_date: string | null;
  levels: PriceLevel[];
  nearest_support: PriceLevel | null;
  nearest_resistance: PriceLevel | null;
  strongest_support: PriceLevel | null;
  strongest_resistance: PriceLevel | null;
  unavailable: Record<string, string>;
  reasons: string[];
  note: string;
}

export interface EntryConfirmation {
  reference_bias: PatternDirection;
  aligned_patterns: string[];
  opposing_patterns: string[];
  candle_confirmation: boolean;
  momentum_returning: boolean;
  volume_confirmed: boolean;
  confirmations: number;
}

export interface TimeframeAnalysis {
  role: "CONTEXT" | "SETUP" | "ENTRY";
  timeframe: TimeframeValue;
  available: boolean;
  unavailable_reason: string | null;
  candles_analyzed: number;
  data_as_of: string | null;
  price: number | null;
  bias: number;
  direction: TrendValue;
  bias_components: Record<string, number>;
  regime: MarketRegime | null;
  regime_confidence: number | null;
  trend_quality: number | null;
  momentum_score: number | null;
  structure_trend: TrendValue | null;
  structure_state: string | null;
  last_structure_event: string | null;
  price_vs_vwap: RelationshipValue | null;
  ma_alignment: string | null;
  key_levels: PriceLevel[];
  retracement: RetracementResult | null;
  setup_state: SetupState | null;
  nearest_support: PriceLevel | null;
  nearest_resistance: PriceLevel | null;
  patterns: PatternResult[];
  confirmation: EntryConfirmation | null;
  relative_volume: number | null;
  reasons: string[];
}

export interface MultiTimeframeResult {
  symbol: string;
  overall_bias: PatternDirection;
  bias_score: number;
  alignment_score: number;
  context_analysis: TimeframeAnalysis;
  setup_analysis: TimeframeAnalysis;
  entry_analysis: TimeframeAnalysis;
  conflicts: string[];
  reasons: string[];
  timestamp: string;
  note: string;
}

export interface PriceActionAnalysis {
  symbol: string;
  timeframe: TimeframeValue;
  candles_analyzed: number;
  price: number | null;
  data_as_of: string | null;
  patterns: PatternResult[];
  recent_patterns: PatternMarker[];
  swing_structure: SwingStructureResult | null;
  retracement: RetracementResult | null;
  support_resistance: SupportResistanceResult | null;
  multi_timeframe: MultiTimeframeResult | null;
  unavailable: Record<string, string>;
  timestamp: string;
  note: string;
}

export interface SymbolPriceAction {
  symbol: string;
  name: string | null;
  analysis: PriceActionAnalysis;
  candles: CandleResponse[];
}

export interface LevelSummary {
  label: string;
  price: number;
  strength: number;
  distance_atr: number;
}

export interface PriceActionSummary {
  symbol: string;
  name: string | null;
  price: number | null;
  data_as_of: string | null;
  patterns: { pattern: PatternType; direction: PatternDirection; confidence: number; strength: number }[];
  recent_pattern_count: number;
  retracement_direction: TrendValue | null;
  retracement_percentage: number | null;
  retracement_zone: RetracementZone | null;
  nearest_support: LevelSummary | null;
  nearest_resistance: LevelSummary | null;
  overall_bias: PatternDirection | null;
  bias_score: number | null;
  alignment_score: number | null;
  setup_state: SetupState | null;
  entry_confirmations: number | null;
}

export interface PriceActionServiceStatus {
  is_running: boolean;
  context_timeframe: TimeframeValue;
  setup_timeframe: TimeframeValue;
  entry_timeframe: TimeframeValue;
  refresh_seconds: number;
  refresh_count: number;
  last_refresh_at: string | null;
  last_compute_ms: number | null;
  last_error: string | null;
  symbols_analyzed: number;
}

export interface PriceActionOverview {
  symbols: PriceActionSummary[];
  status: PriceActionServiceStatus;
  note: string;
}
