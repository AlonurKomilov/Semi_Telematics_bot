/**
 * Overview per-persona layouts.
 *
 * The top-level section order is largely uniform across non-driver
 * personas — every persona benefits from greeting, header,
 * action-needed strip, AI narrative, status grid + chart, drill-down
 * KPIs.  The persona-specific tuning lives **inside** sections:
 *
 *   - OverviewHeader pulls title/description from a persona map.
 *   - OverviewKpiGrid orders the drill-down KPIs via a per-persona
 *     priority array, hiding KPIs the persona doesn't care about.
 *
 * The one section that's truly conditional on persona at the layout
 * level is ``onboarding`` — fresh-tenant onboarding nudge only makes
 * sense for the account-administrator personas (Owner / Admin); a
 * Fleet manager doesn't provision the account.
 *
 * The Driver persona is NOT in this map — the Overview page wrapper
 * picks ``DriverOverview`` as a single component before considering
 * PageLayoutHost.  Drivers have a totally different page shape.
 *
 * HR and Accounting fall back to owner-superset; Phase 5 will tune
 * once the personas ship.
 */
import type { LayoutMap } from '../_lib/types';

export const OVERVIEW_LAYOUTS: LayoutMap = {
  // Owner / Admin get the onboarding banner at the top (silent no-op
  // for accounts that already have vehicles + users provisioned).
  owner: [
    'onboarding',
    'greeting',
    'header',
    'alert_strip',
    'ai_summary',
    'status_grid',
    'status_chart',
    'kpi_grid',
  ],
  admin: [
    'onboarding',
    'greeting',
    'header',
    'alert_strip',
    'ai_summary',
    'status_grid',
    'status_chart',
    'kpi_grid',
  ],

  // Operational personas skip onboarding.  Same section set, same
  // order; persona-specific copy / KPI priority lives inside the
  // sections themselves.
  fleet: [
    'greeting',
    'header',
    'alert_strip',
    'ai_summary',
    'status_grid',
    'status_chart',
    'kpi_grid',
  ],
  dispatcher: [
    'greeting',
    'header',
    'alert_strip',
    'ai_summary',
    'status_grid',
    'status_chart',
    'kpi_grid',
  ],
  safety: [
    'greeting',
    'header',
    'alert_strip',
    'ai_summary',
    'status_grid',
    'status_chart',
    'kpi_grid',
  ],

  // Driver is intentionally absent — the page wrapper renders
  // DriverOverview as a single component before any layout lookup.

  // HR — people-management overview.  Drops status_grid +
  // status_chart (vehicle-ops display HR doesn't action) and the
  // kpi_grid (its drill-downs are vehicle / cost focused).  Keeps
  // greeting + header + alert strip + AI narrative — the strip
  // surfaces driver-relevant alerts (pending alerts queue, unsafe
  // parking can affect HR coaching) and the AI narrative is
  // persona-tuned via useShellConfig internally.
  hr: ['greeting', 'header', 'alert_strip', 'ai_summary'],

  // Accounting — financial overview.  Drops status_grid +
  // status_chart + kpi_grid (vehicle-ops display) for the same
  // reason as HR.  Keeps greeting + header + AI narrative.
  // Future product work: add a Tier-3 CostsAtAGlance section
  // (fuel MTD / CPM trend / unpaid invoices) that slots between
  // ai_summary and the rest — when it lands, the layout becomes
  // ['greeting', 'header', 'ai_summary', 'costs_at_a_glance'].
  accounting: ['greeting', 'header', 'ai_summary'],
};
