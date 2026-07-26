/**
 * Alerts page — Pattern B composition.
 *
 * The wrapper does three things and stops:
 *   1. Provides AlertsSelectionContext so sections share selection +
 *      bulk-action state without prop threading.
 *   2. Mounts PageLayoutHost with the section registry + per-persona
 *      layouts.  PageLayoutHost looks up the current persona, picks
 *      its layout, and renders the listed sections via React.lazy.
 *   3. Passes an empty sectionProps object — every Alerts section is
 *      self-contained (filter state via URL params, selection via
 *      Context, query data via useAlertsQuery).
 *
 * No page-level useState, no filter logic, no query call.  The
 * sections take care of themselves.
 */
import { PageLayoutHost } from '../_lib/PageLayoutHost';
import { AlertsSelectionProvider } from './_shared/AlertsSelectionContext';
import AlertDeepLink from './_shared/AlertDeepLink';
import { ALERTS_SECTIONS } from './registry';
import { ALERTS_LAYOUTS } from './layouts';
import { AlertsTabs } from './AlertsTabs';

export default function Alerts() {
  return (
    <AlertsSelectionProvider>
      {/* Opens the drawer when arrived at via ?alertId= (the bell's rows).
          Lives here, not in a persona layout, so every role gets it. */}
      <AlertDeepLink />
      <AlertsTabs />
      <PageLayoutHost
        registry={ALERTS_SECTIONS}
        layouts={ALERTS_LAYOUTS}
        sectionProps={{}}
      />
    </AlertsSelectionProvider>
  );
}
