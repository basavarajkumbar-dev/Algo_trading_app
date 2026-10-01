"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import {
  loginRequest,
  logoutRequest,
  meRequest,
  refreshRequest,
  registerRequest,
} from "@/lib/auth-client";
import { clearTokens, getStoredTokens, storeTokens } from "@/lib/auth-storage";
import type { TokenResponse, UserPublic } from "@/types/auth";

type AuthStatus = "loading" | "authenticated" | "unauthenticated";

interface AuthContextValue {
  status: AuthStatus;
  user: UserPublic | null;
  accessToken: string | null;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, fullName?: string) => Promise<void>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

function applyTokenResponse(
  response: TokenResponse,
  setUser: (u: UserPublic) => void,
  setAccessToken: (t: string) => void,
  setStatus: (s: AuthStatus) => void
): void {
  storeTokens(response.access_token, response.refresh_token);
  setAccessToken(response.access_token);
  setUser(response.user);
  setStatus("authenticated");
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [user, setUser] = useState<UserPublic | null>(null);
  const [accessToken, setAccessToken] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;

    async function hydrate() {
      const { accessToken: storedAccess, refreshToken: storedRefresh } = getStoredTokens();

      if (!storedAccess || !storedRefresh) {
        if (!cancelled) setStatus("unauthenticated");
        return;
      }

      try {
        const currentUser = await meRequest(storedAccess);
        if (cancelled) return;
        setAccessToken(storedAccess);
        setUser(currentUser);
        setStatus("authenticated");
        return;
      } catch {
        // Access token expired or invalid — try the refresh token before
        // giving up, so a short-lived access token doesn't force a
        // re-login every time the page reloads.
      }

      try {
        const refreshed = await refreshRequest(storedRefresh);
        if (cancelled) return;
        applyTokenResponse(refreshed, setUser, setAccessToken, setStatus);
      } catch {
        if (cancelled) return;
        clearTokens();
        setStatus("unauthenticated");
      }
    }

    hydrate();
    return () => {
      cancelled = true;
    };
  }, []);

  const login = useCallback(async (email: string, password: string) => {
    const response = await loginRequest(email, password);
    applyTokenResponse(response, setUser, setAccessToken, setStatus);
  }, []);

  const register = useCallback(async (email: string, password: string, fullName?: string) => {
    const response = await registerRequest(email, password, fullName);
    applyTokenResponse(response, setUser, setAccessToken, setStatus);
  }, []);

  const logout = useCallback(async () => {
    const { refreshToken } = getStoredTokens();
    if (refreshToken) {
      try {
        await logoutRequest(refreshToken);
      } catch {
        // Best-effort: even if the backend call fails (e.g. already
        // expired), the client must still forget its local session.
      }
    }
    clearTokens();
    setUser(null);
    setAccessToken(null);
    setStatus("unauthenticated");
  }, []);

  const value = useMemo<AuthContextValue>(
    () => ({ status, user, accessToken, login, register, logout }),
    [status, user, accessToken, login, register, logout]
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) {
    throw new Error("useAuth must be used within an AuthProvider.");
  }
  return ctx;
}
