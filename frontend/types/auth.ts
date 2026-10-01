/**
 * Mirrors backend/app/schemas/auth.py and backend/app/schemas/settings.py.
 * Hand-maintained mirror, same rationale as types/system.ts.
 */

export type UserRole = "ADMIN" | "TRADER" | "VIEWER";

export interface UserPublic {
  id: string;
  email: string;
  full_name: string | null;
  role: UserRole;
  is_active: boolean;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: UserPublic;
}

export type TradingModeValue = "PAPER" | "APPROVAL" | "LIVE";

export interface TradingSettingsResponse {
  id: string;
  user_id: string;
  trading_mode: TradingModeValue;
  paper_capital: number;
  auto_trading_enabled: boolean;
  signal_score_threshold: number;
  trading_start_time: string;
  trading_end_time: string;
  emergency_stop: boolean;
}

export interface TradingSettingsUpdateRequest {
  paper_capital?: number;
  signal_score_threshold?: number;
  trading_start_time?: string;
  trading_end_time?: string;
}
