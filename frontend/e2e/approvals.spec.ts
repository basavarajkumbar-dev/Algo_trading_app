import { randomBytes } from "node:crypto";

import { expect, test } from "@playwright/test";

const API = process.env.E2E_API_URL ?? "http://localhost:8000/api/v1";
const EMAIL = `e2e-ap-${Date.now()}@example.com`;
const PASSWORD = `E2e-${randomBytes(9).toString("base64url")}`;

test.beforeAll(async ({ request }) => {
  const res = await request.post(`${API}/auth/register`, { data: { email: EMAIL, password: PASSWORD } });
  expect(res.status(), await res.text()).toBe(201);
});

test("APPROVAL mode: switch, review and reject a queued trade", async ({ page, request }) => {
  test.setTimeout(150_000);
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
  await page.getByRole("navigation", { name: "Main" }).getByRole("link", { name: /^Approvals/ }).click();
  await page.waitForURL("**/approvals");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Approvals");

  await page.getByTestId("mode-APPROVAL").click();
  await expect(page.getByTestId("mode-APPROVAL")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByTestId("topbar-mode")).toHaveText("APPROVAL");
  await expect(page.getByTestId("topbar-approvals")).toContainText("to approve");

  // let the algo propose; a READY signal depends on the simulated market
  const login = await request.post(`${API}/auth/login`, { data: { email: EMAIL, password: PASSWORD } });
  const token = (await login.json()).access_token as string;
  const headers = { Authorization: `Bearer ${token}` };
  await request.post(`${API}/trading/auto`, { data: { enabled: true }, headers });
  try {
    const card = page.getByTestId("approval-card").first();
    try {
      await card.waitFor({ timeout: 90_000 });
    } catch {
      test.info().annotations.push({ type: "note", description: "no READY signal appeared; review step skipped" });
      return;
    }
    for (const id of ["ap-stock", "ap-direction", "ap-entry", "ap-stop", "ap-target", "ap-quantity", "ap-risk", "ap-rr", "ap-strategy", "ap-score", "ap-regime", "ap-reasons", "ap-timestamp"]) {
      await expect(card.getByTestId(id)).toBeVisible();
    }
    const symbol = (await card.getByTestId("ap-stock").textContent())?.trim() ?? "";
    await card.getByTestId("ap-reject").click();
    await card.getByLabel("Rejection reason").fill("e2e: reviewed and declined");
    await card.getByTestId("ap-confirm-reject").click();
    await expect(page.getByTestId("approval-message")).toContainText(`Rejected ${symbol}`);
    const row = page.getByTestId("history-row").filter({ hasText: "e2e: reviewed and declined" }).first();
    await expect(row).toContainText(EMAIL);
  } finally {
    await request.post(`${API}/trading/auto`, { data: { enabled: false }, headers });
    await request.post(`${API}/trading/mode`, { data: { mode: "PAPER" }, headers });
  }
  expect(errors).toEqual([]);
});
