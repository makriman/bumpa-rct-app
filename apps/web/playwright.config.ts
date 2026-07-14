import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  testIgnore: "temporary-auth-live.spec.ts",
  snapshotPathTemplate:
    "{testDir}/__screenshots__/{testFilePath}/{arg}-{projectName}-{platform}{ext}",
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
    command:
      "npm run build && mkdir -p .next/standalone/public .next/standalone/.next/static && cp -R public/. .next/standalone/public/ && cp -R .next/static/. .next/standalone/.next/static/ && PORT=3010 HOSTNAME=127.0.0.1 node .next/standalone/server.js",
    url: "http://localhost:3010/api/health",
    env: { NEXT_PUBLIC_DEMO_MODE: "false" },
    reuseExistingServer: false,
    timeout: 180_000,
  },
});
