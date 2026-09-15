/**
 * What you did, as eleven sounds.
 *
 * The five cues in `engine.ts` all mean ONE thing — the app answered
 * you. An alert arrived, something saved, something was refused. They
 * are raised by lanes: a toast, a banner, an undo window. Nothing in
 * this product has ever made a sound because a person TOUCHED it.
 *
 * This is that other half. It is a third axis for the same reason the
 * keyboard is a second one, and `engine.ts` says it plainly: a keyboard
 * click and a notification cue are not the same kind of sound and must
 * not share one band. An act cue is shorter and quieter than both — a
 * press is 14ms, which is ILLEGAL in `CUE_LIMITS`, whose duration floor
 * is 20ms because it was tuned for something you are meant to notice.
 *
 * Widening `CueName` instead would also have handed all 328 toast call
 * sites the ability to ask for `page_open`, which is a vocabulary
 * nobody could hold.
 *
 * PURE of preferences and of the pack catalogue, the way `keys.ts` is.
 * The gates, the volume and the pack are read one layer up in `cue.ts`;
 * this takes a resolved pack and answers with a cue. What lives here is
 * what belongs to the ACT rather than to the person: how fast one may
 * repeat, how a streak decays, and when the room is already busy.
 */
import { type Cue, type CueLimits } from './engine';
import type { ItemMeta } from '../store/items/meta';

export const ACT_NAMES = [
  // Controls — you touched something
  'press', 'chip', 'toggle_on', 'toggle_off', 'menu_pick',
  // Places — something opened or closed around you
  'page_open', 'page_close', 'surface_open', 'surface_close',
  // Selection — you gathered or dropped a set
  'select_add', 'select_clear',
] as const;
export type ActName = (typeof ACT_NAMES)[number];

/**
 * Three families, so the first complaint at hour six has an answer that
 * is not "turn it all off".
 *
 * Declared `satisfies` a total record, so a twelfth act cannot be added
 * without deciding which switch owns it — an act with no family would
 * be an act no one can silence.
 */
export const ACT_FAMILY = {
  press: 'controls', chip: 'controls', toggle_on: 'controls',
  toggle_off: 'controls', menu_pick: 'controls',
  page_open: 'places', page_close: 'places',
  surface_open: 'places', surface_close: 'places',
  select_add: 'selection', select_clear: 'selection',
} as const satisfies Record<ActName, 'controls' | 'places' | 'selection'>;

export type ActFamily = (typeof ACT_FAMILY)[ActName];

/**
 * Tighter than the keyboard's, which is already tighter than a cue's.
 *
 * The gain ceiling is the ladder made unbreakable by the type system
 * rather than by anybody's restraint: the interface answering a touch
 * must not be louder than the keystroke that caused it, and neither may
 * approach a notification.
 *
 *   acts <= 0.06  <  keys <= 0.08  <  answers <= 0.4
 *
 * The duration floor is 6ms, the same as a key click. The ceiling is
 * 70ms rather than the keyboard's 50: a page opening is allowed to be a
 * longer sound than a keystroke, because it happens a hundredth as
 * often.
 */
export const ACT_LIMITS: CueLimits = {
  freq: { min: 20, max: 12_000 },
  dur: { min: 0.006, max: 0.070 },
  gain: { min: 0, max: 0.06 },
};

export interface ActPack extends ItemMeta {
  readonly cues: Readonly<Record<ActName, Cue>>;
}

/**
 * The floor between two act cues.
 *
 * Longer than the longest act cue plus its tail, so two can never
 * overlap — the argument `keys.ts` makes for its 30ms, scaled to a
 * longer band. `engine.ts` stops an oscillator at `dur + 0.02`, so the
 * real occupancy of a 70ms cue is 90ms.
 *
 * Short enough that a deliberate double-click, around 250ms, still
 * sounds twice.
 *
 * DROPS, never queues. A click that arrives after the thing it belongs
 * to has already happened is worse than silence.
 */
const ACT_GAP_MS = 90;

