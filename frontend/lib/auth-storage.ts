/**
 * Token storage strategy for this phase.
 *
 * Access and refresh tokens live in localStorage. A separate,
 * non-sensitive cookie (`gainzalgo_authed=1`, no token value) is set
 * alongside them purely so `middleware.ts` — which runs before any
 * client JS and cannot read localStorage — can redirect unauthenticated
 * requests to /login without a client-side flash.
 *
 * This is a deliberate, documented simplification for Phase 3: storing
 * bearer tokens in localStorage is readable by any script on the page
 * (XSS risk), which httpOnly, server-set cookies would avoid. Moving to
 * httpOnly cookies issued directly by the backend is a reasonable
 * hardening item for a later phase (see docs/development-roadmap.md
 * Phase 17), not a Phase 3 blocker — there is no third-party script
 * surface in this app yet.
 */

const ACCESS_TOKEN_KEY = "gainzalgo_access_token";
const REFRESH_TOKEN_KEY = "gainzalgo_refresh_token";
const AUTH_COOKIE_NAME = "gainzalgo_authed";
const AUTH_COOKIE_MAX_AGE_SECONDS = 60 * 60 * 24 * 7; // mirrors backend refresh token lifetime

function isBrowser(): boolean {
  return typeof window !== "undefined";
}

export function getStoredTokens(): { accessToken: string | null; refreshToken: string | null } {
  if (!isBrowser()) return { accessToken: null, refreshToken: null };
  return {
    accessToken: window.localStorage.getItem(ACCESS_TOKEN_KEY),
    refreshToken: window.localStorage.getItem(REFRESH_TOKEN_KEY),
  };
}

export function storeTokens(accessToken: string, refreshToken: string): void {
  if (!isBrowser()) return;
  window.localStorage.setItem(ACCESS_TOKEN_KEY, accessToken);
  window.localStorage.setItem(REFRESH_TOKEN_KEY, refreshToken);
  document.cookie = `${AUTH_COOKIE_NAME}=1; path=/; max-age=${AUTH_COOKIE_MAX_AGE_SECONDS}; samesite=lax`;
}

export function clearTokens(): void {
  if (!isBrowser()) return;
  window.localStorage.removeItem(ACCESS_TOKEN_KEY);
  window.localStorage.removeItem(REFRESH_TOKEN_KEY);
  document.cookie = `${AUTH_COOKIE_NAME}=; path=/; max-age=0; samesite=lax`;
}
