import { describe, expect, it } from "vitest";
import {
  countValues,
  durationBetween,
  maskPhone,
  titleCase,
} from "@/lib/platform-data";

describe("platform data formatting", () => {
  it("humanises API enum values", () => {
    expect(titleCase("inventory_management")).toBe("Inventory Management");
    expect(titleCase("sales.total_sales")).toBe("Sales · Total Sales");
    expect(titleCase(null)).toBe("Not available");
  });

  it("masks phone identities without losing their country prefix", () => {
    expect(maskPhone("+2348030001442")).toBe("+23480 ••• 1442");
  });

  it("derives durations and sorted distributions", () => {
    expect(
      durationBetween("2026-07-12T10:00:00Z", "2026-07-12T10:01:05Z"),
    ).toBe("1m 5s");
    expect(countValues(["web", "wa", "web"], (value) => value)).toEqual([
      ["web", 2],
      ["wa", 1],
    ]);
  });
});
