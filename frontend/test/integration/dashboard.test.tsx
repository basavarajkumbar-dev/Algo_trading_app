// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import DashboardPage from "@/app/(dashboard)/page";
import { formatPrice } from "@/lib/intelligence-format";
import { useMarketStore } from "@/lib/stores/market-store";
import { useUiStore } from "@/lib/stores/ui-store";
import type { TradingStatus } from "@/types/trading";

import scanner from "../fixtures/scanner.json";
import trades from "../fixtures/trades.json";
import tradingStatus from "../fixtures/trading-status.json";
import { calledPaths, clone, mockApi, renderWithProviders } from "../render";

vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({ accessToken: "token", user: { email: "t@example.com" }, status: "authenticated", logout: vi.fn() }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/", useRouter: () => ({ replace: vi.fn(), push: vi.fn() }) }));

/** The live status fixture with a closed trade from the trades fixture re-opened. */
function statusWithPosition(): TradingStatus {
  const status = clone(tradingStatus) as unknown as TradingStatus;
  const t = clone(trades.items.find((x) => x.filled_quantity > 0)!) as unknown as TradingStatus["active_trade"] & object;
  status.state = "POSITION_ACTIVE";
  status.active_trade = {
    ...t,
    status: "OPEN",
    exit_reason: null,
    exit_price: null,
    closed_at: null,
    open_quantity: t.filled_quantity,
    net_pnl: -10,
    opened_at: new Date(Date.now() - 65_000).toISOString(),
  };
  return status;
}

describe("dashboard command center (integration)", () => {
  it("fills every panel from the backend APIs", async () => {
    const fetchMock = mockApi();
    renderWithProviders(<DashboardPage />);

    const cc = await screen.findByTestId("command-center");
    await waitFor(() => expect(within(cc).getByTestId("cc-trades")).toHaveTextContent(String(tradingStatus.risk.state.trades_today)));
    expect(within(cc).getByText("PAPER")).toBeInTheDocument();
    expect(within(cc).getByTestId("cc-health")).toHaveTextContent("HEALTHY");
    expect(within(cc).getByTestId("cc-winloss")).toHaveTextContent(
      `${tradingStatus.risk.state.wins_today}W / ${tradingStatus.risk.state.losses_today}L`
    );

    // AI market brain rows
    const brain = await screen.findByTestId("market-brain");
    for (const label of ["Market regime", "Confidence", "Active strategy", "Trend", "Momentum", "VWAP", "Volume", "Volatility", "Structure", "Relative strength"]) {
      expect(within(brain).getByText(label)).toBeInTheDocument();
    }

    // live scanner rows and columns
    const scan = await screen.findByTestId("live-scanner");
    await waitFor(() => expect(within(scan).getAllByTestId("scanner-row")).toHaveLength(scanner.scan.rows.length));
    for (const col of ["Rank", "Stock", "Price", "Change", "Signal", "Score", "Strategy", "Status"]) {
      expect(within(scan).getByText(col)).toBeInTheDocument();
    }

    // signal panel shows the top signal and every contributing factor
    const panel = await screen.findByTestId("signal-panel");
    const top = scanner.scan.signals[0]!;
    await waitFor(() => expect(within(panel).getByTestId("signal-symbol")).toHaveTextContent(top.symbol));
    expect(within(panel).getAllByTestId("why-row")).toHaveLength(top.breakdown.components.length);
    for (const label of ["Entry", "SL", "Target", "Quantity", "Risk : reward"]) {
      expect(within(panel).getByText(label)).toBeInTheDocument();
    }

    // trade history comes from /trading/trades
    const history = await screen.findByTestId("trade-history");
    await waitFor(() =>
      expect(within(history).getAllByTestId("trade-row")).toHaveLength(Math.min(8, trades.items.length))
    );

    const paths = calledPaths(fetchMock);
    for (const p of ["/trading/status", "/scanner", "/market/intelligence", "/market/intelligence/NIFTY50", "/system/components", "/trading/trades", "/trading/risk"]) {
      expect(paths).toContain(p);
    }
  });

  it("selecting a scanner row switches the signal panel", async () => {
    mockApi();
    renderWithProviders(<DashboardPage />);
    const second = scanner.scan.signals[1]!;
    await screen.findAllByTestId("scanner-row");
    useUiStore.getState().setDashboardSignalKey(`${second.symbol}:${second.strategy}:${second.direction}`);
    await waitFor(() => expect(screen.getByTestId("signal-symbol")).toHaveTextContent(second.symbol));
    useUiStore.getState().setDashboardSignalKey(null);
  });

  it("shows the active position with live price, P&L, duration and the SL-entry-current-target bar", async () => {
    const status = statusWithPosition();
    const t = status.active_trade!;
    mockApi({ "/trading/status": status });
    renderWithProviders(<DashboardPage />);

    const pos = await screen.findByTestId("active-position");
    await within(pos).findByTestId("position-bar");
    // a live tick for the position's symbol drives "current"
    const dir = t.side === "LONG" ? 1 : -1;
    const live = t.entry_price! + dir * 1;
    useMarketStore.getState().receive({
      type: "market_snapshot",
      symbol: t.symbol,
      price: live,
      change: 0,
      change_percent: 0,
      volume: 1,
      timestamp: new Date().toISOString(),
    });
    await waitFor(() => expect(within(pos).getByTestId("position-current")).toHaveTextContent(formatPrice(live)));
    // net = trade net so far (-10) + 1 point x open quantity in favour
    const expected = -10 + t.open_quantity;
    expect(within(pos).getByTestId("position-pnl").textContent).toContain(
      `${expected >= 0 ? "+" : "−"}₹${Math.abs(expected).toFixed(2)}`
    );
    expect(within(pos).getByTestId("position-duration")).toHaveTextContent(/1m \d\ds/);
    const bar = within(pos).getByTestId("position-bar");
    expect(within(bar).getByTestId("position-bar-current")).toBeInTheDocument();
    expect(bar.getAttribute("aria-label")).toContain("entry");
  });

  it("shows errors instead of invented data when the backend is down", async () => {
    mockApi({
      "/trading/status": new Error("down"),
      "/scanner": new Error("down"),
      "/market/intelligence": new Error("down"),
    });
    renderWithProviders(<DashboardPage />);
    const scan = await screen.findByTestId("live-scanner");
    await waitFor(() => expect(within(scan).getByText("down")).toBeInTheDocument());
    expect(within(scan).queryAllByTestId("scanner-row")).toHaveLength(0);
    await waitFor(() => expect(screen.getByTestId("command-center")).toHaveTextContent("Some figures are unavailable"));
  });
});
