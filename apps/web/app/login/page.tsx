import LoginPage from "@/components/login-page";
import { createPhoneCountries } from "@/lib/phone";

export default function LoginRoute() {
  // Country names come from CLDR data bundled with the server runtime. Different
  // Node and browser ICU releases legitimately use different English labels for
  // a few regions. Resolve and order the list once on the server, then serialize
  // that exact value into the client component so hydration never recomputes it.
  const regionNames = new Intl.DisplayNames(["en"], { type: "region" });
  const phoneCountries = createPhoneCountries(
    (iso) => regionNames.of(iso) ?? iso,
  );

  return <LoginPage phoneCountries={phoneCountries} />;
}
