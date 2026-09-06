/**
 * Fuel and DEF, drawn the way the dashboard draws them.
 *
 * The panel already used DEF for the warning ring on the icon
 * (``hasLowLevelWarning``) but never SHOWED it, so a truck could wear a
 * red ring for a reason the panel would not say out loud.
 */
export const LOW_LEVEL_PCT = 15;

export interface Level { key: 'fuel' | 'def'; label: string; pct: number; low: boolean }

/** The levels a vehicle actually reports — a missing sensor draws no
 *  bar rather than an honest-looking zero. */
export function levelsOf(p: { fuel_percent?: number | null; def_percent?: number | null }): Level[] {
  const out: Level[] = [];
  if (p.fuel_percent != null) {
    out.push({ key: 'fuel', label: 'Fuel', pct: clampPct(p.fuel_percent), low: p.fuel_percent < LOW_LEVEL_PCT });
  }
  if (p.def_percent != null) {
    out.push({ key: 'def', label: 'DEF', pct: clampPct(p.def_percent), low: p.def_percent < LOW_LEVEL_PCT });
  }
  return out;
}

/** A bar cannot be shorter than empty or longer than full, whatever the
 *  provider sends — a 120% tank would otherwise overflow its track. */
export function clampPct(v: number): number {
  if (!Number.isFinite(v)) return 0;
  return Math.max(0, Math.min(100, Math.round(v)));
}
