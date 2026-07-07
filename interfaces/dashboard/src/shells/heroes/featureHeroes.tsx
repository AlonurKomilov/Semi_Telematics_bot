/**
 * Feature hero registry — maps a route prefix to the feature-owned
 * topbar chip strip rendered while the operator is ON that feature.
 *
 * Feature-centric contract: the component lives in the FEATURE's
 * folder (src/features/<x>/<X>Hero.tsx) and must reuse the feature
 * page's react-query key + classification helpers, so the topbar
 * numbers are identical to the page's own counts by construction —
 * never a second computation of the same stat.  Keep each hero to
 * the FleetHero geometry (flex-1 min-w-0 strip of HeroChips) so the
 * topbar height never changes.
 *
 * Matching is prefix-based ('/maintenance' also covers
 * '/maintenance/anything'); first match wins, so list more-specific
 * prefixes before shorter ones if two ever overlap.
 */
import type React from 'react';
import MaintenanceHero from '../../features/maintenance/MaintenanceHero';
import ApplicationsHero from '../../features/applications/ApplicationsHero';
import TeamHero from '../../features/settings/TeamHero';

export const FEATURE_HEROES: Array<{
  prefix: string;
  Hero: React.ComponentType;
}> = [
  { prefix: '/maintenance', Hero: MaintenanceHero },
  { prefix: '/workforce/applications', Hero: ApplicationsHero },
  { prefix: '/team', Hero: TeamHero },
];

export function matchFeatureHero(pathname: string): React.ComponentType | null {
  const entry = FEATURE_HEROES.find(
    e => pathname === e.prefix || pathname.startsWith(e.prefix + '/'),
  );
  return entry?.Hero ?? null;
}
