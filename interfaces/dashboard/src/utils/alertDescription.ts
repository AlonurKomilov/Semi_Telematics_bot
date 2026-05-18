/**
 * Render an alert's Description column in plain language.
 *
 * The backend stores ``last_detail`` as a terse machine-readable key
 * (``parking:unsafe:8h``, ``fuel:19``, ``crash:281475…``) so the alert
 * pipeline can dedup and route without ambiguity.  That's the right
 * choice for storage, but it leaks raw event IDs and SPN codes into
 * the Alerts table — dispatchers asked us to clean that up.
 *
 * This util translates each known pattern into a sentence a non-
 * engineer can read at a glance.  Unknown patterns fall through to
 * the raw string so we never blank-out a description that doesn't
 * have an explicit mapping.
 *
 * The raw value stays available in the row's ``title`` tooltip for
 * anyone debugging or following up with support.
 */

interface AlertLike {
  alert_type?: string | null;
  last_detail?: string | null;
  message?: string | null;
  description?: string | null;
}

// Parking location classes the parking detector emits.  ``unknown``
// means "GPS dropped or address lookup failed" — admins read it as
// "we couldn't verify the spot is safe" which is what the label says.
const PARKING_CLASS_LABEL: Record<string, string> = {
  safe:     'safe location',
  unsafe:   'unsafe location',
  unknown:  'unverified location',
  highway:  'highway shoulder',
  truck_stop: 'truck stop',
  rest_area:  'rest area',
};

// Samsara safety-event types — same set used by the bot's
// formatting.events module.
const EVENT_TYPE_LABEL: Record<string, string> = {
  crash:                  'Crash detected',
  harshBraking:           'Harsh braking',
  harshAcceleration:      'Harsh acceleration',
  harshTurn:              'Harsh turn',
  speeding:               'Speeding',
  rollingStop:            'Rolling stop',
  inattentiveDriving:     'Inattentive driving',
  noSeatbelt:             'No seatbelt',
  mobileUsage:            'Mobile phone use',
  followingDistance:      'Following distance violation',
  drowsiness:             'Drowsy driving',
  smoking:                'Smoking',
  uTurn:                  'U-turn',
  laneDrift:              'Lane drift',
  laneDeparture:          'Lane departure',
  defensiveDrivingTraining: 'Defensive driving alert',
};

// Health-alert internal codes the alerting pipeline emits.
const HEALTH_LABEL: Record<string, string> = {
  low_battery:        'Low battery',
  low_def:            'Low DEF',
  low_oil_pressure:   'Low oil pressure',
  high_coolant_temp:  'High coolant temp',
  high_engine_temp:   'High engine temp',
  low_fuel:           'Low fuel',
  check_engine:       'Check engine',
};

function pluralize(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** Translate a parking ``last_detail`` ("parking:unsafe:8h") into a
 *  sentence ("Parked in unsafe location for 8 hours"). */
function formatParking(detail: string): string {
  // Expected shape: ``parking:<class>:<N>h``  (e.g. ``parking:unsafe:8h``)
  const parts = detail.split(':');
  const cls = parts[1] || 'unknown';
  const dur = parts[2] || '';
  const label = PARKING_CLASS_LABEL[cls] || `${cls} location`;
  const hoursMatch = dur.match(/^(\d+(?:\.\d+)?)h$/);
  const hours = hoursMatch ? Math.round(Number(hoursMatch[1])) : 0;
  if (hours > 0) {
    return `Parked in ${label} for ${pluralize(hours, 'hour', 'hours')}`;
  }
  return `Parked in ${label}`;
}

/** Translate a fuel ``last_detail`` ("fuel:19") into a sentence. */
function formatFuel(detail: string): string {
  const m = detail.match(/^fuel:(\d+)$/);
  if (!m) return detail;
  const pct = Number(m[1]);
  if (pct <= 10) return `Critical low fuel: ${pct}%`;
  if (pct <= 20) return `Low fuel: ${pct}%`;
  return `Fuel level: ${pct}%`;
}

/** Translate a safety event ``last_detail`` ("crash:2814…") into
 *  plain English.  The trailing event-ID is intentionally dropped
 *  from the cell — it's noise for dispatchers — but the raw value
 *  stays on the row's ``title`` tooltip for support follow-ups. */
function formatEvent(detail: string): string {
  // Expected: ``<eventType>:<eid>`` — sometimes just ``<eventType>``
  const colonAt = detail.indexOf(':');
  const evType = colonAt > -1 ? detail.slice(0, colonAt) : detail;
  return EVENT_TYPE_LABEL[evType] || (evType ? evType.replace(/([A-Z])/g, ' $1').trim() : detail);
}

/** Translate a health ``last_detail`` ("low_battery-low_def") into
 *  a human-readable comma-list. */
function formatHealth(detail: string): string {
  const codes = detail.split('-').filter(Boolean);
  if (codes.length === 0) return detail;
  const labels = codes.map((c) => HEALTH_LABEL[c] || c.replace(/_/g, ' '));
  return labels.join(', ');
}

/** Translate a fault ``last_detail`` ("SPN103:Engine Turbocharger 1 Speed
 *  |SPN510:..."): pick the first SPN's human name and tag the count when
 *  there are more.  SPN codes are still visible via the tooltip. */
function formatFault(detail: string): string {
  // Backend joins multiple faults with "|".  The format per fault is
  // ``SPN<code>:<name>``; ``SPN<code>:Manufacturer Assignable SPN`` is
  // a generic placeholder the OEM didn't translate — collapse those.
  const faults = detail.split('|').map((f) => f.trim()).filter(Boolean);
  if (faults.length === 0) return detail;
  function _label(f: string): string {
    const m = f.match(/^SPN(\d+):(.+)$/);
    if (!m) return f;
    const [, code, name] = m;
    if (name.toLowerCase().startsWith('manufacturer assignable')) {
      return `Unmapped fault code SPN ${code}`;
    }
    return name;
  }
  const first = _label(faults[0]);
  return faults.length > 1
    ? `${first} (+${faults.length - 1} more)`
    : first;
}

/** Public API — pick the right per-type formatter and fall through
 *  to the raw detail on anything unrecognised. */
export function formatAlertDescription(alert: AlertLike): string {
  const raw =
    alert.last_detail ||
    alert.message ||
    alert.description ||
    '';
  if (!raw) return '—';
  const type = (alert.alert_type || '').toLowerCase();

  // Dispatch by type FIRST so we don't mis-parse a fault-detail string
  // that happens to start with "fuel:" or similar.
  if (type === 'parking' && raw.startsWith('parking:')) return formatParking(raw);
  if (type === 'fuel' && raw.startsWith('fuel:'))      return formatFuel(raw);
  if (type === 'events' || type === 'safety_event')    return formatEvent(raw);
  if (type === 'fault' || type === 'faults')           return formatFault(raw);
  if (type === 'health')                               return formatHealth(raw);

  // Geofence already arrives as "entered Yard A" — readable as-is.
  // Camera, maintenance, scorecard, documents, etc. fall through.
  return raw;
}
