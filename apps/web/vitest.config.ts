import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import path from "node:path";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
      "@bumpabestie/web-foundation": path.resolve(
        __dirname,
        "../../packages/web-foundation/src/index.ts",
      ),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    exclude: ["e2e/**", "node_modules/**", ".next/**"],
    coverage: {
      provider: "v8",
      thresholds: {
        statements: 62,
        branches: 55,
        functions: 52,
        lines: 64,
      },
    },
  },
});
