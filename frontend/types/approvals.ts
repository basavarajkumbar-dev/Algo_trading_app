/** Phase 12 approval requests (mirror backend/app/approvals/schemas.py). */
export type ApprovalStatus = "PENDING" | "APPROVED" | "REJECTED" | "EXPIRED" | "CANCELLED" | "EXECUTED" | "FAILED";

export interface ApprovalRequest {
  id: string;
  status: ApprovalStatus;
  source: "ALGO" | "MANUAL";
  signal_id: string | null;
  symbol: string;
  direction: "BUY" | "SELL";
  side: "LONG" | "SHORT";
  entry: number;
  stop_loss: number;
  target: number;
  quantity: number;
  risk_amount: number;
  risk_per_share: number;
  position_value: number;
  reward_risk: number;
  strategy: string | null;
  strategy_version: string | null;
  score: number | null;
  category: string | null;
  market_regime: string | null;
  regime_confidence: number | null;
  reasons: string[];
  risk_checks: { name: string; passed: boolean; detail: string; reason: string | null }[];
  signal_timestamp: string | null;
  created_at: string;
  expires_at: string;
  requested_by_email: string | null;
  approved_by_email: string | null;
  approved_at: string | null;
  rejected_by_email: string | null;
  rejected_at: string | null;
  expired_at: string | null;
  executed_at: string | null;
  reason: string | null;
  note: string | null;
  trade_id: string | null;
  execution_reasons: string[];
  seconds_remaining: number;
}

export interface ApprovalList {
  items: ApprovalRequest[];
  total: number;
  counts: Partial<Record<ApprovalStatus, number>>;
  mode: string;
  ttl_seconds: number;
  server_time: string;
  note: string;
}
