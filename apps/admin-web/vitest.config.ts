import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

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
    testTimeout: 15_000,
    environment: "jsdom",
    setupFiles: ["./tests/setup.ts"],
    exclude: ["node_modules/**", ".next/**", "e2e/**"],
  },
});
