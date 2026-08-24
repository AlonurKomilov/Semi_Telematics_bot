/**
 * Callout catalog — the single source of truth for what a callout
 * KEY means, mirroring the featureCatalog pattern: structure lives
 * here, words live in the locale files (`callout.<key>.*`).
 *
 * Kinds are not decoration.  Each carries a different dismissal
 * lifecycle, which is the only reason it earns its own name:
 *
 *   caveat     qualifies the data on screen — NEVER dismissible,
 *              because hiding "these miles are summed across two
 *              devices" re-hides the very thing it corrects.
 *   condition  a state that clears when the WORLD changes, not when
 *              the reader clicks.
 *   guidance   an optional suggestion — dismissible and remembered.
 *              No consumer yet; the storage lands with the first one.
 *
 * A key the backend can emit but this catalog does not know would
 * render as a raw string, so `test_callouts.py` compares the two
 * lists and fails the build on drift.
 */
import type { Tone } from '../../lib/status';

export type CalloutKind = 'caveat' | 'condition' | 'guidance';

export interface CalloutSpec {
  kind: CalloutKind;
  /** Maps to the shared tone vocabulary — colour AND icon. */
  severity: Tone;
  /**
   * Whether a dismissal applies to the key everywhere or only to the
   * entity it names.  A structural property of the callout, never a
   * user choice.  Only read for `guidance`.
   */
  dismissScope?: 'key' | 'entity';
}

export const CALLOUT_CATALOG: Record<string, CalloutSpec> = {
  // ── Vehicle conditions ──────────────────────────────────────
  'vehicle.no_engine_data': { kind: 'condition', severity: 'warn' },

  // ── Mileage caveats (previously mileageFlags' FLAG_NOTE) ────
  // All six are `warn` because all six render as a warn chip TODAY —
  // the fold is deliberately rendering-identical, so a visual diff
  // here would be the fold's bug rather than its feature.  Re-tiering
  // the three that are really informational ('estimated', 'catchup',
  // 'partial') is a one-line follow-up, on its own decision.
  'mileage.device_change': { kind: 'caveat', severity: 'warn' },
  'mileage.estimated':     { kind: 'caveat', severity: 'warn' },
  'mileage.catchup':       { kind: 'caveat', severity: 'warn' },
  'mileage.partial':       { kind: 'caveat', severity: 'warn' },
  'mileage.reset':         { kind: 'caveat', severity: 'warn' },
  'mileage.rebase':        { kind: 'caveat', severity: 'warn' },
};

/** One callout as it arrives on the wire. */
export interface CalloutData {
  key: string;
  /** `""` = the surface itself; `vehicle:<id>` = one record. */
  entity?: string;
  since?: string;
  params?: Record<string, string>;
}

export function calloutSpec(key: string): CalloutSpec | undefined {
  return CALLOUT_CATALOG[key];
}

/**
 * Group a response's `callouts` array by entity, so a row can ask
 * "anything about me?" in constant time.  Callouts with no entity
 * land under `''` — the surface's own.
 */
export function byEntity(
  callouts: CalloutData[] | undefined,
): Map<string, CalloutData[]> {
  const map = new Map<string, CalloutData[]>();
  for (const c of callouts ?? []) {
    const k = c.entity ?? '';
    const list = map.get(k);
    if (list) list.push(c);
    else map.set(k, [c]);
  }
  return map;
}
