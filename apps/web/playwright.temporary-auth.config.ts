import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.TEMPORARY_AUTH_E2E_BASE_URL;
if (!baseURL) {
  throw new Error("TEMPORARY_AUTH_E2E_BASE_URL is required");
}
const outputDir = process.env.TEMPORARY_AUTH_E2E_OUTPUT_DIR;
if (!outputDir) {
  throw new Error("TEMPORARY_AUTH_E2E_OUTPUT_DIR is required");
}

export default defineConfig({
  testDir: "./e2e",
  testMatch: "temporary-auth-live.spec.ts",
  outputDir,
  fullyParallel: false,
  workers: 1,
  retries: 0,
  reporter: "list",
  expect: { timeout: 15_000 },
  use: {
    baseURL,
    navigationTimeout: 30_000,
    // This gate submits a temporary credential. Never persist request bodies in
    // traces, videos, or screenshots, including on failure.
    trace: "off",
    screenshot: "off",
    video: "off",
    ...devices["Desktop Chrome"],
  },
});
