/**
 * Backend address. The app never runs the trading engine itself: it talks
 * to the GainzAlgo backend service over HTTPS.
 *
 * Order of precedence:
 *   1. the address saved on the device (Settings -> Backend), if any;
 *   2. EXPO_PUBLIC_API_BASE_URL baked in at build time (the release build's
 *      production backend, set in CI from a repository variable);
 *   3. none: the app asks for one on first launch.
 *
 * There is deliberately no hard-coded default address. Plain http:// is
 * accepted only for development builds or private-network addresses the
 * user types explicitly, and is shown as "not encrypted".
 */

import * as SecureStore from "expo-secure-store";

const KEY = "gainzalgo.backendUrl";

export const BUILD_BACKEND_URL: string | null =
  normalizeUrl(process.env.EXPO_PUBLIC_API_BASE_URL ?? "") ?? null;

export const APP_SCHEME = "gainzalgo";

export function normalizeUrl(raw: string): string | null {
  const text = raw.trim().replace(/\/+$/, "").replace(/\/api\/v1$/, "");
  if (!text) return null;
  let url: URL;
  try {
    url = new URL(text);
  } catch {
    return null;
  }
  if (url.protocol !== "https:" && url.protocol !== "http:") return null;
  if (url.username || url.password) return null; // never credentials in a URL
  return `${url.protocol}//${url.host}${url.pathname === "/" ? "" : url.pathname}`;
}

export function isEncrypted(url: string | null): boolean {
  return !!url && url.startsWith("https://");
}

export async function loadBackendUrl(): Promise<string | null> {
  try {
    const saved = await SecureStore.getItemAsync(KEY);
    return normalizeUrl(saved ?? "") ?? BUILD_BACKEND_URL;
  } catch {
    return BUILD_BACKEND_URL;
  }
}

export async function saveBackendUrl(url: string | null): Promise<void> {
  if (url === null) {
    await SecureStore.deleteItemAsync(KEY);
    return;
  }
  const normalized = normalizeUrl(url);
  if (!normalized) throw new Error("Enter a full address such as https://api.example.com");
  await SecureStore.setItemAsync(KEY, normalized);
}
