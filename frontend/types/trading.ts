// Mirrors backend app/trading, app/risk and app/execution models (Phase 8).

export type TradingStateValue =
  | "IDLE"
  | "SCANNING"
  | "SIGNAL_FOUND"
  | "RISK_CHECK"
  | "ORDER_PENDING"
  | "POSITION_ACTIVE"
  | "EXIT"
  | "CLOSED";

export type TradeSide = "LONG" | "SHORT";
export type TradeStatus =
  | "PENDING"
  | "OPEN"
  | "EXITING"
  | "CLOSED"
  | "CANCELLED"
  | "REJECTED"
  | "INTERRUPTED";
export type ExitReason =
  | "STOP_LOSS"
  | "TARGET"
  | "TIME_EXIT"
  | "MANUAL"
  | "EMERGENCY"
  | "ENTRY_EXPIRED"
  | "ENTRY_REJECTED";

export type RejectionReason =
  | "TRADING_MODE_NOT_PAPER"
  | "EMERGENCY_STOP"
  | "SYSTEM_UNHEALTHY"
  | "MARKET_CLOSED"
  | "DATA_STALE"
  | "ACTIVE_POSITION_EXISTS"
  | "DUPLICATE_ORDER"
  | "DAILY_LOSS_LIMIT"
  | "MAX_TRADES_REACHED"
  | "MAX_CONSECUTIVE_LOSSES"
  | "SIGNAL_NOT_EXECUTABLE"
  | "SHORT_NOT_ALLOWED"
  | "INVALID_STOP"
  | "INVALID_TARGET"
  | "PRICE_MOVED_AWAY"
  | "INVALID_QUANTITY"
  | "INSUFFICIENT_CAPITAL"
  | "MAX_EXPOSURE";

export interface ChargeBreakdown {
  brokerage: number;
  stt: number;
  exchange: number;
  sebi: number;
  stamp_duty: number;
  gst: number;
}

export interface Fill {
  order_id: string;
  quantity: number;
  price: number;
  market_price: number;
  slippage: number;
  charges: ChargeBreakdown;
  timestamp: string;
}

export interface PaperOrder {
  id: string;
  client_order_id: string | null;
  tag: string | null;
  symbol: string;
  side: "BUY" | "SELL";
  order_type: "MARKET" | "LIMIT" | "SL_M";
  quantity: number;
  price: number | null;
  trigger_price: number | null;
  status: "OPEN" | "TRIGGER_PENDING" | "PARTIALLY_FILLED" | "FILLED" | "CANCELLED" | "REJECTED";
  filled_quantity: number;
  average_price: number | null;
  triggered: boolean;
  fills: Fill[];
  charges: ChargeBreakdown;
  rejection_reason: string | null;
  placed_at: string;
  updated_at: string;
}

export interface Position {
  symbol: string;
  quantity: number;
  average_price: number;
  last_price: number | null;
  realized_pnl: number;
  unrealized_pnl: number;
  charges: number;
}

export interface Balance {
  starting_capital: number;
  cash: number;
  used_margin: number;
  available: number;
  realized_pnl: number;
  unrealized_pnl: number;
  charges_paid: number;
  equity: number;
}

export interface RiskCheck {
  name: string;
  passed: boolean;
  detail: string;
  reason: RejectionReason | null;
}

export interface PositionSize {
  quantity: number;
  raw_quantity: number;
  risk_amount: number;
  risk_per_unit: number;
  actual_risk: number;
  position_value: number;
  entry: number;
  stop_loss: number;
  constraints: string[];
}

export interface TradeIntent {
  signal_id: string | null;
  symbol: string;
  side: TradeSide;
  entry: number;
  stop_loss: number;
  target: number;
  score: number | null;
  strategy: string | null;
}

export interface RiskDecision {
  approved: boolean;
  intent: TradeIntent;
  size: PositionSize | null;
  checks: RiskCheck[];
  rejection_reasons: RejectionReason[];
  evaluated_at: string;
  note: string;
}

