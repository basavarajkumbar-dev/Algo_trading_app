/**
 * Base URL the *browser* uses to reach the backend.
 *
 * This is intentionally a browser-facing URL (e.g. http://localhost:8000),
 * not the Docker-internal service hostname — calls in this phase run
 * client-side, so they need the same address the developer's machine
 * uses to reach the published backend port.
 */
export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "http://localhost:8000";

export const API_V1_BASE_URL = `${API_BASE_URL}/api/v1`;

/**
 * Browser-facing WebSocket URL, derived from the same base the REST
 * client uses (http -> ws, https -> wss).
 */
export const WS_BASE_URL = `${API_V1_BASE_URL.replace(/^http/, "ws")}`;

export class ApiError extends Error {
  status: number | null;

  constructor(message: string, status: number | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

interface RequestOptions {
  accessToken?: string | null;
}

async function request<T>(
  path: string,
  init: RequestInit,
  options: RequestOptions = {}
): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "application/json");
  if (init.body) headers.set("Content-Type", "application/json");
  if (options.accessToken) headers.set("Authorization", `Bearer ${options.accessToken}`);

  let response: Response;
  try {
    response = await fetch(`${API_V1_BASE_URL}${path}`, {
      ...init,
      headers,
      cache: "no-store",
    });
  } catch {
    throw new ApiError("Could not reach the backend API.");
  }

  if (!response.ok) {
    let detail: string | undefined;
    try {
      const body = await response.json();
      detail =
        typeof body?.detail === "string"
          ? body.detail
          : typeof body?.error?.message === "string"
            ? body.error.message
            : undefined;
    } catch {
      // response body wasn't JSON — fall through to the generic message
    }
    throw new ApiError(
      detail ?? `Backend responded with status ${response.status}.`,
      response.status
    );
  }

  if (response.status === 204) {
    return undefined as T;
  }
  return (await response.json()) as T;
}

/** Minimal typed fetch wrapper for GET requests. */
export async function apiGet<T>(path: string, options: RequestOptions = {}): Promise<T> {
  return request<T>(path, { method: "GET" }, options);
}

/** Minimal typed fetch wrapper for POST requests. */
export async function apiPost<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {}
): Promise<T> {
  return request<T>(
    path,
    { method: "POST", body: body !== undefined ? JSON.stringify(body) : undefined },
    options
  );
}

/** Minimal typed fetch wrapper for PATCH requests. */
export async function apiPatch<T>(
  path: string,
  body?: unknown,
  options: RequestOptions = {}
): Promise<T> {
  return request<T>(
    path,
    { method: "PATCH", body: body !== undefined ? JSON.stringify(body) : undefined },
    options
  );
}
