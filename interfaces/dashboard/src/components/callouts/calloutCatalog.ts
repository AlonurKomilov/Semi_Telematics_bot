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
 *              No consumer yet; the storage lands with the first one.
 *
 * A key the backend can emit but this catalog does not know would
 * render as a raw string, so `test_callouts.py` compares the two
 * lists and fails the build on drift.
 */
import type { Tone } from '../../lib/status';

export type CalloutKind = 'caveat' | 'condition';

/**
 * What the X does — declared per callout, because `kind` is too coarse
 * to decide it.
 *
 *   none      no X at all.
 *   collapse  shrinks to one line, still on screen.  For anything that
 *             qualifies a NUMBER the reader is about to act on: the
 *             line is what stops a 0-mile truck reading as a real zero,
 *             and an audit entry only assigns blame afterwards.
 *   remove    gone from this person's view.  For statements worth
 *             knowing once that cost nothing to lose.
 *
 * `remove` is the only one that writes to the activity trail, because
 * it is the only one where something left the reader's screen.
 */
export type CalloutDismiss = 'none' | 'collapse';

/** The default when a callout does not declare one — kind decides. */
export function defaultDismiss(kind: CalloutKind): CalloutDismiss {
  return kind === 'caveat' ? 'none' : 'collapse';
}

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
  /**
   * Overrides `defaultDismiss(kind)`.  A condition that is genuinely
   * good-to-know rather than protective declares `'remove'` here and
   * gets a real X instead of a collapse.
   */
  dismiss?: CalloutDismiss;
}

export const CALLOUT_CATALOG: Record<string, CalloutSpec> = {
  // ── Vehicle conditions ──────────────────────────────────────
  'vehicle.no_engine_data': { kind: 'condition', severity: 'warn' },

  // ── Device identity questions ───────────────────────────────
  // Rendered through this lane but stored and resolved by the vehicles
  // feature (device_event_log).  Their ANSWER edits the registry and
  // closes the question for the whole account, which is why the card
  // supplies its own buttons through the actions slot — those are not
  // this lane's business and never were.
  // ``warn``, not ``danger``.  Danger is this system's colour for
  // overdue / failed / past-due — states that are actively failing and
  // want "act now".  A changed VIN is serious but it is a QUESTION
  // waiting on a person, and painting the whole strip red (body text
  // included) both overstated it and cost legibility.
  //
  // They FOLD, like any condition.  They used to declare
  // `dismiss: 'none'` to stop a pending question being closed — sound
  // when the alternative was removal, and stale the moment removal
  // was deleted.  Collapse is not closing: the statement stays on
  // screen as one line carrying its count, it re-opens the moment a
  // new truck raises the same question, and one click undoes it.  A
  // dispatcher who is not the person answering identity questions
  // should not carry someone else's queue at the top of the Vehicles
  // page all week, and the answer buttons stay exactly where they are.
  'vehicle.vin_changed':      { kind: 'condition', severity: 'warn' },
  'vehicle.gateway_swapped':  { kind: 'condition', severity: 'warn' },
  'vehicle.odometer_rebased': { kind: 'condition', severity: 'warn' },

  // ── The truck has left the fleet ────────────────────────────
  // A STATE, not a fault, stated at the top of a page that otherwise
  // looks exactly like a working truck's.  The detail page reads the
  // PROVIDER, not our warehouse, so the ingest gate cannot reach it:
  // Samsara returns a retired truck's last-known fuel, DEF and
  // coordinates and the page draws them beside a freshness dot.
  //
  // `info` for an archive someone chose — nothing is wrong, and amber
  // would put a retired truck in the same register as a fault.
  // `warn` for one the sweep retired: that might be a broken gateway
  // rather than a truck that left, and it is the one with something
  // to go and check.
  'vehicle.archived':         { kind: 'condition', severity: 'info' },
  'vehicle.stopped_reporting': { kind: 'condition', severity: 'warn' },

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
  /**
   * The identity a dismissal is stored against — minted by the
   * callouts capability from key + entity + occurrence start.  OPAQUE:
   * never parse it, never build one client-side.  It changes when a
   * fault clears and returns, which is what makes a second outage
   * reappear instead of inheriting the first one's dismissal.
   */
  callout_id?: string;
  /** `""` = the surface itself; `vehicle:<id>` = one record. */
  entity?: string;
  since?: string;
  params?: Record<string, string>;
}

export function calloutSpec(key: string): CalloutSpec | undefined {
  return CALLOUT_CATALOG[key];
}

/** What the X should do for this callout — its declaration, else the
 *  kind's default, else nothing for a key we do not know. */
export function dismissBehaviour(key: string): CalloutDismiss {
  const spec = CALLOUT_CATALOG[key];
  if (!spec) return 'none';
  return spec.dismiss ?? defaultDismiss(spec.kind);
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
