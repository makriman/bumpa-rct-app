import { describe, expect, it } from "vitest";

import { generateImage } from "./managed-image";
import { timeoutValue, workspacePath } from "./security";

describe("sandbox request boundaries", () => {
  it("allows only normalized paths inside /workspace", () => {
    expect(workspacePath("/workspace")).toBe("/workspace");
    expect(workspacePath("/workspace/reports/result.csv")).toBe(
      "/workspace/reports/result.csv",
    );
    expect(() => workspacePath("/etc/passwd")).toThrow("invalid_path");
    expect(() => workspacePath("/workspace/../etc/passwd")).toThrow(
      "invalid_path",
    );
    expect(() => workspacePath("/workspace/./secret")).toThrow("invalid_path");
  });

  it("clamps every execution to the hard runtime budget", () => {
    expect(timeoutValue({ timeout_ms: 50 })).toBe(1_000);
    expect(timeoutValue({ timeout_ms: 5_000 })).toBe(5_000);
    expect(timeoutValue({ timeout_ms: 600_000 })).toBe(30_000);
    expect(timeoutValue({ timeout_ms: "30000" })).toBe(30_000);
  });

  it("keeps generated image bytes behind the audited managed-provider response", async () => {
    const ai = {
      run: async () => ({ image: "cG5nLWZpeHR1cmU=" }),
    } as unknown as Ai;

    await expect(generateImage(ai, { prompt: "Create a shop poster" })).resolves.toEqual({
      image_base64: "cG5nLWZpeHR1cmU=",
      mime_type: "image/png",
      model: "@cf/black-forest-labs/flux-1-schnell",
      untrusted_content: true,
    });
    await expect(generateImage(ai, { prompt: " " })).rejects.toThrow("invalid_prompt");
  });
});
