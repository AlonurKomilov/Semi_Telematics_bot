/**
 * Alerts section registry.
 *
 * Each section is lazy-loaded so Vite emits a separate JS chunk —
 * AlertsResults is the biggest (the full queue render); the others
 * are small.  Persona-specific sections (LiveAckPanel,
 * SafetySummaryStrip, VehicleHealthSummary, EscalationStatusCard,
 * AccountAlertSummary, IncidentDrillInDrawer) register here as they
 * land.
 *
 * Section ids referenced here must match the ids in ``layouts.ts``;
 * the ``check:layout-coverage`` audit catches drift between them.
 */
import { lazy } from 'react';
import type { SectionRegistry } from '../_lib/types';
import type { AlertsSectionProps } from './sections/_shared/types';

// ``label`` now ALSO feeds the page-sections gear (the user-facing
// customisation menu), so labels name what the operator SEES, not which
// persona it was built for — the gear may one day offer a section across
// personas, and "Dispatcher live-ops panel" would read as someone else's
// furniture.  ``required`` sections survive every configuration tier:
// the queue IS the page, the header carries its identity + bulk actions,
// bulk_error is the persistent failure surface (hiding your own error
// channel is a footgun), and the drawer is invisible machinery that
// ?alertId= deep links and the row click both need.
export const ALERTS_SECTIONS: SectionRegistry<AlertsSectionProps> = {
  header: {
    Component: lazy(() => import('./sections/AlertsHeader')),
    label: 'Page header',
    required: true,
  },
  control_bar: {
    Component: lazy(() => import('./sections/AlertsControlBar')),
    label: 'Date-range control',
    required: true,
  },
  bulk_error: {
    Component: lazy(() => import('./sections/AlertsBulkError')),
    label: 'Bulk-action error banner',
    required: true,
  },
  results: {
    Component: lazy(() => import('./sections/AlertsResults')),
    label: 'Alerts queue',
    required: true,
  },
  live_ack_panel: {
    Component: lazy(() => import('./sections/LiveAckPanel')),
    label: 'Queue progress (pending · new · last ack)',
  },
  vehicle_health_summary: {
    Component: lazy(() => import('./sections/VehicleHealthSummary')),
    label: 'Open-alert counters (faults / health / fuel)',
  },
  safety_summary_strip: {
    Component: lazy(() => import('./sections/SafetySummaryStrip')),
    label: 'Safety events summary (today · week · coaching)',
  },
  escalation_status_card: {
    Component: lazy(() => import('./sections/EscalationStatusCard')),
    label: 'Escalation status (past-due + breached)',
  },
  account_alert_summary: {
    Component: lazy(() => import('./sections/AccountAlertSummary')),
    label: 'Alert volume (last 7 days)',
  },
  incident_drillin_drawer: {
    Component: lazy(() => import('./sections/IncidentDrillInDrawer')),
    label: 'Alert details drawer',
    required: true,
  },
};
