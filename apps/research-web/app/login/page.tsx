import { SurfaceLoginPage } from "@/components/surface-login-page";
import { createPhoneCountries } from "@/lib/phone";
import { safeResearchNextPath } from "@/lib/research-navigation";

const REGION_NAMES = new Intl.DisplayNames(["en"], { type: "region" });

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string | string[] }>;
}) {
  const phoneCountries = createPhoneCountries(
    (iso) => REGION_NAMES.of(iso) ?? iso,
  );
  const next = (await searchParams).next;
  return (
    <SurfaceLoginPage
      allowedRoles={["researcher", "superadmin"]}
      description="Use the approved mobile number attached to your consented research role."
      homePath="/"
      nextPath={safeResearchNextPath(typeof next === "string" ? next : null)}
      phoneCountries={phoneCountries}
      surfaceName="Bumpa Bestie Research"
    />
  );
}
