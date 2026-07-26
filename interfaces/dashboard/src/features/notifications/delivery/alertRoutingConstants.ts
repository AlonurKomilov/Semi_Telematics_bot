// Shared alert-routing label constants — used by BOTH the role-mode
// roster (AlertRoutingSection) and the single-bot topic table
// (ForumRoutingSection).  Kept in their own module (not exported from a
// component file) so React Fast Refresh stays happy.
//
// TYPE_LABELS, FEATURE_GROUPS, and SUBTYPE_LABELS are intentionally
// English in ALL locales: alert-type / feature / kind names are fixed
// PRODUCT VOCABULARY (owner decision 2026-07-21).  Single-bot mode draws
// the same names + descriptions straight from the backend catalog
// (English), so translating only these frontend constants would DESYNC
// the two modes — do not localize them without also localizing the
// backend catalog.  Not a bug; don't "fix" it.

// Operational staff roles, in roster order.  The owner_admin aggregate
// renders separately (the Main row); these are the roles a manager can
// belong to and that get their own group / Sub bot.
export const ROLE_ORDER = ['dispatcher', 'safety', 'fleet', 'hr', 'accounting', 'recruiter'] as const;

// Display names for the canonical alert types.
export const TYPE_LABELS: Record<string, string> = {
  faults: 'Faults', health: 'Health', fuel: 'Fuel', events: 'Safety Events',
  camera: 'Cameras', parking: 'Parking', geofence: 'Geofences',
  scorecard: 'Scorecards', maintenance: 'Maintenance',
  documents: 'Driver Documents', system: 'Sync & System',
};

// Feature hierarchy — topics render grouped under their owning FEATURE
// (mirrors the product taxonomy: Vehicle telemetry, Safety, …), not as
// one flat list.  Both modes group identically.
export const FEATURE_GROUPS: { label: string; types: string[] }[] = [
  { label: 'Vehicle', types: ['faults', 'health', 'fuel'] },
  { label: 'Safety', types: ['events', 'camera', 'parking'] },
  { label: 'Geofences', types: ['geofence'] },
  { label: 'Scorecards', types: ['scorecard'] },
  { label: 'Maintenance', types: ['maintenance'] },
  { label: 'Drivers', types: ['documents'] },
  { label: 'System', types: ['system'] },
];

// Sub-category display names — mirrors the events formatting SSOT
// (capabilities/formatting/events.py keys).
export const SUBTYPE_LABELS: Record<string, string> = {
  crash: 'Crash', braking: 'Harsh Braking', acceleration: 'Harsh Acceleration',
  harshTurn: 'Harsh Turn', rollingStop: 'Rolling Stop',
  followingDistance: 'Following Distance', laneDeparture: 'Lane Departure',
};
