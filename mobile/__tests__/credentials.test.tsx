/** Zerodha credential screen against a mocked backend: empty on a fresh
 * install, the secret is sent once and never kept or shown, statuses are the
 * backend's, failures are not reported as success. */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import React from "react";

import { configureClient } from "../src/api/client";
import { AuthProvider } from "../src/auth/AuthProvider";
import { connectLabel, mayTransmitSecrets, statusTone, validateCredentials } from "../src/lib/credentials";
import { ZerodhaScreen } from "../src/screens/broker";

jest.mock("@react-navigation/native", () => ({
  ...jest.requireActual("@react-navigation/native"),
  useNavigation: () => ({ navigate: jest.fn() }),
}));

jest.setTimeout(20_000);

const KEY = "kitekey1234";
const SECRET = "kiteSecretValue0987";

const clients: QueryClient[] = [];
afterEach(async () => {
  await cleanup();
  clients.splice(0).forEach((c) => c.clear());
});

function status(overrides: Record<string, unknown> = {}) {
  return {
    broker: "ZERODHA",
    status: "NOT_CONFIGURED",
    status_label: "Not Configured",
    detail: null,
    configured: false,
    credentials_source: "USER",
    api_key_hint: null,
    api_secret: null,
    active_on_server: false,
    connected: false,
    user_id: null,
    user_name: null,
    session_expires_at: null,
    last_validated_at: null,
    last_connected_at: null,
    updated_at: null,
    storage: "ENCRYPTED",
    storage_problem: null,
    redirect_url_configured: true,
    paper_trading_available: true,
    live_trading_note: "",
    market_data: "SIMULATED",
    market_data_note: null,
    ...overrides,
  };
}

const SAVED = status({
  status: "CREDENTIALS_SAVED",
  status_label: "Credentials Saved",
  configured: true,
  api_key_hint: "••••1234",
  api_secret: "Configured",
  active_on_server: true,
});

type Reply = { status: number; body: unknown } | "offline";

function backend(role: string, handler: (method: string, path: string, body: string | undefined) => Reply) {
  const calls: { method: string; path: string; body?: string }[] = [];
  const fetchImpl = jest.fn(async (url: string, init: RequestInit) => {
    const path = url.split("/api/v1")[1];
    const method = init.method ?? "GET";
    const body = typeof init.body === "string" ? init.body : undefined;
    calls.push({ method, path, body });
    if (path.startsWith("/auth/me")) {
      return { ok: true, status: 200, json: async () => ({ id: "u", email: "t@x.y", role, full_name: null, is_active: true }) } as unknown as Response;
    }
    const reply = handler(method, path, body);
    if (reply === "offline") throw new TypeError("Network request failed");
    return { ok: reply.status < 300, status: reply.status, json: async () => reply.body } as unknown as Response;
  });
  configureClient({ fetchImpl: fetchImpl as never });
  return calls;
}

async function show() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(qc);
  return render(
    <QueryClientProvider client={qc}>
      <AuthProvider>
        <ZerodhaScreen />
      </AuthProvider>
    </QueryClientProvider>,
  );
}

function storedValues(): string {
  const SecureStore = jest.requireMock("expo-secure-store");
  return JSON.stringify(SecureStore.setItemAsync.mock.calls);
}

beforeEach(async () => {
  const SecureStore = jest.requireMock("expo-secure-store");
  await SecureStore.setItemAsync("gainzalgo.backendUrl", "https://api.example.com");
  await SecureStore.setItemAsync("gainzalgo.tokens", JSON.stringify({ accessToken: "A", refreshToken: "R" }));
  configureClient({ baseUrl: "https://api.example.com", tokens: { accessToken: "A", refreshToken: "R" } });
});

describe("credential rules", () => {
  it("validates like the backend", () => {
    expect(validateCredentials("", SECRET)).toMatch(/API key/);
    expect(validateCredentials(KEY, "")).toMatch(/API secret/);
    expect(validateCredentials("bad key", SECRET)).toMatch(/letters or digits/);
    expect(validateCredentials(KEY, "short")).toMatch(/12-128/);
    expect(validateCredentials(SECRET, SECRET)).toMatch(/different/);
    expect(validateCredentials(` ${KEY} `, SECRET)).toBeNull();
  });

  it("sends secrets only over HTTPS outside development builds", () => {
    expect(mayTransmitSecrets("https://api.example.com", false)).toBe(true);
    expect(mayTransmitSecrets("http://192.168.1.5:8000", false)).toBe(false);
    expect(mayTransmitSecrets("http://10.0.2.2:8000", true)).toBe(true);
    expect(mayTransmitSecrets(null, true)).toBe(false);
  });

  it("labels reconnects and tones", () => {
    expect(connectLabel("SESSION_EXPIRED")).toBe("Reconnect to Zerodha");
    expect(connectLabel("CREDENTIALS_SAVED")).toBe("Connect to Zerodha");
    expect(statusTone("CONNECTED")).toBe("good");
    expect(statusTone("CONNECTION_FAILED")).toBe("bad");
  });
});

