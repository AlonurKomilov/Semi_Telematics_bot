/**
 * Spotlight — interactive product tours.
 *
 * A tour is a statement plus a walk: "did you know X?" (the intro), and
 * on Show me, a guided pass through the user's OWN work on the live
 * page — each step lights the real control, and advances when the user
 * performs the real action.  No slides, no simulation: the ending is a
 * real write the user wanted to make anyway.
 *
 * Engine (this folder) knows no feature.  Each feature contributes its
 * tours as data in `features/<x>/spotlights.ts` and registers them in
 * `spotlightCatalog.ts`; every rendered word lives in the locale files
 * under `spotlight.<key>.*`.  Mirrors the callouts split: structure
 * here, words in locales, so re-wording or a ninth language never
 * touches the engine.
 */

export interface TourStep {
  /**
   * The `data-spotlight` attribute value of the element this step
   * lights.  Declared, never a CSS selector — selectors rot silently
   * when a page is restyled, an attribute is grep-able, and the guard
   * in spotlight.test.ts fails the build if a referenced anchor stops
   * existing in the source.
   */
  anchor: string;
  /**
   * What ends the step.  'click' advances on the user's real click
   * anywhere inside the anchored element (capture-phase, so a
   * component calling stopPropagation cannot strand the tour).
   *
   * 'click-gone' is for a step whose click can FAIL — a submit that
   * native validation refuses.  The click only ARMS the step; it
   * completes when the anchor then leaves the DOM, which for a form
   * that closes itself on success is the success signal.  A live run
   * proved the need: Create was refused ("Please fill out this
   * field") while the tour congratulated the user on a task that was
   * never created.
   */
  advanceOn: 'click' | 'click-gone';
  /**
   * Narrows WHERE inside the anchor the click must land — a CSS
   * selector the target must match via `closest()`.  A step whose
   * anchor is a container (the vehicle-chip well) means "pick one",
   * not "touch the box": without this, clicking the well's own
   * padding — or its "Loading…" text — would count as picking.
   */
  advanceWithin?: string;
  /**
   * This step fires a REAL WRITE.  The engine changes its contract:
   * it never asks for the click — the card states the consequence
   * (with the live count when `countFrom` resolves) and offers
   * "Finish tour" instead, because a tour that suggested an idea must
   * not also press the trigger.  The user commits alone, and only a
   * genuine success (armed click + the form closing itself) earns the
   * "created" goodbye.  The guard forces every tour's LAST step to
   * declare this field true or false — an author cannot add a tour
   * without answering "does this end in a write?".
   */
  commit?: boolean;
  /**
   * Anchor whose `data-spotlight-count` attribute carries the live
   * blast radius ("27 vehicles selected").  Read at render; when the
   * element or attribute is absent the consequence line simply drops
   * its number — never a guessed one.
   */
  countFrom?: string;
}

/**
 * What a page hands the eligibility check.  Deliberately tiny: Phase A
 * triggers read data the page already loads; the behavioural signals
 * endpoint (Phase B) widens this without changing the shape.
 */
export interface TourCtx {
  /** Rows/records the page currently shows (feature-defined meaning). */
  count: number;
  /** The caller may create the thing the tour teaches. */
  canCreate: boolean;
}

export interface TourSpec {
  /** `<feature>.<name>` — namespaced like callout keys, same reason. */
  key: string;
  feature: string;
  steps: TourStep[];
  /**
   * Should the intro offer itself right now?  Pure, reads only ctx —
   * "the user works here enough that the shortcut is worth a
   * sentence".  Adoption-based retirement (they already use it) lands
   * with the Phase B signals.
   */
  relevant: (ctx: TourCtx) => boolean;
}

/** Per-user verdicts, kept in the preferences service (synced). */
export type TourStatus = 'done' | 'skipped' | 'snoozed';

export interface TourStateEntry {
  s: TourStatus;
  /** ISO timestamp of the verdict — snooze expiry is computed from it. */
  t: string;
}

export type SpotlightState = Record<string, TourStateEntry>;

/** Snoozed tours re-offer after this many days; skip/done are final. */
export const SNOOZE_DAYS = 14;

export function isEligible(
  spec: TourSpec,
  ctx: TourCtx,
  state: SpotlightState,
  now = new Date(),
): boolean {
  if (!spec.relevant(ctx)) return false;
  const entry = state[spec.key];
  if (!entry) return true;
  if (entry.s === 'done' || entry.s === 'skipped') return false;
  const age = (now.getTime() - new Date(entry.t).getTime()) / 86_400_000;
  return age >= SNOOZE_DAYS;
}
