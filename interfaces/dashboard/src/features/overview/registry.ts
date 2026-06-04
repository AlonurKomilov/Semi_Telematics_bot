/**
 * Overview section registry.
 *
 * All 8 sections are lazy-loaded so personas that don't include a
 * section in their layout never download the chunk.  The most
 * obvious savings: Owner / Admin layouts include
 * ``OverviewOnboarding`` but Fleet / Dispatch / Safety don't, so
 * those personas skip the onboarding-banner chunk entirely.
 *
 * Note: ``DriverOverview`` is intentionally NOT in this registry —
 * the driver path is a single-component view, not a per-section
 * composition.  The Overview page wrapper picks DriverOverview
 * before falling through to PageLayoutHost for non-driver personas.
 */
import { lazy } from 'react';
import type { SectionRegistry } from '../_lib/types';
import type { OverviewSectionProps } from './sections/_shared/types';

export const OVERVIEW_SECTIONS: SectionRegistry<OverviewSectionProps> = {
  onboarding: {
    Component: lazy(() => import('./sections/OverviewOnboarding')),
    label: 'Onboarding banner',
  },
  greeting: {
    Component: lazy(() => import('./sections/OverviewGreeting')),
    label: 'Greeting',
  },
  header: {
    Component: lazy(() => import('./sections/OverviewHeader')),
    label: 'Page header',
  },
  alert_strip: {
    Component: lazy(() => import('./sections/OverviewAlertStrip')),
    label: 'Action-needed alert strip',
  },
  ai_summary: {
    Component: lazy(() => import('./sections/OverviewAISummary')),
    label: 'AI narrative',
  },
  status_grid: {
    Component: lazy(() => import('./sections/OverviewStatusGrid')),
    label: 'Movement-status grid',
  },
  status_chart: {
    Component: lazy(() => import('./sections/OverviewStatusChart')),
    label: 'Status distribution chart',
  },
  kpi_grid: {
    Component: lazy(() => import('./sections/OverviewKpiGrid')),
    label: 'Drill-down KPIs',
  },
};
