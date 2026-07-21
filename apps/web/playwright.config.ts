import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: "temporary-auth-live.spec.ts",
  snapshotPathTemplate:
    "{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}-{platform}{ext}",
  fullyParallel: false,
  workers: process.env.CI ? 1 : 2,
  retries: 0,
  reporter: "list",
  expect: { timeout: 10_000 },
  use: {
    baseURL: "http://localhost:3010",
    navigationTimeout: 30_000,
    trace: "retain-on-failure",
  },
  projects: [
    {
      name: "desktop-chromium",
      use: {
        ...devices["Desktop Chrome"],
        viewport: { width: 1440, height: 900 },
      },
    },
    {
      name: "mobile-chromium",
      use: {
        ...devices["iPhone 13"],
        viewport: { width: 390, height: 844 },
      },
    },
  ],
  webServer: {
    command: "./e2e/start-server.sh",
    url: "http://localhost:3010/api/health",
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
