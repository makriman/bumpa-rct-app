import { ResumableOnboarding } from "@/components/admin-onboarding";

export default async function Page({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  return <ResumableOnboarding onboardingId={id} />;
}
