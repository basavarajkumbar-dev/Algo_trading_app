import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a running stack (backend + `next start`).
 * They do not start servers themselves: run `docker compose up` (or the
 * backend and `npm start`) first, then `npm run test:e2e`.
 *
 *   E2E_BASE_URL  frontend URL (default http://localhost:3000)
 *   E2E_API_URL   backend API URL (default http://localhost:8000/api/v1)
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 15_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    viewport: { width: 1600, height: 1000 },
    trace: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1600, height: 1000 } } }],
});
