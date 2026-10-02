// Mirrors backend app/analytics/performance.py, app/services/component_health.py
// and the /system/events and trading_event WebSocket payloads (Phase 9).

export type HealthLevel = "HEALTHY" | "WARNING" | "CRITICAL";

export interface ComponentReport {
  key:
    | "market_data"
    | "backend"
    | "database"
    | "redis"
    | "websocket"
    | "strategy_engine"
    | "risk_engine"
    | "paper_broker";
  name: string;
  status: HealthLevel;
  detail: string;
  metrics: Record<string, unknown>;
}

export interface ComponentHealthResponse {
  status: HealthLevel;
  components: ComponentReport[];
  demo_mode: boolean;
  trading_mode: string;
  checked_at: string;
  note: string;
}

export interface SystemEventItem {
  id: string;
  event_type: string;
  severity: "INFO" | "WARNING" | "CRITICAL" | string;
  message: string;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface SystemEventList {
  items: SystemEventItem[];
  total: number;
}

export interface EquityPoint {
  trade_id: string;
  symbol: string;
  strategy: string | null;
  closed_at: string;
  net_pnl: number;
  r_multiple: number | null;
  cumulative_pnl: number;
  equity: number;
  drawdown: number;
  drawdown_percent: number;
}

export interface DailyPnl {
  date: string;
  net_pnl: number;
  trades: number;
  wins: number;
  losses: number;
}

export interface GroupStats {
  key: string;
  trades: number;
  wins: number;
  losses: number;
  win_rate: number | null;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  average_r: number | null;
}

export interface PerformanceSummary {
  closed_trades: number;
  wins: number;
  losses: number;
  breakeven: number;
  win_rate: number | null;
  net_pnl: number;
  gross_profit: number;
  gross_loss: number;
  profit_factor: number | null;
  charges: number;
  average_win: number | null;
  average_loss: number | null;
  expectancy: number | null;
  average_r: number | null;
  best_trade: number | null;
  worst_trade: number | null;
  max_drawdown: number;
  max_drawdown_percent: number;
  longest_win_streak: number;
  longest_loss_streak: number;
  starting_capital: number;
  ending_equity: number;
  return_percent: number;
}

export interface PerformanceReport {
  summary: PerformanceSummary;
  equity_curve: EquityPoint[];
  daily: DailyPnl[];
  by_strategy: GroupStats[];
  by_exit_reason: GroupStats[];
  other_trades: Record<string, number>;
  note: string;
}

export interface WsTradingEventMessage {
  type: "trading_event";
  event_type: string;
  severity: string;
  message: string;
  at: string;
  metadata: Record<string, unknown>;
}
