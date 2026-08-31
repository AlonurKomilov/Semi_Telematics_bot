/**
 * "May this user open the page a tour teaches?" — the listing gate.
 *
 * A tour card is only shown when its FEATURE passes the same two
 * checks the catalog applies everywhere: the account has the module
 * enabled, and the role holds one of the feature's permission flags.
 * Browsing a tour for a page you cannot open isn't education, it's
 * advertising — the never-offer-what-the-server-refuses rule, applied
 * to a library.
 *
 * Pure and separate from generateNav on purpose: the sidebar also
 * curates by PERSONA (a recruiter's sidebar hides Maintenance even
 * when a cross-grant allows it) — a library must not, because "can I
 * open it" is the honest bar for "may I learn it".
 */
import { FEATURE_CATALOG, isPathModuleEnabled } from '../../config/featureCatalog';

export interface FeatureAccess {
  hasAny: (...flags: string[]) => boolean;
  enabledModules: string[] | undefined;
}

export function reachableFeature(
  featureId: string, access: FeatureAccess,
): { path: string; labelKey: string } | null {
  const f = FEATURE_CATALOG.find((c) => c.id === featureId);
  if (!f) return null;
  // A navHidden or service-kind entry is not a page a person browses
  // to — a tour for one may exist someday, but its card would promise
  // a destination the catalog itself says is not a destination.
  if (f.navHidden || f.kind === 'service') return null;
  if (!isPathModuleEnabled(f.path, access.enabledModules)) return null;
  const flags = f.permission == null ? []
    : Array.isArray(f.permission) ? f.permission : [f.permission];
  if (flags.length > 0 && !access.hasAny(...flags)) return null;
  return { path: f.path, labelKey: f.labelKey };
}
