/**
 * Vehicle Detail per-persona layouts.
 *
 * Each entry is an ordered list of section ids — visual order is
 * top-to-bottom on the rendered page.  Section ids reference
 * ``VEHICLE_SECTIONS`` in ``registry.ts``; the ``check:layout-coverage``
 * audit catches drift if an id here is missing from the registry or a
 * persona is missing here entirely.
 *
 * Owner + Admin are the canonical superset — every section, in the
 * order the original VehicleDetail.tsx rendered them.  Tuned-persona
 * layouts (fleet / dispatcher / safety / driver) reorder and trim to
 * match each persona's workflow.  HR + Accounting are intentionally
 * absent — they fall through to the owner superset at runtime via
 * PageLayoutHost.  The audit script warns about the missing entries
 * (correctly); add tuned entries here when those personas ship in
 * Phase 5.
 */
import type { LayoutMap } from '../_lib/types';

export const VEHICLE_LAYOUTS: LayoutMap = {
  // Superset — preserves the original page order so Owner / Admin
  // see byte-identical output to pre-Pattern-B.  Used as the runtime
  // fallback for any persona that doesn't have an entry below.
  owner: ['header', 'info', 'inventory', 'location', 'health', 'faults', 'inspections', 'timeline', 'usage'],
  admin: ['header', 'info', 'inventory', 'location', 'health', 'faults', 'inspections', 'timeline', 'usage'],

  // Fleet — vehicle health + maintenance signals lead.  Faults bubbles
  // above Location because a fleet manager opening a truck is usually
  // chasing a fault code; coordinates can wait.  Usage trends sit at
  // the bottom for utilization review.
  fleet: ['header', 'info', 'inventory', 'health', 'faults', 'inspections', 'location', 'timeline', 'usage'],

  // Dispatcher — "where is this truck right now and what's it doing
  // over the next few hours."  Health / Faults / Inspections are
  // omitted; Dispatch doesn't action vehicle ops.
  dispatcher: ['header', 'info', 'location', 'timeline'],

  // Safety — drilling into a truck during an incident review.
  // Inspections (CSA history proxy) and Timeline (where was the
  // truck during the incident) matter most; Faults are omitted
  // because Safety doesn't action mechanical issues directly.  Usage
  // trends added for harsh-event totals over the recent window.
  safety: ['header', 'info', 'location', 'inventory', 'inspections', 'timeline', 'usage'],

  // Driver — uses the Mini App in practice; this is a fallback for the
  // unlikely web-dashboard visit by a driver role.  Minimal: identity
  // + location only.
  driver: ['header', 'info', 'location'],

  // HR — compliance-focused.  Inspections (PTI history is CSA-audit-
  // adjacent) lands above Location.  Faults / Health / Timeline are
  // omitted — HR doesn't action vehicle ops.  When DriverAssignment
  // and InsuranceStatus sections are built (product work), they
  // slot in between info and inspections.
  hr: ['header', 'info', 'inspections', 'location'],

  // Recruiter — personnel persona; same vehicle view as HR.
  recruiter: ['header', 'info', 'inspections', 'location'],

  // Accounting — utilisation + identity, no ops.  Usage trends carry
  // the cost-per-mile + work-order spend; Timeline kept for the
  // weekly drill-down.  Health / Faults / Inspections / Location all
  // omitted — accounting reviews financial outcomes, not operations.
  accounting: ['header', 'info', 'usage', 'timeline'],
};