export interface RiskState {
  trading_day: string;
  day_start_capital: number;
  trades_today: number;
  realized_pnl_today: number;
  consecutive_losses: number;
  wins_today: number;
  losses_today: number;
}

export interface RiskSnapshot {
  state: RiskState;
  capital: number;
  available_capital: number;
  open_exposure: number;
  daily_loss_limit_amount: number;
  daily_loss_remaining: number;
  risk_per_trade_amount: number;
  trades_remaining: number;
  consecutive_losses_remaining: number;
  entries_blocked: boolean;
  blocked_reasons: RejectionReason[];
  note: string;
}

export interface RiskLimits {
  risk_per_trade_percent: number;
  lot_size: number;
  tick_size: number;
  max_order_quantity: number;
  max_position_value: number;
  margin_multiplier: number;
  daily_loss_limit_percent: number;
  max_trades_per_day: number;
  max_consecutive_losses: number;
  max_exposure: number;
  min_signal_score: number;
  max_data_age_seconds: number;
  max_signal_age_seconds: number;
  max_entry_deviation_r: number;
  allow_short: boolean;
  session: {
    mode: "EXCHANGE" | "SIMULATED_24H";
    open_time: string;
    close_time: string;
    last_entry_time: string;
    square_off_time: string;
    holidays: string[];
  };
}

export interface PaperTrade {
  id: string;
  signal_id: string | null;
  symbol: string;
  side: TradeSide;
  strategy: string | null;
  score: number | null;
  status: TradeStatus;
  planned_entry: number;
  stop_loss: number;
  target: number;
  quantity: number;
  filled_quantity: number;
  open_quantity: number;
  entry_price: number | null;
  exit_price: number | null;
  last_price: number | null;
  initial_risk: number;
  gross_pnl: number;
  charges: number;
  net_pnl: number;
  unrealized_pnl: number;
  r_multiple: number | null;
  exit_reason: ExitReason | null;
  created_at: string;
  opened_at: string | null;
  closed_at: string | null;
  order_ids: Record<string, string[]>;
  risk_summary: string;
  risk_checks: RiskCheck[];
  note: string;
}

export interface StateChange {
  from_state: TradingStateValue;
  to_state: TradingStateValue;
  at: string;
  reason: string;
}

export interface PaperBrokerConfig {
  slippage_bps: number;
  execution_delay_seconds: number;
  max_fill_fraction: number;
  lot_size: number;
  tick_size: number;
  margin_multiplier: number;
  costs: Record<string, number>;
}

export interface TradingStatus {
  mode: "PAPER";
  state: TradingStateValue;
  state_since: string;
  auto_trading: boolean;
  emergency_stop: boolean;
  emergency_reason: string | null;
  active_trade: PaperTrade | null;
  last_decision: RiskDecision | null;
  balance: Balance;
  positions: Position[];
  working_orders: PaperOrder[];
  risk: RiskSnapshot;
  system_healthy: boolean;
  health_detail: string;
  persistence_ok: boolean;
  persistence_error: string | null;
  restored_trades_today: number;
  interrupted_trades: number;
  broker: PaperBrokerConfig;
  entry_timeout_seconds: number;
  recent_transitions: StateChange[];
  is_running: boolean;
  market_open: boolean;
  entries_allowed: boolean;
  market_detail: string;
  session_mode: "EXCHANGE" | "SIMULATED_24H";
  note: string;
}

export interface RiskResponse {
  limits: RiskLimits;
  snapshot: RiskSnapshot;
  last_decision: RiskDecision | null;
  note: string;
}

export interface TradeList {
  items: PaperTrade[];
  total: number;
  limit: number;
  offset: number;
  note: string;
}

export interface StoredOrder {
  order: PaperOrder;
  trade_id: string | null;
  purpose: string | null;
}

export interface OrderList {
  items: StoredOrder[];
  total: number;
  limit: number;
  offset: number;
  note: string;
}

export interface ExecuteResponse {
  decision: RiskDecision;
  status: TradingStatus;
}
