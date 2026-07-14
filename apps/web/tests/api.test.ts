import { afterEach, describe, expect, it, vi } from "vitest";
import { apiRequest } from "@/lib/api";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("apiRequest", () => {
  it.each([204, 205])(
    "treats no-content mutation status %i as successful",
    async (status) => {
      const request = vi
        .spyOn(globalThis, "fetch")
        .mockResolvedValue(new Response(null, { status }));

      await expect(
        apiRequest<void>("/admin/platform-access/admin-other/operator", {
          method: "DELETE",
        }),
      ).resolves.toBeUndefined();
      expect(request).toHaveBeenCalledTimes(1);
    },
  );
});
