/**
 * HTTP client for the GainzAlgo backend.
 *
 * - every request carries the signed-in user's access token;
 * - a 401 triggers one refresh (/auth/refresh) and one retry, then signs out;
 * - network failures, timeouts, server errors and "not configured" are
 *   distinct error kinds so screens can show the right state;
 * - nothing here retries a POST: order-related calls are never repeated
 *   automatically (a repeat could duplicate an action).
 */

export type ApiErrorKind =
  | "NO_BACKEND" // no backend address configured
  | "OFFLINE" // network unreachable / DNS / TLS
  | "TIMEOUT"
  | "UNAUTHORIZED"
  | "FORBIDDEN"
  | "NOT_FOUND"
  | "CONFLICT"
  | "VALIDATION"
  | "UNAVAILABLE" // 503: backend up, component not ready
  | "SERVER";

export class ApiError extends Error {
  constructor(
    message: string,
    public kind: ApiErrorKind,
    public status: number | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface Tokens {
  accessToken: string;
  refreshToken: string;
}

interface ClientState {
  baseUrl: string | null;
  tokens: Tokens | null;
  onTokens: (tokens: Tokens | null) => void;
  fetchImpl: typeof fetch;
  timeoutMs: number;
}

const state: ClientState = {
  baseUrl: null,
  tokens: null,
  onTokens: () => undefined,
  fetchImpl: (...args) => fetch(...args),
  timeoutMs: 15_000,
};

export function configureClient(patch: Partial<ClientState>): void {
  Object.assign(state, patch);
}

export function currentBaseUrl(): string | null {
  return state.baseUrl;
}

export function currentTokens(): Tokens | null {
  return state.tokens;
}

function kindFor(status: number): ApiErrorKind {
  if (status === 401) return "UNAUTHORIZED";
  if (status === 403) return "FORBIDDEN";
  if (status === 404) return "NOT_FOUND";
  if (status === 409) return "CONFLICT";
  if (status === 422) return "VALIDATION";
  if (status === 503) return "UNAVAILABLE";
  return "SERVER";
}

async function detailOf(response: Response): Promise<string | undefined> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    if (Array.isArray(body?.detail)) {
      return body.detail
        .map((d: { msg?: string; loc?: unknown[] }) => `${(d.loc ?? []).slice(-1)[0] ?? ""}: ${d.msg ?? ""}`)
        .join("; ");
    }
    if (typeof body?.error?.message === "string") return body.error.message;
  } catch {
    // not JSON
  }
  return undefined;
}

async function raw(path: string, init: RequestInit, token: string | null): Promise<Response> {
  if (!state.baseUrl) throw new ApiError("No backend address is set.", "NO_BACKEND");
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), state.timeoutMs);
  try {
    return await state.fetchImpl(`${state.baseUrl}/api/v1${path}`, {
      ...init,
      headers,
      signal: controller.signal,
    });
  } catch (error) {
    if ((error as { name?: string })?.name === "AbortError") {
      throw new ApiError("The backend did not answer in time.", "TIMEOUT");
    }
    throw new ApiError("Cannot reach the backend (offline or wrong address).", "OFFLINE");
  } finally {
    clearTimeout(timer);
  }
}

let refreshing: Promise<boolean> | null = null;

async function refreshTokens(): Promise<boolean> {
  const tokens = state.tokens;
  if (!tokens) return false;
  refreshing ??= (async () => {
    try {
      const response = await raw(
        "/auth/refresh",
        { method: "POST", body: JSON.stringify({ refresh_token: tokens.refreshToken }) },
        null,
      );
      if (!response.ok) return false;
      const body = (await response.json()) as { access_token: string; refresh_token: string };
      state.tokens = { accessToken: body.access_token, refreshToken: body.refresh_token };
      state.onTokens(state.tokens);
      return true;
    } catch {
      return false;
    } finally {
      refreshing = null;
    }
  })();
  return refreshing;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response = await raw(path, init, state.tokens?.accessToken ?? null);
  if (response.status === 401 && state.tokens && !path.startsWith("/auth/")) {
    if (await refreshTokens()) {
      response = await raw(path, init, state.tokens?.accessToken ?? null);
    }
    if (response.status === 401) {
      state.tokens = null;
      state.onTokens(null);
    }
  }
  if (!response.ok) {
    const detail = await detailOf(response);
    throw new ApiError(
      detail ?? `The backend answered ${response.status}.`,
      kindFor(response.status),
      response.status,
    );
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const get = <T>(path: string) => request<T>(path, { method: "GET" });
export const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });
export const patch = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "PATCH", body: JSON.stringify(body) });

/** A one-line explanation for an error, for screens. */
export function describeError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.kind) {
      case "NO_BACKEND":
        return "Set the backend address in Settings.";
      case "OFFLINE":
        return "Backend unreachable. Check your connection and the backend address.";
      case "TIMEOUT":
        return "The backend is slow to answer. Try again.";
      case "UNAUTHORIZED":
        return "Your session ended. Sign in again.";
      case "FORBIDDEN":
        return "Your role does not allow this action.";
      default:
        return error.message;
    }
  }
  return error instanceof Error ? error.message : "Something went wrong.";
}
