// @vitest-environment jsdom
import { fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import BacktestingPage from "@/app/(dashboard)/backtesting/page";
import type { BacktestOptions, RunDetail, RunList } from "@/types/backtest";

import optionsJson from "../fixtures/backtest-options.json";
import runJson from "../fixtures/backtest-run.json";
import runsJson from "../fixtures/backtest-runs.json";
import tradesJson from "../fixtures/backtest-trades.json";
import equityJson from "../fixtures/backtest-equity.json";
import { calledPaths, clone, mockApi, renderWithProviders } from "../render";

const auth = vi.hoisted(() => ({ role: "TRADER" }));
vi.mock("@/components/auth/auth-provider", () => ({
  useAuth: () => ({
    accessToken: "token",
    user: { email: "t@example.com", role: auth.role },
    status: "authenticated",
    logout: vi.fn(),
  }),
}));
vi.mock("next/navigation", () => ({ usePathname: () => "/backtesting" }));

const options = optionsJson as unknown as BacktestOptions;
const run = runJson as unknown as RunDetail;
const runs = runsJson as unknown as RunList;

function backtestRoutes(overrides: Record<string, unknown> = {}) {
  return {
    "/backtests/options": options,
    "/backtests": runs,
    [`/backtests/${run.id}`]: run,
    [`/backtests/${run.id}/trades`]: tradesJson,
    [`/backtests/${run.id}/equity`]: equityJson,
    ...overrides,
  };
}

beforeEach(() => {
  auth.role = "TRADER";
});

describe("backtesting page (integration)", () => {
  it("lists stored runs and opens the newest with its headline metrics", async () => {
    mockApi(backtestRoutes());
    renderWithProviders(<BacktestingPage />);
    const table = await screen.findByTestId("bt-runs");
    expect(within(table).getAllByTestId("bt-run-row")).toHaveLength(runs.items.length);

    const metrics = await screen.findByTestId("bt-metrics");
    const m = run.metrics!;
    expect(within(metrics).getByTestId("bt-m-trades")).toHaveTextContent(String(m.total_trades));
    expect(within(metrics).getByTestId("bt-m-winrate")).toHaveTextContent(`${m.win_rate!.toFixed(1)}%`);
    expect(within(metrics).getByTestId("bt-m-pf")).toHaveTextContent(m.profit_factor!.toFixed(2));
    expect(within(metrics).getByTestId("bt-m-streaks")).toHaveTextContent(`${m.max_consecutive_wins}W`);
    for (const label of ["Expectancy", "Net P&L", "Max drawdown", "Average win", "Average loss", "Risk / reward", "Sharpe"]) {
      expect(within(metrics).getByText(label)).toBeInTheDocument();
    }
    expect(screen.getByTestId("bt-primary-label")).toHaveTextContent(run.result!.primary_label);
  });

  it("always shows the no-guarantee note, data warnings and robustness checks", async () => {
    mockApi(backtestRoutes());
    renderWithProviders(<BacktestingPage />);
    expect(await screen.findByTestId("bt-note")).toHaveTextContent("do not predict or guarantee future performance");
    expect(screen.getByTestId("bt-warnings")).toHaveTextContent("Synthetic data");
    const flags = screen.getByTestId("bt-flags");
    for (const f of run.robustness!.flags) expect(flags).toHaveTextContent(f);
    const rob = screen.getByTestId("bt-robustness");
    expect(within(rob).getByText("Monte Carlo net P&L")).toBeInTheDocument();
    expect(within(rob).getByText("Net with charges doubled")).toBeInTheDocument();
    expect(within(rob).getByText("95% interval of mean R")).toBeInTheDocument();
  });

  it("draws the equity curve and drawdown from the equity endpoint", async () => {
    const fetchMock = mockApi(backtestRoutes());
    renderWithProviders(<BacktestingPage />);
    await screen.findByTestId("bt-equity");
    await waitFor(() => expect(calledPaths(fetchMock)).toContain(`/backtests/${run.id}/equity`));
    expect(screen.getByText("Drawdown")).toBeInTheDocument();
  });

  it("shows the validation: segments, the selection grid and why a set was chosen", async () => {
    mockApi(backtestRoutes());
    renderWithProviders(<BacktestingPage />);
    await screen.findByTestId("bt-metrics");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Validation" }));
    const segments = await screen.findByTestId("bt-segments");
    expect(within(segments).getAllByRole("row")).toHaveLength(1 + run.result!.segments.length);
    const grid = screen.getByTestId("bt-grid");
    expect(grid).toHaveTextContent("Profit alone is not the criterion");
    expect(within(grid).getAllByRole("row")).toHaveLength(1 + run.result!.grid.length);
    expect(grid.querySelectorAll('[data-selected="true"]')).toHaveLength(run.result!.grid.filter((g) => g.selected).length);
    if (run.result!.mode === "OUT_OF_SAMPLE") {
      expect(screen.getByTestId("bt-degradation")).toBeInTheDocument();
    }
  });

  it("lists every trade with its exit reason", async () => {
    mockApi(backtestRoutes());
    renderWithProviders(<BacktestingPage />);
    await screen.findByTestId("bt-metrics");
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Trades" }));
    const table = await screen.findByTestId("bt-trades");
    await waitFor(() =>
      expect(within(table).getAllByTestId("bt-trade-row")).toHaveLength(Math.min(100, tradesJson.items.length))
    );
  });

  it("shows progress for a running backtest instead of results", async () => {
    const running = { ...clone(run), status: "RUNNING", progress: 0.42, message: "Signals: 2/5 symbols", metrics: null, robustness: null, result: null };
    const list = clone(runs);
    list.items[0] = { ...list.items[0]!, status: "RUNNING", progress: 0.42, message: "Signals: 2/5 symbols", headline: null };
    mockApi(backtestRoutes({ [`/backtests/${run.id}`]: running, "/backtests": list }));
    renderWithProviders(<BacktestingPage />);
    expect(await screen.findByTestId("bt-progress")).toHaveTextContent("Signals: 2/5 symbols · 42%");
    expect(screen.queryByTestId("bt-metrics")).not.toBeInTheDocument();
  });

  it("offers every input the brief asks for and posts the request", async () => {
    let posted: unknown = null;
    const fetchMock = mockApi(backtestRoutes());
    // POST /backtests answers with a queued run
    fetchMock.mockImplementation(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input));
      const path = url.pathname.replace(/^\/api\/v1/, "");
      const routes = backtestRoutes() as Record<string, unknown>;
      if (path === "/backtests" && init?.method === "POST") {
        posted = JSON.parse(String(init.body));
        return new Response(JSON.stringify({ ...runs.items[0], status: "QUEUED", estimated_seconds: 30 }), { status: 202 });
      }
      return new Response(JSON.stringify(routes[path] ?? { detail: "missing" }), { status: routes[path] ? 200 : 404 });
    });
    renderWithProviders(<BacktestingPage />);
    fireEvent.mouseDown(await screen.findByTestId("bt-tab-new"));
    const form = await screen.findByTestId("backtest-form");
    for (const label of ["Strategy", "Version", "Data source", "Timeframe"]) {
      expect(within(form).getByLabelText(label)).toBeInTheDocument();
    }
    expect(within(form).getByTestId("bt-start")).toBeInTheDocument();
    expect(within(form).getByTestId("bt-end")).toBeInTheDocument();
    expect(within(form).getByTestId("bt-capital")).toBeInTheDocument();
    expect(within(form).getByTestId("bt-slippage")).toBeInTheDocument();
    expect(within(form).getByText("Risk per trade (%)")).toBeInTheDocument();

    // strategy → its parameters and its version
    fireEvent.change(within(form).getByLabelText("Strategy"), { target: { value: "TREND_CONTINUATION" } });
    expect(within(form).getByTestId("param-min_pullback_percent")).toBeInTheDocument();
    expect(within(form).getByTestId("bt-version-match")).toHaveTextContent("1.0.0+");
    fireEvent.change(within(form).getByLabelText("Min pullback %"), { target: { value: "35" } });
    expect(within(form).getByTestId("bt-version-match")).toHaveTextContent("new version");

    // a grid and out-of-sample mode
    fireEvent.change(within(form).getByLabelText("Min signal score grid values"), { target: { value: "55, 65, 75" } });
    expect(within(form).getByTestId("bt-grid-count")).toHaveTextContent("3 combinations");
    fireEvent.click(within(form).getByRole("radio", { name: "Out-of-sample" }));
    expect(within(form).getByTestId("bt-mode-help")).toHaveTextContent("in-sample part only");

    // symbols
    fireEvent.click(within(form).getByRole("button", { name: "SBIN" }));
    expect(within(form).getByTestId("bt-symbol-count")).toHaveTextContent("6");

    fireEvent.click(within(form).getByTestId("bt-run"));
    await waitFor(() => expect(posted).not.toBeNull());
    const body = posted as Record<string, unknown>;
    expect(body.mode).toBe("OUT_OF_SAMPLE");
    expect(body.strategy).toBe("TREND_CONTINUATION");
    expect(body.grid).toEqual({ min_score: [55, 65, 75] });
    expect((body.parameters as Record<string, unknown>).min_pullback_percent).toBe(35);
    expect(body.version).toBeNull();
    expect(body.symbols).toContain("SBIN");
    // back on the runs tab after queueing
    expect(await screen.findByTestId("bt-runs")).toBeInTheDocument();
  });

  it("refuses walk-forward without a grid, and viewers cannot run", async () => {
    auth.role = "VIEWER";
    mockApi(backtestRoutes());
    renderWithProviders(<BacktestingPage />);
    fireEvent.mouseDown(await screen.findByTestId("bt-tab-new"));
    const form = await screen.findByTestId("backtest-form");
    fireEvent.click(within(form).getByRole("radio", { name: "Walk-forward" }));
    expect(within(form).getByTestId("bt-form-error")).toHaveTextContent("Walk-forward selects among grid values");
    expect(within(form).getByTestId("bt-run")).toBeDisabled();
    expect(within(form).getByText(/needs the TRADER or ADMIN role/)).toBeInTheDocument();
  });

  it("says so when there are no runs yet", async () => {
    mockApi(backtestRoutes({ "/backtests": { items: [], total: 0, queued: 0, note: "" } }));
    renderWithProviders(<BacktestingPage />);
    expect(await screen.findByTestId("bt-runs-empty")).toBeInTheDocument();
  });
});