/**
 * How long an act defers to the app having just spoken.
 *
 * An alert arriving is the one thing in this product a person must not
 * miss, and a press landing on top of it is a press competing with it.
 * The action bus already ducks; this is the other half — the act simply
 * does not play.
 */
const DEFER_MS = 250;

/**
 * How long after a keystroke the act axis stays out of the way.
 *
 * Typing and clicking are one continuous gesture when somebody fills a
 * form: tab, type, tab, type. The keyboard axis owns that stretch, and
 * two voices on one physical act is a stutter.
 */
const TYPING_MS = 250;

/** A streak is over after this much quiet. */
const STREAK_RESET_MS = 1_500;

/**
 * Ten repeats become one announcement and nine murmurs.
 *
 * Scaling the VOLUME ARGUMENT rather than the cue's own gain, because
 * `engine.ts` computes `cue.gain * volume` and `engine.test.ts` asserts
 * that chain is linear in exactly that argument. A second multiplier
 * anywhere else is the "40% of 40%" bug the Sounds panel already
 * carries one test against.
 */
const STREAK_SCALE: readonly (readonly [number, number])[] = [
  [6, 0.35],
  [3, 0.6],
];

let lastAt = -Infinity;
let streakName: ActName | null = null;
let streakCount = 0;

/**
 * What this session has actually HEARD, per act.
 *
 * Every per-shift number behind this axis is a derivation — there is no
 * click telemetry in this product, and there is deliberately none being
 * added for a convenience feature. So the instrument is the smallest
 * honest one: a count in memory, on this tab, shown to the person whose
 * ears it is about.
 *
 * It answers the question the design could only estimate — is a press
 * five hundred times a shift, or two thousand? — and it answers a
 * second one the person asks themselves: "how much of this am I
 * actually hearing?" A number they can read is a better basis for
 * keeping it on than a feeling.
 *
 * Counted where a cue was PLAYED, past every gate and every floor, so
 * it reports what reached the room rather than what was attempted.
 * Nothing leaves the tab and nothing is stored.
 */
const heard = new Map<ActName, number>();
let heardTotal = 0;

export function noteHeard(name: ActName): void {
  heard.set(name, (heard.get(name) ?? 0) + 1);
  heardTotal += 1;
}

/** The tally, newest count included. Total is separate so a reader does
 *  not have to sum a map to answer the first question. */
export function heardSoFar(): { total: number; byAct: ReadonlyMap<ActName, number> } {
  return { total: heardTotal, byAct: heard };
}

/** Test seam — the rate limiter is module state, like the context. */
export function resetActSoundForTests(): void {
  lastAt = -Infinity;
  streakName = null;
  streakCount = 0;
  heard.clear();
  heardTotal = 0;
}

/** What a repeated act is scaled by, given how many came before it. */
export function streakScale(count: number): number {
  for (const [at, scale] of STREAK_SCALE) if (count >= at) return scale;
  return 1;
}

/**
 * The cue one act earns and the volume scale it plays at, or null.
 *
 * `now` and the two lane timestamps are passed IN rather than read, so
 * this file stays a leaf: it imports no preferences, no clock policy
 * and no other lane. That is what lets `acts.test.ts` drive every branch
 * without a DOM, a context or a stub.
 */
export function pickActCue(
  name: ActName,
  pack: ActPack | undefined,
  now: number,
  lanes: { lastNotifyAt: number; lastKeyAt: number },
): { cue: Cue; scale: number } | null {
  // Deference first, and it does NOT spend the floor: an act refused
  // because the app was speaking must not also silence the next one.
  if (now - lanes.lastNotifyAt < DEFER_MS) return null;
  if (now - lanes.lastKeyAt < TYPING_MS) return null;

  if (now - lastAt < ACT_GAP_MS) return null;

  // The streak counts every act that got past the floor, whether or not
  // a pack answers — it is a property of what the hand did.
  if (name === streakName && now - lastAt < STREAK_RESET_MS) streakCount += 1;
  else { streakName = name; streakCount = 0; }
  lastAt = now;

  const cue = pack?.cues[name];
  return cue ? { cue, scale: streakScale(streakCount) } : null;
}
