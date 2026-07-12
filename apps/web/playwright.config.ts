import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false,
  workers: 2,
  retries: 0,
  reporter: "list",
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://localhost:3010",
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"] } },
    { name: "mobile-chromium", use: { ...devices["Pixel 7"] } },
  ],
  webServer: {
    command: "next dev -p 3010",
    url: "http://localhost:3010/api/health",
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
