import { describe, expect, it } from "vitest";
import {
  assembleE164,
  countryByIso,
  maskedPhone,
  PHONE_COUNTRIES,
} from "@/lib/phone";

describe("country-aware phone normalization", () => {
  it("normalizes a UK national trunk prefix to E.164", () => {
    expect(assembleE164(countryByIso("GB"), "07400 123456")).toEqual({
      ok: true,
      e164: "+447400123456",
      nationalDigits: "7400123456",
    });
  });

  it("normalizes valid India and Nigeria mobile numbers", () => {
    expect(assembleE164(countryByIso("IN"), "98765 43210")).toMatchObject({
      ok: true,
      e164: "+919876543210",
    });
    expect(assembleE164(countryByIso("NG"), "0801 234 5678")).toMatchObject({
      ok: true,
      e164: "+2348012345678",
    });
  });

  it("rejects an international prefix or a number invalid for the selected plan", () => {
    expect(assembleE164(countryByIso("GB"), "+44 7400 123456")).toEqual({
      ok: false,
      error: "Enter only the number after +44.",
    });
    expect(assembleE164(countryByIso("IN"), "12345")).toMatchObject({
      ok: false,
    });
  });

  it("offers comprehensive country metadata and masks confirmed numbers", () => {
    expect(PHONE_COUNTRIES.length).toBeGreaterThan(200);
    expect(countryByIso("GB")).toMatchObject({
      dialCode: "44",
      priority: true,
    });
    expect(countryByIso("IN")).toMatchObject({
      dialCode: "91",
      priority: true,
    });
    expect(maskedPhone("+919876543210", countryByIso("IN"))).toBe(
      "+91 •••••• 3210",
    );
  });
});
