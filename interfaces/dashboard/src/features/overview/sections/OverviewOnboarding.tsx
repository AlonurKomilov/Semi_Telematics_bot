/**
 * Fresh-tenant onboarding nudge.
 *
 * Only rendered for Owner / Admin personas (layout-gated, so this
 * section's chunk is never even downloaded for Fleet/Dispatch/Safety
 * users).  The OnboardingBanner component itself decides whether to
 * show based on how many vehicles + users the account has — a fully
 * provisioned account shows nothing and the section silently no-ops.
 */
import { OnboardingBanner } from '../../../components/shell';
import type { OverviewSectionProps } from './_shared/types';

export default function OverviewOnboarding({ stats }: OverviewSectionProps) {
  const vehicleTotal = (stats.vehicles ?? stats.fleet)?.total ?? 0;
  return (
    <OnboardingBanner
      vehicleCount={vehicleTotal}
      userCount={1 /* TODO: wire real count when /admin/users is preloaded */}
    />
  );
}
