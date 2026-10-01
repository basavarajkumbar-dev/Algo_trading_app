import { apiGet, apiPatch, apiPost } from "@/lib/api";
import type {
  TokenResponse,
  TradingSettingsResponse,
  TradingSettingsUpdateRequest,
  UserPublic,
} from "@/types/auth";

export function registerRequest(
  email: string,
  password: string,
  fullName?: string
): Promise<TokenResponse> {
  return apiPost<TokenResponse>("/auth/register", {
    email,
    password,
    full_name: fullName || undefined,
  });
}

export function loginRequest(email: string, password: string): Promise<TokenResponse> {
  return apiPost<TokenResponse>("/auth/login", { email, password });
}

export function refreshRequest(refreshToken: string): Promise<TokenResponse> {
  return apiPost<TokenResponse>("/auth/refresh", { refresh_token: refreshToken });
}

export function logoutRequest(refreshToken: string): Promise<void> {
  return apiPost<void>("/auth/logout", { refresh_token: refreshToken });
}

export function meRequest(accessToken: string): Promise<UserPublic> {
  return apiGet<UserPublic>("/auth/me", { accessToken });
}

export function getMySettingsRequest(accessToken: string): Promise<TradingSettingsResponse> {
  return apiGet<TradingSettingsResponse>("/settings/me", { accessToken });
}

export function updateMySettingsRequest(
  accessToken: string,
  update: TradingSettingsUpdateRequest
): Promise<TradingSettingsResponse> {
  return apiPatch<TradingSettingsResponse>("/settings/me", update, { accessToken });
}
