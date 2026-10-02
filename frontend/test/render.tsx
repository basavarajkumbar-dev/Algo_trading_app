import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { initialMarketState, useMarketStore } from "@/lib/stores/market-store";

import components from "./fixtures/components.json";
import events from "./fixtures/events.json";
import intelligenceNifty from "./fixtures/intelligence-nifty.json";
import intelligence from "./fixtures/intelligence.json";
import performance from "./fixtures/performance.json";
import route from "./fixtures/route.json";
import scanner from "./fixtures/scanner.json";
import systemHealth from "./fixtures/system-health.json";
import trades from "./fixtures/trades.json";
import tradingRisk from "./fixtures/trading-risk.json";
import tradingStatus from "./fixtures/trading-status.json";

/** Real responses captured from the backend, keyed by API path. */
export const FIXTURES: Record<string, unknown> = {
  "/trading/status": tradingStatus,
  "/trading/risk": tradingRisk,
  "/trading/trades": trades,
  "/trading/orders": { items: [], total: 0, limit: 100, offset: 0, note: "" },
  "/system/components": components,
  "/system/events": events,
  "/system/health": systemHealth,
  "/analytics/performance": performance,
  "/scanner": scanner,
  "/market/intelligence": intelligence,
  "/market/intelligence/NIFTY50": intelligenceNifty,
  "/strategies/route": route,
};

export const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value)) as T;

type Route = unknown | Error | ((url: URL) => unknown);

/**
 * Replace fetch with a router over FIXTURES (plus overrides). An Error
 * value answers 503; a missing route answers 404, so a component calling
 * an endpoint the test did not expect fails loudly.
 */
export function mockApi(overrides: Record<string, Route> = {}) {
  const routes: Record<string, Route> = { ...FIXTURES, ...overrides };
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = new URL(typeof input === "string" ? input : input.toString());
    const path = url.pathname.replace(/^\/api\/v1/, "");
    const route = routes[path];
    if (route === undefined) {
      return new Response(JSON.stringify({ detail: `no fixture for ${path}` }), { status: 404 });
    }
    if (route instanceof Error) {
      return new Response(JSON.stringify({ detail: route.message }), { status: 503 });
    }
    const body = typeof route === "function" ? (route as (u: URL) => unknown)(url) : route;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

export function calledPaths(fetchMock: ReturnType<typeof mockApi>): string[] {
  return fetchMock.mock.calls.map(([input]) =>
    new URL(String(input)).pathname.replace(/^\/api\/v1/, "")
  );
}

export function makeTestClient(): QueryClient {
  return new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity, refetchInterval: false } },
  });
}

export function renderWithProviders(ui: ReactElement, client: QueryClient = makeTestClient()) {
  useMarketStore.setState({ ...initialMarketState });
  return {
    client,
    ...render(
      <QueryClientProvider client={client}>
        <TooltipProvider>{ui}</TooltipProvider>
      </QueryClientProvider>
    ),
  };
}

/** A WebSocket stand-in the tests drive by hand. */
export class MockWebSocket {
  static instances: MockWebSocket[] = [];
  url: string;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;

  constructor(url: string) {
    this.url = url;
    MockWebSocket.instances.push(this);
  }

  open() {
    this.onopen?.();
  }

  emit(message: unknown) {
    this.onmessage?.({ data: JSON.stringify(message) });
  }

  drop() {
    this.onclose?.();
  }

  close() {
    this.closed = true;
  }
}
