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

export const ALERTS_SECTIONS: SectionRegistry<AlertsSectionProps> = {
  header: {
    Component: lazy(() => import('./sections/AlertsHeader')),
    label: 'Page header + bulk-ack action',
  },
  control_bar: {
    Component: lazy(() => import('./sections/AlertsControlBar')),
    label: 'View-mode + date-range control bar',
  },
  filter_chips: {
    Component: lazy(() => import('./sections/AlertsFilterChips')),
    label: 'Filter chips + vehicle search',
  },
  bulk_error: {
    Component: lazy(() => import('./sections/AlertsBulkError')),
    label: 'Bulk-action error banner',
  },
  results: {
    Component: lazy(() => import('./sections/AlertsResults')),
    label: 'Alerts queue (grouping is the operator\'s choice)',
  },
  pagination: {
    Component: lazy(() => import('./sections/AlertsPagination')),
    label: 'Pagination footer',
  },
  live_ack_panel: {
    Component: lazy(() => import('./sections/LiveAckPanel')),
    label: 'Dispatcher live-ops panel (sound, delta, last ack)',
  },
  vehicle_health_summary: {
    Component: lazy(() => import('./sections/VehicleHealthSummary')),
    label: 'Fleet hero strip (faults / health / fuel)',
  },
  safety_summary_strip: {
    Component: lazy(() => import('./sections/SafetySummaryStrip')),
    label: 'Safety hero strip (events today/week + coaching backlog)',
  },
  escalation_status_card: {
    Component: lazy(() => import('./sections/EscalationStatusCard')),
    label: 'Owner/admin escalation oversight (past-due + breached)',
  },
  account_alert_summary: {
    Component: lazy(() => import('./sections/AccountAlertSummary')),
    label: 'Owner/admin 7-day alert-volume histogram',
  },
  incident_drillin_drawer: {
    Component: lazy(() => import('./sections/IncidentDrillInDrawer')),
    label: 'Alert details drawer (slides over)',
  },
};
