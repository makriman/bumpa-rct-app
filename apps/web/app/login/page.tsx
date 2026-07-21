import LoginPage from "@/components/login-page";
import { safeConsumerNextPath } from "@/lib/consumer-navigation";
import { createPhoneCountries } from "@/lib/phone";

const REGION_NAMES = new Intl.DisplayNames(["en"], { type: "region" });

export default async function LoginRoute({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  // Country names come from CLDR data bundled with the server runtime. Different
  // Node and browser ICU releases legitimately use different English labels for
  // a few regions. Resolve and order the list once on the server, then serialize
  // that exact value into the client component so hydration never recomputes it.
  const phoneCountries = createPhoneCountries(
    (iso) => REGION_NAMES.of(iso) ?? iso,
  );

  const next = (await searchParams).next;
  const nextPath = safeConsumerNextPath(typeof next === "string" ? next : null);
  return <LoginPage phoneCountries={phoneCountries} nextPath={nextPath} />;
}
