/**
 * Unit-formatting helpers — mph/kph and °F/°C.
 *
 * Backend speaks Imperial (mph, °F, gallons).  When the user picks
 * "metric" in profile preferences we convert at render time.
 */

export type UnitSystem = 'imperial' | 'metric';

const KEY = 'st_units';

export function getUnitSystem(): UnitSystem {
  try {
    const v = localStorage.getItem(KEY);
    return v === 'metric' ? 'metric' : 'imperial';
  } catch {
    return 'imperial';
  }
}

export function setUnitSystem(u: UnitSystem): void {
  try {
    localStorage.setItem(KEY, u);
    window.dispatchEvent(new Event('st-units-change'));
  } catch {
    /* storage may be unavailable in some Telegram desktop builds */
  }
}

/** Format a speed value (input always in mph). */
export function fmtSpeed(mph: number | null | undefined, units: UnitSystem = getUnitSystem()): string {
  if (mph == null) return '—';
  if (units === 'metric') return `${Math.round(mph * 1.60934)} km/h`;
  return `${Math.round(mph)} mph`;
}

/** Speed unit label only (no value). */
export function speedUnit(units: UnitSystem = getUnitSystem()): string {
  return units === 'metric' ? 'km/h' : 'mph';
}

/** Format a distance value (input in miles). */
export function fmtDistance(miles: number | null | undefined, units: UnitSystem = getUnitSystem()): string {
  if (miles == null) return '—';
  if (units === 'metric') return `${(miles * 1.60934).toFixed(1)} km`;
  return `${miles.toFixed(1)} mi`;
}
