import { SurfaceLoginPage } from "@/components/surface-login-page";
import { createPhoneCountries } from "@/lib/phone";

const REGION_NAMES = new Intl.DisplayNames(["en"], { type: "region" });

function safeResearchNextPath(value: string | null): string | null {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  )
    return null;
  const url = new URL(value, "https://research.bumpabestie.invalid");
  if (url.origin !== "https://research.bumpabestie.invalid") return null;
  return [
    "/",
    "/questions",
    "/conversations",
    "/classifications",
    "/cohorts",
    "/reports",
    "/exports",
    "/consent",
  ].some((path) => url.pathname === path || url.pathname.startsWith(`${path}/`))
    ? `${url.pathname}${url.search}`
    : null;
}

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
