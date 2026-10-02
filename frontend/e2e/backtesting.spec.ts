import { randomBytes } from "node:crypto";

import { expect, test } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://localhost:8000/api/v1";
const EMAIL = `e2e-bt-${Date.now()}@example.com`;
const PASSWORD = `E2e-${randomBytes(9).toString("base64url")}`;

test.beforeAll(async ({ request }) => {
  const res = await request.post(`${API}/auth/register`, { data: { email: EMAIL, password: PASSWORD } });
  expect(res.status(), await res.text()).toBe(201);
});

test("configure, run and read a small backtest end to end", async ({ page }) => {
  // waits behind any run already queued on the server
  test.setTimeout(600_000);
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  page.on("console", (m) => {
    if (m.type() === "error") errors.push(m.text());
  });

  await page.goto("/login");
  await page.fill("input[type=email]", EMAIL);
  await page.fill("input[type=password]", PASSWORD);
  await page.click("button[type=submit]");
  await page.waitForURL("/");
  // through the sidebar: a hard navigation would abort Next.js prefetches (console errors)
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /^Backtesting/ }).click();
  await page.waitForURL("**/backtesting");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Backtesting");

  await page.getByTestId("bt-tab-new").click();
  const form = page.getByTestId("backtest-form");
  await form.getByRole("button", { name: "Clear" }).click();
  await form.getByRole("button", { name: "SBIN", exact: true }).click();
  await expect(form.getByTestId("bt-symbol-count")).toHaveText("1");
  const end = await form.getByTestId("bt-end").inputValue();
  const start = new Date(`${end}T00:00:00Z`);
  start.setUTCDate(start.getUTCDate() - 6);
  await form.getByTestId("bt-start").fill(start.toISOString().slice(0, 10));
  const name = `e2e ${Date.now()}`;
  await form.getByLabel("Name (optional)").fill(name);
  await form.getByTestId("bt-run").click();

  // back on the runs tab; the run is queued, runs and completes
  const row = page.getByTestId("bt-run-row").filter({ hasText: name });
  await expect(row).toBeVisible();
  await expect(row).toHaveAttribute("data-status", "COMPLETED", { timeout: 540_000 });
  await row.click();
  await expect(page.getByTestId("bt-result-title")).toHaveText(name);
  await expect(page.getByTestId("bt-note")).toContainText("do not predict or guarantee future performance");
  await expect(page.getByTestId("bt-metrics")).toBeVisible();
  await expect(page.getByTestId("bt-robustness")).toBeVisible();

  await row.getByRole("button", { name: "Delete run" }).click();
  await expect(row).toHaveCount(0);
  expect(errors).toEqual([]);
});
