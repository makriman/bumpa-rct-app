import {
  getCountries,
  getCountryCallingCode,
  parsePhoneNumberFromString,
  type CountryCode,
} from "libphonenumber-js/min";

export type PhoneCountry = {
  iso: CountryCode;
  name: string;
  dialCode: string;
  example?: string;
  priority: boolean;
};

const TEAM_COUNTRIES = new Set<CountryCode>(["GB", "IN"]);
const EXAMPLES: Partial<Record<CountryCode, string>> = {
  GB: "7400 123456",
  IN: "98765 43210",
  NG: "801 234 5678",
  US: "202 555 0123",
};
const regionNames = new Intl.DisplayNames(["en"], { type: "region" });

/**
 * Comprehensive ISO country metadata from libphonenumber-js. Current team
 * regions are surfaced first in the selector while every supported dialing
 * plan remains available without maintaining a second country-code table.
 */
export const PHONE_COUNTRIES: readonly PhoneCountry[] = getCountries()
  .map((iso) => ({
    iso,
    name: regionNames.of(iso) ?? iso,
    dialCode: getCountryCallingCode(iso),
    example: EXAMPLES[iso],
    priority: TEAM_COUNTRIES.has(iso),
  }))
  .toSorted((a, b) => a.name.localeCompare(b.name));

export type PhoneAssemblyResult =
  | { ok: true; e164: string; nationalDigits: string }
  | { ok: false; error: string };

export function countryByIso(iso: string): PhoneCountry {
  return (
    PHONE_COUNTRIES.find((country) => country.iso === iso) ??
    PHONE_COUNTRIES.find((country) => country.iso === "GB") ??
    PHONE_COUNTRIES[0]
  );
}

export function assembleE164(
  country: PhoneCountry,
  nationalInput: string,
): PhoneAssemblyResult {
  const trimmed = nationalInput.trim();
  if (!trimmed) {
    return { ok: false, error: "Enter your mobile number." };
  }
  if (trimmed.includes("+")) {
    return {
      ok: false,
      error: `Enter only the number after +${country.dialCode}.`,
    };
  }
  if (!/^[\d\s().-]+$/.test(trimmed)) {
    return {
      ok: false,
      error: "Use numbers only; spaces, brackets and hyphens are fine.",
    };
  }

  const parsed = parsePhoneNumberFromString(trimmed, country.iso);
  if (!parsed || !parsed.isPossible() || !parsed.isValid()) {
    return {
      ok: false,
      error: `Enter a valid ${country.name} mobile number after +${country.dialCode}.`,
    };
  }
  if (!/^\+[1-9]\d{7,14}$/.test(parsed.number)) {
    return { ok: false, error: "Enter a valid international mobile number." };
  }

  return {
    ok: true,
    e164: parsed.number,
    nationalDigits: parsed.nationalNumber,
  };
}

export function maskedPhone(e164: string, country: PhoneCountry): string {
  return `+${country.dialCode} •••••• ${e164.slice(-4)}`;
}
