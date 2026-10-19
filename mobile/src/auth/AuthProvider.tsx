/** Session state: backend address, tokens (in the Android keystore via
 * expo-secure-store, never in plain storage) and the signed-in user. */

import * as SecureStore from "expo-secure-store";
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { configureClient, currentTokens, type Tokens } from "../api/client";
import { api, type UserPublic } from "../api/endpoints";
import { loadBackendUrl, saveBackendUrl } from "../config";

const TOKENS_KEY = "gainzalgo.tokens";

interface AuthState {
  ready: boolean;
  backendUrl: string | null;
  user: UserPublic | null;
  signIn: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string, name?: string) => Promise<void>;
  signOut: () => Promise<void>;
  setBackendUrl: (url: string | null) => Promise<void>;
  refreshUser: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

async function persistTokens(tokens: Tokens | null): Promise<void> {
  try {
    if (tokens) await SecureStore.setItemAsync(TOKENS_KEY, JSON.stringify(tokens));
    else await SecureStore.deleteItemAsync(TOKENS_KEY);
  } catch {
    // keystore unavailable: the session simply does not survive a restart
  }
}

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [backendUrl, setUrl] = useState<string | null>(null);
  const [user, setUser] = useState<UserPublic | null>(null);

  const onTokens = useCallback((tokens: Tokens | null) => {
    void persistTokens(tokens);
    if (!tokens) setUser(null);
  }, []);

  const refreshUser = useCallback(async () => {
    if (!currentTokens()) {
      setUser(null);
      return;
    }
    try {
      setUser(await api.me());
    } catch {
      setUser(null);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const url = await loadBackendUrl();
      let tokens: Tokens | null = null;
      try {
        const raw = await SecureStore.getItemAsync(TOKENS_KEY);
        tokens = raw ? (JSON.parse(raw) as Tokens) : null;
      } catch {
        tokens = null;
      }
      configureClient({ baseUrl: url, tokens, onTokens });
      if (cancelled) return;
      setUrl(url);
      if (url && tokens) {
        try {
          const me = await api.me();
          if (!cancelled) setUser(me);
        } catch {
          // offline or expired: the login screen / offline state handles it
        }
      }
      if (!cancelled) setReady(true);
    })();
    return () => {
      cancelled = true;
    };
  }, [onTokens]);

  const signIn = useCallback(async (email: string, password: string) => {
    const r = await api.login(email.trim(), password);
    const tokens = { accessToken: r.access_token, refreshToken: r.refresh_token };
    configureClient({ tokens });
    await persistTokens(tokens);
    setUser(r.user);
  }, []);

  const register = useCallback(async (email: string, password: string, name?: string) => {
    const r = await api.register(email.trim(), password, name);
    const tokens = { accessToken: r.access_token, refreshToken: r.refresh_token };
    configureClient({ tokens });
    await persistTokens(tokens);
    setUser(r.user);
  }, []);

  const signOut = useCallback(async () => {
    const tokens = currentTokens();
    if (tokens) {
      try {
        await api.logout(tokens.refreshToken);
      } catch {
        // signing out locally is enough if the backend is unreachable
      }
    }
    configureClient({ tokens: null });
    await persistTokens(null);
    setUser(null);
  }, []);

  const setBackendUrl = useCallback(async (url: string | null) => {
    await saveBackendUrl(url);
    const effective = await loadBackendUrl();
    configureClient({ baseUrl: effective, tokens: null });
    await persistTokens(null); // a different backend means a different account
    setUser(null);
    setUrl(effective);
  }, []);

  const value = useMemo(
    () => ({ ready, backendUrl, user, signIn, register, signOut, setBackendUrl, refreshUser }),
    [ready, backendUrl, user, signIn, register, signOut, setBackendUrl, refreshUser],
  );
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth outside AuthProvider");
  return ctx;
}
