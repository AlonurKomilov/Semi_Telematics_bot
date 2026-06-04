/**
 * Persona-aware Alerts page configuration.
 *
 * Mirrors §5 of docs/architecture/phase-3-alerts-design.md.  These
 * are the URL parameter values written on first land for each
 * persona — they define "what does this page look like the moment
 * a Safety user (or Fleet user, or Dispatch user...) clicks
 * Alerts."
 *
 * Section components NEVER read this directly.  The Alerts page
 * wrapper resolves the defaults once (via
 * ``useAlertsFilters().resetToDefaults`` on first mount when the URL
 * has no filter params) and writes them into the URL.  From that
 * point on, the URL is the single source of truth — sections call
 * ``useAlertsFilters()`` and don't know about personas at all.
 *
 * Enforced by check-role-drift.mjs — adding ``useShellConfig`` to a
 * section in features/alerts/sections/ would fail the audit.
 */
import type { Persona } from '../_lib/types';

/** Discrete filter values that compose the page's URL state. */
export type AlertViewMode = 'list' | 'by-vehicle';
export type AlertType =
  | 'all' | 'fault' | 'health' | 'fuel' | 'events' | 'parking' | 'safety_events';
export type AlertSeverity = 'all' | 'critical' | 'warning' | 'info';
export type AlertAckState = 'active' | 'acknowledged' | 'all';

export interface FilterDefaults {
  viewMode: AlertViewMode;
  typeFilter: AlertType;
  severityFilter: AlertSeverity;
  ackState: AlertAckState;
  vehicleSearch: string;
  days: number;
}

/**
 * Per-persona first-land filter defaults (§5 of the design doc).
 *
 * Tuned per persona based on what they actually do every day:
 *   • Dispatcher — flat list, last 7 days, active queue
 *   • Safety / HR — by-vehicle, safety_events only (pattern detection)
 *   • Fleet — by-vehicle, last 30 days (more time for vehicle-health trends)
 *   • Owner / Admin — by-vehicle, last 7 days, all types
 *   • Accounting — by-vehicle, last 30 days (cost-trend window)
 *   • Driver — flat list (Mini App is primary surface; web is fallback)
 */
export const PERSONA_FILTER_DEFAULTS: Record<Persona, FilterDefaults> = {
  owner: {
    viewMode: 'by-vehicle',
    typeFilter: 'all',
    severityFilter: 'all',
    ackState: 'active',
    vehicleSearch: '',
    days: 7,
  },
  admin: {
    viewMode: 'by-vehicle',
    typeFilter: 'all',
    severityFilter: 'all',
    ackState: 'active',
    vehicleSearch: '',
    days: 7,
  },
  fleet: {
    viewMode: 'by-vehicle',
    typeFilter: 'all',
    severityFilter: 'all',
    ackState: 'active',
    vehicleSearch: '',
    days: 30,
  },
  dispatcher: {
    viewMode: 'list',
    typeFilter: 'all',
    severityFilter: 'all',
    ackState: 'active',
    vehicleSearch: '',
    days: 7,
  },
  safety: {
    viewMode: 'by-vehicle',
    typeFilter: 'safety_events',
    severityFilter: 'all',
    ackState: 'active',
    vehicleSearch: '',
    days: 7,
  },
  hr: {
    viewMode: 'by-vehicle',
    typeFilter: 'safety_events',
    severityFilter: 'all',
    ackState: 'active',
    vehicleSearch: '',
    days: 30,
  },
  accounting: {
    viewMode: 'by-vehicle',
    typeFilter: 'all',
    severityFilter: 'all',
    ackState: 'active',
    vehicleSearch: '',
    days: 30,
  },
  driver: {
    viewMode: 'list',
    typeFilter: 'all',
    severityFilter: 'all',
    ackState: 'active',
    vehicleSearch: '',
    days: 7,
  },
};

/**
 * Resolve filter defaults for a persona with the standard
 * Pattern B fallback: persona → owner → admin → first defined entry.
 * Mirrors the resolver pattern in features/overview/personaConfig.ts
 * so a future refactor that drops an entry never crashes the page.
 */
export function resolveFilterDefaults(persona: Persona): FilterDefaults {
  return (
    PERSONA_FILTER_DEFAULTS[persona] ??
    PERSONA_FILTER_DEFAULTS.owner ??
    PERSONA_FILTER_DEFAULTS.admin
  );
}