describe("Zerodha credentials screen", () => {
  it("starts empty, hides the secret, saves once and forgets it", async () => {
    let saved = false;
    const calls = backend("TRADER", (method, path) => {
      if (path === "/broker/zerodha/credentials" && method === "PUT") {
        saved = true;
        return { status: 200, body: SAVED };
      }
      if (path === "/broker/zerodha/credentials") return { status: 200, body: saved ? SAVED : status() };
      return { status: 404, body: { detail: "nf" } };
    });
    await show();
    await waitFor(() => expect(screen.getByTestId("api-key")).toBeTruthy());
    expect(screen.getByText("Not Configured")).toBeTruthy();
    expect(screen.getByTestId("api-key").props.value).toBe("");
    expect(screen.getByTestId("api-secret").props.value).toBe("");
    expect(screen.getByTestId("api-secret").props.secureTextEntry).toBe(true);
    await fireEvent.press(screen.getByTestId("toggle-secret"));
    expect(screen.getByTestId("api-secret").props.secureTextEntry).toBe(false);
    await fireEvent.press(screen.getByTestId("toggle-secret"));

    await fireEvent.changeText(screen.getByTestId("api-key"), KEY);
    await fireEvent.changeText(screen.getByTestId("api-secret"), SECRET);
    await fireEvent.press(screen.getByTestId("save-credentials"));
    await waitFor(() => expect(screen.getByText(/Credentials saved on the server/)).toBeTruthy());

    const put = calls.find((c) => c.method === "PUT");
    expect(put?.path).toBe("/broker/zerodha/credentials");
    expect(JSON.parse(put?.body ?? "{}")).toEqual({ api_key: KEY, api_secret: SECRET });
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(1);
    // the form is gone, the screen shows only the mask, nothing was kept on the phone
    expect(screen.queryByTestId("api-secret")).toBeNull();
    expect(screen.getAllByText("••••1234").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Configured").length).toBeGreaterThan(0);
    expect(screen.queryByDisplayValue(SECRET)).toBeNull();
    expect(screen.queryByText(new RegExp(SECRET))).toBeNull();
    expect(storedValues()).not.toContain(SECRET);
    expect(screen.getByTestId("zerodha-connect")).toBeTruthy();
  });

  it("does not claim success when the backend is unreachable or refuses", async () => {
    let mode: "offline" | "refuse" = "offline";
    const calls = backend("TRADER", (method, path) => {
      if (method === "PUT") {
        return mode === "offline"
          ? "offline"
          : { status: 503, body: { detail: "Secure credential storage is not set up on the server. Nothing was saved." } };
      }
      if (path === "/broker/zerodha/credentials") return { status: 200, body: status() };
      return { status: 404, body: {} };
    });
    await show();
    await waitFor(() => expect(screen.getByTestId("api-key")).toBeTruthy());
    await fireEvent.changeText(screen.getByTestId("api-key"), KEY);
    await fireEvent.changeText(screen.getByTestId("api-secret"), SECRET);
    await fireEvent.press(screen.getByTestId("save-credentials"));
    await waitFor(() => expect(screen.getByTestId("zerodha-message")).toBeTruthy());
    expect(screen.getByTestId("zerodha-message").props.children).toMatch(/^Not saved: .*unreachable/);
    mode = "refuse";
    await fireEvent.press(screen.getByTestId("save-credentials"));
    await waitFor(() =>
      expect(screen.getByTestId("zerodha-message").props.children).toMatch(/Not saved: .*Nothing was saved/),
    );
    expect(screen.queryByText(/Credentials saved on the server/)).toBeNull();
    expect(calls.filter((c) => c.method === "PUT")).toHaveLength(2);
  });

  it("checks the input before sending anything", async () => {
    const calls = backend("TRADER", (_m, path) =>
      path === "/broker/zerodha/credentials" ? { status: 200, body: status() } : { status: 404, body: {} },
    );
    await show();
    await waitFor(() => expect(screen.getByTestId("api-key")).toBeTruthy());
    await fireEvent.changeText(screen.getByTestId("api-key"), "has space");
    await fireEvent.changeText(screen.getByTestId("api-secret"), SECRET);
    await fireEvent.press(screen.getByTestId("save-credentials"));
    await waitFor(() => expect(screen.getByText(/letters or digits/)).toBeTruthy());
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });

  it("shows the server's Session Expired state with a reconnect action", async () => {
    backend("TRADER", (_m, path) =>
      path === "/broker/zerodha/credentials"
        ? {
            status: 200,
            body: { ...SAVED, status: "SESSION_EXPIRED", status_label: "Session Expired", last_connected_at: "2026-10-19T04:00:00Z" },
          }
        : { status: 404, body: {} },
    );
    await show();
    await waitFor(() => expect(screen.getAllByText("Session Expired").length).toBeGreaterThan(0));
    expect(screen.getByText("Reconnect to Zerodha")).toBeTruthy();
    expect(screen.getByText(/credentials are kept/)).toBeTruthy();
    expect(screen.queryByTestId("api-secret")).toBeNull(); // not asked again
  });

  it("is read-only for a viewer and when the server cannot encrypt", async () => {
    backend("VIEWER", (_m, path) =>
      path === "/broker/zerodha/credentials"
        ? { status: 200, body: status({ storage: "UNAVAILABLE", storage_problem: "CREDENTIALS_ENCRYPTION_KEY is not set" }) }
        : { status: 404, body: {} },
    );
    await show();
    await waitFor(() => expect(screen.getByTestId("storage-unavailable")).toBeTruthy());
    expect(screen.getByTestId("api-secret").props.editable).toBe(false);
    expect(screen.getByTestId("save-credentials").props.accessibilityState.disabled).toBe(true);
    expect(screen.getByText(/role is read-only/)).toBeTruthy();
  });
});
