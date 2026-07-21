import { SurfaceLoginPage } from "@/components/surface-login-page";
import { createPhoneCountries } from "@/lib/phone";

const REGION_NAMES = new Intl.DisplayNames(["en"], { type: "region" });

function safeAdminNextPath(value: string | null): string | null {
  if (
    !value ||
    !value.startsWith("/") ||
    value.startsWith("//") ||
    value.includes("\\")
  )
    return null;
  const url = new URL(value, "https://admin.bumpabestie.invalid");
  if (url.origin !== "https://admin.bumpabestie.invalid") return null;
  return [
    "/",
    "/tenants",
    "/administrators",
    "/connections",
    "/sync-runs",
    "/failures",
    "/provider-failures",
    "/usage",
    "/onboarding",
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
      allowedRoles={["operator", "superadmin"]}
      description="Use the approved mobile number attached to your platform operations role."
      homePath="/"
      nextPath={safeAdminNextPath(typeof next === "string" ? next : null)}
      phoneCountries={phoneCountries}
      surfaceName="Bumpa Bestie Admin"
    />
  );
}
