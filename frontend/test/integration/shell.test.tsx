// @vitest-environment jsdom
import { act, fireEvent, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Header } from "@/components/layout/header";
import { Sidebar } from "@/components/layout/sidebar";
import { SocketBridge } from "@/components/realtime/socket-bridge";
import { formatPrice } from "@/lib/intelligence-format";
import { useMarketStore } from "@/lib/stores/market-store";
import { useUiStore } from "@/lib/stores/ui-store";

import events from "../fixtures/events.json";
import { MockWebSocket, calledPaths, mockApi, renderWithProviders } from "../render";

vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({ accessToken: "token", user: { email: "t@example.com" }, status: "authenticated", logout: vi.fn() }),
}));
let pathname = "/analytics";
vi.mock("next/navigation", () => ({ usePathname: () => pathname }));

beforeEach(() => {
  MockWebSocket.instances = [];
  vi.stubGlobal("WebSocket", MockWebSocket);
  useUiStore.setState({ notificationsSeenAt: null });
});
afterEach(() => vi.useRealTimers());

describe("sidebar", () => {
  it("lists the twelve sections and marks the current one", () => {
    mockApi();
    renderWithProviders(<Sidebar />);
    const links = screen.getAllByRole("link");
    expect(links.map((a) => a.textContent?.replace("planned", "").trim())).toEqual([
      "Dashboard", "Market", "Scanner", "Signals", "Positions", "Trades",
      "Analytics", "Strategies", "Backtesting", "Risk", "System", "Settings",
    ]);
    expect(screen.getByRole("link", { name: /Analytics/ })).toHaveAttribute("aria-current", "page");
    expect(screen.getByRole("link", { name: /Dashboard/ })).not.toHaveAttribute("aria-current");
  });

  it("highlights Market on the price action page", () => {
    pathname = "/price-action";
    mockApi();
    renderWithProviders(<Sidebar />);
    expect(screen.getByRole("link", { name: /Market/ })).toHaveAttribute("aria-current", "page");
    pathname = "/analytics";
  });
});

describe("top bar + WebSocket (integration)", () => {
  it("shows mode, algo, health and live NIFTY from the socket", async () => {
    mockApi();
    renderWithProviders(
      <>
        <SocketBridge />
        <Header />
      </>
    );
    await waitFor(() => expect(screen.getByTestId("topbar-health")).toHaveTextContent("HEALTHY"));
    expect(screen.getByTestId("topbar-mode")).toHaveTextContent("PAPER");
    expect(screen.getByTestId("topbar-algo")).not.toHaveTextContent("Unknown");
    expect(screen.getByTestId("topbar-market")).toHaveTextContent(/Simulated 24h|Open|Closed|No new entries/);

    const ws = MockWebSocket.instances[0]!;
    expect(ws.url).toMatch(/\/api\/v1\/ws\/market$/);
    expect(screen.getByTestId("socket-status")).toHaveTextContent("Connecting");
    act(() => ws.open());
    expect(screen.getByTestId("socket-status")).toHaveTextContent("Live");

    act(() =>
      ws.emit({ type: "market_snapshot", symbol: "NIFTY50", price: 25012.35, change: 100, change_percent: 0.4, volume: 0, timestamp: "2026-10-02T05:00:00Z" })
    );
    expect(screen.getByTestId("topbar-nifty")).toHaveTextContent(formatPrice(25012.35));
    act(() => ws.emit({ type: "market_tick", symbol: "NIFTY50", price: 25020.0, volume: 0, timestamp: "2026-10-02T05:00:01Z" }));
    expect(screen.getByTestId("topbar-nifty")).toHaveTextContent(formatPrice(25020));
  });

  it("refetches trading and system data when a trading event arrives", async () => {
    const fetchMock = mockApi();
    renderWithProviders(
      <>
        <SocketBridge />
        <Header />
      </>
    );
    await waitFor(() => expect(screen.getByTestId("topbar-health")).toHaveTextContent("HEALTHY"));
    const before = calledPaths(fetchMock).filter((p) => p === "/trading/status").length;
    const eventsBefore = calledPaths(fetchMock).filter((p) => p === "/system/events").length;
    act(() =>
      MockWebSocket.instances[0]!.emit({
        type: "trading_event",
        event_type: "TRADE_CLOSED",
        severity: "INFO",
        message: "LONG 10 TCS: TARGET",
        at: "2026-10-02T05:00:00Z",
        metadata: {},
      })
    );
    await waitFor(() =>
      expect(calledPaths(fetchMock).filter((p) => p === "/trading/status").length).toBeGreaterThan(before)
    );
    await waitFor(() =>
      expect(calledPaths(fetchMock).filter((p) => p === "/system/events").length).toBeGreaterThan(eventsBefore)
    );
    expect(useMarketStore.getState().tradingEvents[0]!.event_type).toBe("TRADE_CLOSED");
  });

  it("reconnects after the socket drops", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi();
    renderWithProviders(<SocketBridge />);
    act(() => MockWebSocket.instances[0]!.open());
    act(() => MockWebSocket.instances[0]!.drop());
    expect(useMarketStore.getState().status).toBe("closed");
    await act(async () => {
      vi.advanceTimersByTime(2_100);
    });
    expect(MockWebSocket.instances).toHaveLength(2);
    expect(useMarketStore.getState().status).toBe("reconnecting");
  });

  it("counts unread notifications and clears them once the menu is opened and closed", async () => {
    mockApi();
    renderWithProviders(<Header />);
    await waitFor(() =>
      expect(screen.getByTestId("notifications-count")).toHaveTextContent(
        events.items.length > 9 ? "9+" : String(events.items.length)
      )
    );
    const trigger = screen.getByTestId("notifications-trigger");
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "Enter" });
    await screen.findByText("Notifications");
    expect(screen.getByText(events.items[0]!.message)).toBeInTheDocument();
    fireEvent.keyDown(document.activeElement ?? document.body, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("notifications-count")).not.toBeInTheDocument());
    expect(useUiStore.getState().notificationsSeenAt).toBe(events.items[0]!.created_at);
  });
});
