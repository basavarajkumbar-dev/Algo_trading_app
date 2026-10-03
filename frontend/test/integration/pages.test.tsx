// @vitest-environment jsdom
import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import AnalyticsPage from "@/app/(dashboard)/analytics/page";
import RiskPage from "@/app/(dashboard)/risk/page";
import SystemPage from "@/app/(dashboard)/system/page";
import TradesPage from "@/app/(dashboard)/trades/page";
import type { ComponentHealthResponse, PerformanceReport } from "@/types/dashboard";

import components from "../fixtures/components.json";
import performance from "../fixtures/performance.json";
import trades from "../fixtures/trades.json";
import { clone, mockApi, renderWithProviders } from "../render";

vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({ accessToken: "token", user: { email: "t@example.com" }, status: "authenticated", logout: vi.fn() }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/" }));

describe("analytics page (integration)", () => {
  it("shows win rate, profit factor, drawdown and strategy performance from /analytics/performance", async () => {
    mockApi();
    renderWithProviders(<AnalyticsPage />);
    const s = (performance as unknown as PerformanceReport).summary;
    const summary = await screen.findByTestId("analytics-summary");
    expect(within(summary).getByTestId("an-winrate")).toHaveTextContent(`${s.win_rate!.toFixed(1)}%`);
    expect(within(summary).getByTestId("an-pf")).toHaveTextContent(s.profit_factor!.toFixed(2));
    expect(within(summary).getByTestId("an-dd")).toHaveTextContent(Math.abs(s.max_drawdown).toFixed(2));
    for (const title of ["Equity curve", "Drawdown", "P&L", "Strategy performance", "By exit reason"]) {
      expect(screen.getByText(title)).toBeInTheDocument();
    }
    const table = screen.getByTestId("strategy-performance");
    expect(within(table).getAllByRole("row")).toHaveLength(1 + performance.by_strategy.length);
  });

  it("shows an empty state, not invented charts, when no trade has closed", async () => {
    const empty = clone(performance) as unknown as PerformanceReport;
    empty.summary = { ...empty.summary, closed_trades: 0, wins: 0, losses: 0 };
    empty.equity_curve = [];
    empty.by_strategy = [];
    mockApi({ "/analytics/performance": empty });
    renderWithProviders(<AnalyticsPage />);
    expect(await screen.findByTestId("analytics-empty")).toBeInTheDocument();
    expect(screen.queryByText("Equity curve")).not.toBeInTheDocument();
  });
});

describe("system page (integration)", () => {
  it("shows that orders execute on the paper broker and why live trading is blocked", async () => {
    mockApi();
    renderWithProviders(<SystemPage />);
    const panel = await screen.findByTestId("broker-panel");
    await waitFor(() => expect(within(panel).getByTestId("broker-execution")).toHaveTextContent("PAPER"));
    expect(within(panel).getByTestId("broker-zerodha-state")).toHaveTextContent("not configured");
    expect(within(panel).getByTestId("broker-live-orders")).toHaveTextContent("disabled");
    const blockers = within(panel).getByTestId("broker-blockers");
    expect(blockers).toHaveTextContent("LIVE_MODE_ENABLED");
    expect(blockers).toHaveTextContent("not been armed");
    expect(screen.getByTestId("component-live_broker")).toHaveTextContent("HEALTHY");
  });

  it("lists all eight components with their level", async () => {
    mockApi();
    renderWithProviders(<SystemPage />);
    for (const key of ["market_data", "backend", "database", "redis", "websocket", "strategy_engine", "risk_engine", "paper_broker", "live_broker"]) {
      const card = await screen.findByTestId(`component-${key}`);
      expect(card).toHaveTextContent(/HEALTHY|WARNING|CRITICAL/);
    }
    expect(screen.getByTestId("system-overall")).toHaveTextContent(components.status);
    await waitFor(() => expect(screen.getByTestId("event-log")).toHaveTextContent(/order placed|risk rejected|auto trading/));
  });

  it("surfaces a critical component and the overall level", async () => {
    const bad = clone(components) as unknown as ComponentHealthResponse;
    bad.status = "CRITICAL";
    bad.components = bad.components.map((c) =>
      c.key === "database" ? { ...c, status: "CRITICAL", detail: "Unable to reach PostgreSQL" } : c
    );
    mockApi({ "/system/components": bad });
    renderWithProviders(<SystemPage />);
    const db = await screen.findByTestId("component-database");
    expect(db).toHaveTextContent("CRITICAL");
    expect(db).toHaveTextContent("Unable to reach PostgreSQL");
    expect(screen.getByTestId("system-overall")).toHaveTextContent("CRITICAL");
  });

  it("says the backend is unreachable instead of showing stale health", async () => {
    mockApi({ "/system/components": new Error("connection refused") });
    renderWithProviders(<SystemPage />);
    expect(await screen.findByText(/Backend unreachable/)).toBeInTheDocument();
    expect(screen.queryByTestId("component-database")).not.toBeInTheDocument();
  });
});

describe("trades and risk pages (integration)", () => {
  it("renders the complete trade table", async () => {
    mockApi();
    renderWithProviders(<TradesPage />);
    const table = await screen.findByTestId("trades-table");
    await waitFor(() => expect(within(table).getAllByTestId("trade-row")).toHaveLength(trades.items.length));
    for (const col of ["Opened", "Stock", "Side", "Strategy", "Qty", "Entry", "Exit", "SL", "Target", "Result", "Gross", "Charges", "Net P&L", "R", "Held"]) {
      expect(within(table).getByText(col)).toBeInTheDocument();
    }
  });

  it("shows daily loss, risk used, trades used, exposure, consecutive losses and system status", async () => {
    mockApi();
    renderWithProviders(<RiskPage />);
    const summary = await screen.findByTestId("risk-summary");
    await within(summary).findByText("Daily loss");
    for (const label of ["Daily loss", "Risk used (open trade)", "Trades used", "Exposure", "Consecutive losses", "System"]) {
      expect(within(summary).getByText(label)).toBeInTheDocument();
    }
  });
});
