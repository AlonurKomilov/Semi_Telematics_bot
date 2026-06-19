/**
 * Alerts per-persona layouts.
 *
 * Today every persona renders the same 6 sections in header →
 * control_bar → filter_chips → bulk_error → results → pagination
 * order.  The persona value lives entirely in the URL filter
 * defaults that useAlertsFilters writes on first land (see
 * personaConfig.ts).
 *
 * Planned persona-specific additions — wire here when each section lands:
 *   • dispatcher → ``live_ack_panel`` before ``control_bar``
 *   • safety     → ``safety_summary_strip`` + ``incident_drillin_drawer``
 *   • fleet      → ``vehicle_health_summary``
 *   • owner/admin → ``escalation_status_card`` + ``account_alert_summary``
 *
 * Until those land the layouts stay uniform; the audit script
 * (check:layout-coverage) keeps the keys in sync with the Persona union.
 */
import type { LayoutMap } from '../_lib/types';

const UNIVERSAL: ReadonlyArray<string> = [
  'header',
  'control_bar',
  'filter_chips',
  'bulk_error',
  'results',
  'pagination',
];

// Per-persona section lists are kept as ``key: [...literal]`` so the
// check:layout-coverage audit script (regex-based, dependency-free)
// can match every persona — the script looks for ``key: [`` exactly.
// Don't refactor to helper-function-of-spread; the audit will report
// the persona as missing and CI will warn.
export const ALERTS_LAYOUTS: LayoutMap = {
  // Operational personas get a persona-specific section above the
  // control bar — that's where the glance-up triage lives.
  dispatcher: [
    'header',
    'live_ack_panel',
    'control_bar', 'filter_chips', 'bulk_error', 'results', 'pagination',
  ],
  fleet: [
    'header',
    'vehicle_health_summary',
    'control_bar', 'filter_chips', 'bulk_error', 'results', 'pagination',
  ],
  safety: [
    'header',
    'safety_summary_strip',
    'control_bar', 'filter_chips', 'bulk_error', 'results', 'pagination',
    // Drawer renders nothing until openDrillIn writes to context.  It
    // lives at the tail of the layout so its fixed-position overlay
    // mounts AFTER the rest of the safety page is in the tree — keeps
    // focus management predictable.
    'incident_drillin_drawer',
  ],
  // Owner / Admin share the escalation oversight + account-wide
  // volume cards.  Both sit above the queue so the cross-cutting
  // signal lands before the operator scrolls.
  owner: [
    'header',
    'escalation_status_card',
    'account_alert_summary',
    'control_bar', 'filter_chips', 'bulk_error', 'results', 'pagination',
  ],
  admin: [
    'header',
    'escalation_status_card',
    'account_alert_summary',
    'control_bar', 'filter_chips', 'bulk_error', 'results', 'pagination',
  ],
  // Personas without dedicated sections render the universal layout.
  // Adding a persona-specific section means: write it → register it
  // → expand the array below in place of ``[...UNIVERSAL]``.
  hr:         [...UNIVERSAL],
  accounting: [...UNIVERSAL],
  recruiter:  [...UNIVERSAL],
  driver:     [...UNIVERSAL],
};
