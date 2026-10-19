/** Screens against a mocked backend: real data is rendered as returned,
 * simulated data is labelled, offline / error states appear, and actions
 * go to the backend (which owns the risk checks). */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react-native";
import React from "react";

import { configureClient } from "../src/api/client";
import { AuthProvider } from "../src/auth/AuthProvider";
import { DashboardScreen, LoginScreen } from "../src/screens/core";
import { RiskScreen } from "../src/screens/trading";
import { SystemHealthScreen } from "../src/screens/system";

jest.mock("@react-navigation/native", () => ({
  ...jest.requireActual("@react-navigation/native"),
  useNavigation: () => ({ navigate: jest.fn() }),
}));

type Handler = (path: string, init: RequestInit) => { status: number; body: unknown };

function backend(handler: Handler) {
  const calls: string[] = [];
  const fetchImpl = jest.fn(async (url: string, init: RequestInit) => {
    const path = url.split("/api/v1")[1];
    calls.push(`${init.method} ${path}`);
    const { status, body } = handler(path, init);
    return { ok: status < 300, status, json: async () => body } as unknown as Response;
  });
  return { fetchImpl, calls };
}

jest.setTimeout(20_000);

const clients: QueryClient[] = [];

afterEach(async () => {
  // unmount first, then stop every poll so no timer outlives the test
  await cleanup();
  clients.splice(0).forEach((c) => c.clear());
});

async function wrap(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
  clients.push(qc);
  return await render(
    <QueryClientProvider client={qc}>
      <AuthProvider>{ui}</AuthProvider>
    </QueryClientProvider>,
  );
}

const STATUS = {
  mode: "PAPER",
  state: "IDLE",
  auto_trading: false,
  emergency_stop: false,
  emergency_reason: null,
  active_trade: null,
  balance: { starting_capital: 100000, cash: 100000, used_margin: 0, available: 100000, realized_pnl: 250, unrealized_pnl: 0, charges_paid: 53, equity: 100250 },
  risk: { blocked_reasons: [] },
  system_healthy: true,
  health_detail: "ok",
  market_open: false,
  entries_allowed: false,
  market_detail: "NSE closed",
  broker: { slippage_bps: 5 },
  note: "paper",
};

function happy(path: string): { status: number; body: unknown } {
  if (path.startsWith("/trading/status")) return { status: 200, body: STATUS };
  if (path.startsWith("/market/status")) return { status: 200, body: { data_source: "SIMULATED", watchlist: [], notes: [] } };
  if (path.startsWith("/market/intelligence")) return { status: 503, body: { detail: "No analysis yet" } };
  if (path.startsWith("/scanner")) return { status: 503, body: { detail: "No scan has completed yet." } };
  if (path.startsWith("/broker/zerodha/status")) return { status: 200, body: { configured: false, connected: false, auth: "NOT_CONFIGURED" } };
  if (path.startsWith("/auth/me")) return { status: 200, body: { id: "u", email: "a@b.c", role: "TRADER", full_name: null, is_active: true } };
  return { status: 404, body: { detail: "not found" } };
}

beforeEach(async () => {
  // what AuthProvider restores from the keystore at start-up
  const SecureStore = jest.requireMock("expo-secure-store");
  await SecureStore.setItemAsync("gainzalgo.backendUrl", "https://api.example.com");
  await SecureStore.setItemAsync("gainzalgo.tokens", JSON.stringify({ accessToken: "A", refreshToken: "R" }));
  configureClient({ baseUrl: "https://api.example.com", tokens: { accessToken: "A", refreshToken: "R" } });
});

it("command center shows backend numbers and labels simulated data", async () => {
  const { fetchImpl } = backend(happy);
  configureClient({ fetchImpl: fetchImpl as never });
  await wrap(<DashboardScreen />);
  await waitFor(() => expect(screen.getByText("₹1,00,250")).toBeTruthy());
  expect(screen.getByText("SIMULATED DATA")).toBeTruthy();
  expect(screen.getByText("ZERODHA NOT CONNECTED")).toBeTruthy();
  // the scanner answered 503: the card shows the error, not invented signals
  await waitFor(() => expect(screen.getAllByTestId("state-error").length).toBeGreaterThan(0));
});

it("shows an offline state when the backend is unreachable", async () => {
  configureClient({ fetchImpl: (async () => { throw new TypeError("Network request failed"); }) as never });
  await wrap(<RiskScreen />);
  await waitFor(() => expect(screen.getByTestId("state-offline")).toBeTruthy());
  expect(screen.getByText("Backend unreachable")).toBeTruthy();
});

it("system health says when the backend cannot be reached", async () => {
  configureClient({ fetchImpl: (async () => { throw new TypeError("offline"); }) as never });
  await wrap(<SystemHealthScreen />);
  await waitFor(() => expect(screen.getByText("BACKEND UNREACHABLE")).toBeTruthy());
});

it("login posts credentials to the backend and reports a wrong password", async () => {
  const { fetchImpl, calls } = backend((path) =>
    path === "/auth/login" ? { status: 401, body: { detail: "Invalid email or password." } } : happy(path),
  );
  configureClient({ fetchImpl: fetchImpl as never, tokens: null });
  await jest.requireMock("expo-secure-store").deleteItemAsync("gainzalgo.tokens");
  await wrap(<LoginScreen />);
  await waitFor(() => expect(screen.getByTestId("email")).toBeTruthy());
  await fireEvent.changeText(screen.getByTestId("email"), "trader@example.com");
  await fireEvent.changeText(screen.getByTestId("password"), "correct-horse-1");
  await fireEvent.press(screen.getByTestId("login-submit"));
  await waitFor(() => expect(screen.getByTestId("login-error")).toBeTruthy(), { timeout: 3000 }).catch((e) => {
    throw new Error(`${e.message}\ncalls: ${calls.join(", ")}`);
  });
  expect(screen.getByText("Wrong email or password.")).toBeTruthy();
  expect(calls).toContain("POST /auth/login");
});
