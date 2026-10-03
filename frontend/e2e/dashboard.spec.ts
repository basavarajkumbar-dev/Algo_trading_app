import { randomBytes } from "node:crypto";

import { expect, test, type Page } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://localhost:8000/api/v1";
// A throwaway account per run; nothing is hardcoded.
const EMAIL = `e2e-${Date.now()}@example.com`;
const PASSWORD = `E2e-${randomBytes(9).toString("base64url")}`;

const PAGES: [string, string, RegExp][] = [
  ["Dashboard", "/", /Command Center/],
  ["Market", "/market", /Market Intelligence/],
  ["Scanner", "/scanner", /Scanner/],
  ["Signals", "/signals", /Signals/],
  ["Positions", "/positions", /Positions/],
  ["Trades", "/trades", /Trades/],
  ["Analytics", "/analytics", /Analytics/],
  ["Strategies", "/strategies", /Strategies/],
  ["Backtesting", "/backtesting", /Backtesting/],
  ["Risk", "/risk", /Risk/],
  ["System", "/system", /System/],
  ["Settings", "/settings", /Settings/],
];

test.beforeAll(async ({ request }) => {
  const res = await request.post(`${API}/auth/register`, { data: { email: EMAIL, password: PASSWORD } });
  expect(res.status(), await res.text()).toBe(201);
});

async function login(page: Page) {
  await page.goto("/login");
  await page.fill("input[type=email]", EMAIL);
  await page.fill("input[type=password]", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL("/");
}

function watchErrors(page: Page): string[] {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });
  return errors;
}

test("every sidebar section opens without errors", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  const nav = page.getByRole("navigation", { name: "Main" });
  for (const [label, path, heading] of PAGES) {
    await nav.getByRole("link", { name: new RegExp(`^${label}`) }).click();
    await page.waitForURL(path === "/" ? "/" : `**${path}`);
    await expect(page.getByRole("heading", { level: 1 }).first()).toHaveText(heading);
    await expect(nav.getByRole("link", { name: new RegExp(`^${label}`) })).toHaveAttribute("aria-current", "page");
  }
  expect(errors).toEqual([]);
});

test("the command center is live over the WebSocket and filled from the APIs", async ({ page }) => {
  const errors = watchErrors(page);
  await login(page);
  await expect(page.getByTestId("socket-status")).toHaveText("Live");

  // NIFTY arrives over the socket and keeps moving (the feed ticks every second)
  const nifty = page.getByTestId("topbar-nifty");
  await expect(nifty).not.toHaveText("—");
  const first = await nifty.textContent();
  await expect.poll(async () => nifty.textContent(), { timeout: 20_000 }).not.toBe(first);

  await expect(page.getByTestId("topbar-mode")).toHaveText("PAPER");
  await expect(page.getByTestId("topbar-health")).toHaveText(/HEALTHY|WARNING|CRITICAL/);
  await expect(page.getByTestId("cc-trades")).not.toContainText("—");
  await expect(page.getByTestId("market-brain")).toContainText("Market regime");
  await expect(page.getByTestId("live-scanner").getByTestId("scanner-row").first()).toBeVisible();
  await expect(page.getByTestId("system-health-list").getByTestId("health-database")).toBeVisible();
  await expect(page.getByTestId("active-position")).toBeVisible();
  expect(errors).toEqual([]);
});

test("system page reports every component and the broker", async ({ page }) => {
  await login(page);
  await page.goto("/system");
  for (const key of ["market_data", "backend", "database", "redis", "websocket", "strategy_engine", "risk_engine", "paper_broker", "live_broker"]) {
    await expect(page.getByTestId(`component-${key}`)).toContainText(/HEALTHY|WARNING|CRITICAL/);
  }
  await expect(page.getByTestId("browser-socket")).toContainText("open");
  // no credentials in this stack: orders stay on the paper broker, live orders disabled
  await expect(page.getByTestId("broker-execution")).toContainText("PAPER");
  await expect(page.getByTestId("broker-live-orders")).toContainText("disabled");
  await expect(page.getByTestId("broker-blockers")).toContainText("LIVE_MODE_ENABLED");
});

test("automatic paper trading can be switched on and off from Positions", async ({ page }) => {
  await login(page);
  await page.goto("/positions");
  const on = page.getByRole("button", { name: "Turn automatic trading on" });
  const off = page.getByRole("button", { name: "Turn automatic trading off" });
  await expect(on.or(off)).toBeVisible();
  if (await off.isVisible()) await off.click(); // start from off
  await on.click();
  await expect(off).toBeVisible();
  await expect(page.getByTestId("topbar-algo")).not.toHaveText("Stopped");
  await off.click();
  await expect(on).toBeVisible();
});

test("notifications open from the top bar", async ({ page }) => {
  await login(page);
  await page.getByTestId("notifications-trigger").click();
  await expect(page.getByText("Notifications", { exact: true })).toBeVisible();
  await expect(page.getByRole("menuitem", { name: /System health and event log/ })).toBeVisible();
});
