/**
 * Mirrors backend/app/strategies/*. Hand-maintained, same rationale as
 * types/system.ts. Candidates are hypotheses, never orders.
 */

import type { MarketRegime } from "@/types/intelligence";
import type { TimeframeValue } from "@/types/market";

export type StrategyName = "TREND_CONTINUATION" | "BREAKOUT_MOMENTUM" | "MEAN_REVERSION";
export type TradeDirection = "LONG" | "SHORT" | "NONE";
export type FeatureValue = number | string | boolean | null;

export interface StrategyCheck {
  key: string;
  label: string;
  passed: boolean;
  required: boolean;
  weight: number;
  detail: string;
}

export interface StrategyResult {
  strategy: StrategyName;
  symbol: string;
  timeframe: TimeframeValue;
  eligible: boolean;
  direction: TradeDirection;
  entry_candidate: number | null;
  stop_candidate: number | null;
  target_candidate: number | null;
  risk_per_unit: number | null;
  reward_per_unit: number | null;
  reward_risk: number | null;
  entry_basis: string | null;
  stop_basis: string | null;
  target_basis: string | null;
  confidence: number;
  setup_score: number;
  stage: string | null;
  checks: StrategyCheck[];
  reasons: string[];
  blockers: string[];
  features: Record<string, FeatureValue>;
  data_as_of: string | null;
  timestamp: string;
  note: string;
}

export interface RoutingEntry {
  strategy: StrategyName;
  routed: boolean;
  reason: string;
  allowed_directions: TradeDirection[];
}

export interface RouterDecision {
  regime: MarketRegime;
  regime_confidence: number;
  eligible_strategies: StrategyName[];
  entries: RoutingEntry[];
  reasons: string[];
}

export interface SymbolStrategyEvaluation {
  symbol: string;
  decision: RouterDecision;
  results: StrategyResult[];
  candidates: StrategyResult[];
  data_as_of: string | null;
  timestamp: string;
  note: string;
}

export interface SymbolStrategies {
  symbol: string;
  name: string | null;
  evaluation: SymbolStrategyEvaluation;
  unrouted: StrategyResult[] | null;
}

export interface CandidateSummary {
  strategy: StrategyName;
  direction: TradeDirection;
  entry_candidate: number | null;
  stop_candidate: number | null;
  target_candidate: number | null;
  reward_risk: number | null;
  confidence: number;
}

export interface StrategySummary {
  symbol: string;
  name: string | null;
  price: number | null;
  regime: MarketRegime;
  regime_confidence: number;
  eligible_strategies: StrategyName[];
  candidates: CandidateSummary[];
  stages: Record<string, string | null>;
  data_as_of: string | null;
}

export interface StrategyServiceStatus {
  is_running: boolean;
  refresh_count: number;
  last_refresh_at: string | null;
  last_compute_ms: number | null;
  last_error: string | null;
  symbols_evaluated: number;
  candidates: number;
  price_action_refresh: number;
}

export interface StrategyOverview {
  symbols: StrategySummary[];
  status: StrategyServiceStatus;
  note: string;
}

export interface RouteRule {
  strategy: StrategyName;
  directions: TradeDirection[];
}

export interface StrategyEngineConfig {
  router: { routes: Record<MarketRegime, RouteRule[]>; min_regime_confidence: number };
  breakout_momentum: { opening_range_minutes: number; [key: string]: unknown };
  [key: string]: unknown;
}
