import { SurfaceLoginPage } from "@/components/surface-login-page";
import { safeAdminNextPath } from "@/lib/admin-navigation";
import { createPhoneCountries } from "@/lib/phone";

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
      allowedRoles={["operator", "superadmin"]}
      description="Use the approved mobile number attached to your platform operations role."
      homePath="/"
      nextPath={safeAdminNextPath(typeof next === "string" ? next : null)}
      phoneCountries={phoneCountries}
      surfaceName="Bumpa Bestie Admin"
    />
  );
}
