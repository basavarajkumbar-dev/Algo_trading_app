/** Client-side checks for the Kite Connect credential form. The backend
 * validates again; these only give an immediate, clear message. Nothing here
 * stores or logs a value. */

import type { BrokerConnectionStatus } from "../api/endpoints";
import type { Tone } from "../theme";

const API_KEY = /^[A-Za-z0-9]{6,64}$/;
const API_SECRET = /^[A-Za-z0-9]{12,128}$/;

export function validateCredentials(apiKey: string, apiSecret: string): string | null {
  const key = apiKey.trim();
  const secret = apiSecret.trim();
  if (!key) return "Enter your API key.";
  if (!secret) return "Enter your API secret.";
  if (!API_KEY.test(key)) return "The API key should be 6-64 letters or digits, no spaces.";
  if (!API_SECRET.test(secret)) return "The API secret should be 12-128 letters or digits, no spaces.";
  if (key === secret) return "The API key and the API secret must be different.";
  return null;
}

/** Credentials may only travel over HTTPS (a development build may talk to a
 * local http backend). */
export function mayTransmitSecrets(baseUrl: string | null, isDevBuild: boolean): boolean {
  if (!baseUrl) return false;
  if (baseUrl.startsWith("https://")) return true;
  return isDevBuild;
}

export function statusTone(status: BrokerConnectionStatus | undefined): Tone {
  switch (status) {
    case "CONNECTED":
      return "good";
    case "SESSION_EXPIRED":
      return "warn";
    case "CONNECTION_FAILED":
      return "bad";
    case "AWAITING_LOGIN":
      return "info";
    case "CREDENTIALS_SAVED":
      return "accent";
    default:
      return "neutral";
  }
}

/** "Reconnect" once a session existed or failed; "Connect" the first time. */
export function connectLabel(status: BrokerConnectionStatus | undefined): string {
  return status === "SESSION_EXPIRED" || status === "CONNECTION_FAILED" || status === "NOT_CONNECTED"
    ? "Reconnect to Zerodha"
    : "Connect to Zerodha";
}
