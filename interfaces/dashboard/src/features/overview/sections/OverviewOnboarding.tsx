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
import { useTeamMembersQuery } from '../../../hooks/useTeamMembers';
import { usePreference } from '../../../preferences/usePreference';
import type { OverviewSectionProps } from './_shared/types';

export default function OverviewOnboarding({ stats }: OverviewSectionProps) {
  const vehicleTotal = (stats.vehicles ?? stats.fleet)?.total ?? 0;

  // The banner hides itself once dismissed, so ask for the roster only
  // while it could still appear — a settled account pays for nothing.
  const { value: dismissed } = usePreference('onboarding.dismissed');
  const { data } = useTeamMembersQuery({ enabled: !dismissed });

  // `userCount` was hardcoded to 1. That is not just a wrong number
  // beside a real one: the team step is `done: userCount > 1`, and the
  // banner only disappears when every step is done — so an account with
  // twenty members was still being told to invite its team, and the
  // checklist could never retire itself. Nothing renders until the count
  // is known, because a checklist that appears for one frame and then
  // vanishes is worse than one that arrives late.
  if (dismissed || !data) return null;

  return (
    <OnboardingBanner
      vehicleCount={vehicleTotal}
      userCount={data.users.length}
    />
  );
}
