/**
 * One listener, for every click in the product.
 *
 * The obvious build is a cue inside `components/ui/button.tsx`, and it
 * is measurably wrong here: this codebase has **278 `<Button>` and 690
 * raw `<button>`**. A cue in the primitive reaches under a third of the
 * clickable things on screen, and the two thirds it misses are not
 * random — they cluster by feature, by who wrote the page and when. The
 * result would be the one thing the owner ruled out: the same act
 * sounding on one page and silent on the next.
 *
 * So the rule is read from the DOM and never from the component. A
 * feature cannot opt in, cannot opt out, and cannot be inconsistent,
 * because no feature is involved.
 *
 * CAPTURE PHASE, and that is not a preference. Fifty places in this tree
 * call `stopPropagation`, and three of them are the DataGrid's selection
 * checkboxes — literally `onClick={e => e.stopPropagation()}`. A
 * bubble-phase listener on `document` would be deaf to exactly the
 * controls this feature is about.
 *
 * `click` and not `pointerdown`, for three measured reasons: 292
 * `<label>` elements wrap their own control and `pointerdown` on the
 * label word walks up and away from it; `pointerdown` fires on
 * touchstart, so a scroll begun on a button sounds a press nobody
 * completed; and `pointerdown` cannot see keyboard activation at all,
 * which would silence every keyboard-only user without saying so.
 */
import { playActCue } from './cue';
import { isSensitiveTarget } from './keys';
import type { ActName } from './acts';

/** What counts as button-shaped. Tags and roles, never class names. */
const PRESSABLE = 'button,[role="button"],[data-slot="button"],summary,a[href]';

/** A binary state you committed. */
const TOGGLE = [
  '[role="switch"]', '[role="checkbox"]',
  'input[type="checkbox"]', '[data-slot="checkbox"]',
].join(',');

/**
 * One of a visible set.
 *
 * `aria-pressed` is this product's chip signature — `panel/Chip.tsx`
 * carries it and twenty other files reach for the same attribute — and
 * a tab is the same question in a different shape. A radio is the
 * native form of it.
 *
 * A chip-shaped control that carries NO signature classifies as a
 * press, and that is the honest answer rather than a gap: an element
 * that tells no assistive technology it is selected is not a chip to
 * anything that reads the page. The sound follows the semantics, so
 * where the semantics are missing both are wrong together and one fix
 * repairs both.
 */
const CHIP = [
  '[aria-pressed]', '[role="tab"]', '[role="radio"]',
  'input[type="radio"]', '[data-slot="radio"]',
].join(',');

/** A choice landing out of a list that was open. */
const MENU = [
  '[role="menuitem"]', '[role="menuitemradio"]', '[role="menuitemcheckbox"]',
  '[role="option"]', '[data-slot="select-item"]',
].join(',');

/**
 * Still waiting for a name of its own.
 *
 * Empty now that Controls is complete. It is kept because the rule it
 * encodes outlives its current contents: a control whose cue is coming
 * stays SILENT rather than borrowing `press`, because a cue somebody
 * has learned cannot be quietly reassigned. Selection and Places both
 * arrive through here.
 */
const DEFERRED = ':not(*)';

/** The escape hatch, for a control that must stay silent by name. */
const MUTED = '[data-cue="none"]';

/**
 * Which act a click on this element is, or null.
 *
 * Pure, exported, and driven directly by its own tests — the branches
 * below are the whole feature, and a classifier only reachable through
 * a real DOM event is a classifier nobody can prove.
 */
export function classifyAct(target: EventTarget | null): ActName | null {
  const node = target as Element | null;
  if (!node || typeof node.closest !== 'function') return null;

  // Marked silent, anywhere up the tree.
  if (node.closest(MUTED)) return null;

  // The SSN and password rule, reused rather than restated. It already
  // covers `data-no-key-sound`, which is how the FMCSA form stays quiet.
  if (isSensitiveTarget(node)) return null;

  // NO SPECIAL CASE FOR <label>, deliberately, and it took a mutation
  // to establish that. 292 labels here wrap their own control, and a
  // click on the label word does arrive twice — once on the label, once
  // forwarded to the control. But `closest` walks UP, and the control is
  // a CHILD, so the label's own event classifies to nothing on its own;
  // and the two arrive in the same task, so the 90ms floor in `acts.ts`
  // collapses them even when both would sound. A guard here would be a
  // branch nothing can reach, which is worse than none: the next person
  // reads it as load-bearing.
  const el = node.closest(`${PRESSABLE},${TOGGLE},${CHIP},${MENU},${DEFERRED}`);
  if (!el) return null;

  // Disabled is checked on the ELEMENT, never up the tree: a disabled
  // fieldset legitimately contains controls that are themselves live.
  if (el.matches('[aria-disabled="true"],[data-disabled]')) return null;
  if ((el as HTMLButtonElement).disabled) return null;

  // Its own name is coming. Sounding it as a press today would teach a
  // meaning we would have to un-teach.
  if (el.matches(DEFERRED)) return null;

  // ORDER IS THE RULE. A switch is a `<button>` and a tab usually is
  // too, so the specific shapes have to answer before the general one
  // or everything would be a press.
  if (el.matches(TOGGLE)) return toggleName(el);
  if (el.matches(CHIP)) return 'chip';
  if (el.matches(MENU)) return 'menu_pick';

  return 'press';
}

/**
 * Which way a toggle went.
 *
 * The two kinds disagree about WHEN the state changes, and reading them
 * the same way would get one of them backwards on every click.
 *
 * A native input has already flipped: activation behaviour runs before
 * the click event is dispatched, so `checked` in the capture phase is
 * where the person just put it.
 *
 * An ARIA toggle has not. `switch.tsx` writes `aria-checked` on React's
 * next render, so the attribute here is still the OLD value — and the
 * act is the flip, so the cue is its destination rather than its
 * origin.
 *
 * Deliberately the INTENT and not the outcome. `matrixCells.tsx` writes
 * asynchronously and the server may refuse; waiting to see whether the
 * state really changed would silence every accepted toggle too, since
 * the answer arrives long after the frame. What a refusal earns is the
 * `error` cue the toast lane already raises — "you set it", then "it
 * was refused" — which is a sentence, and one this axis exists to speak
 * the first half of.
 */
function toggleName(el: Element): ActName {
  if (el instanceof HTMLInputElement) return el.checked ? 'toggle_on' : 'toggle_off';
  return el.getAttribute('aria-checked') === 'true' ? 'toggle_off' : 'toggle_on';
}

/**
 * Installed once and never removed — the shape `installKeySound` and the
 * engine's visibility hook both use, for the same reason: a listener
 * that comes and goes with a preference is a listener that stacks. The
 * gate is read at play time, in `cue.ts`, where every other gate is.
 */
let installed = false;
export function installActSound(): void {
  if (installed || typeof document === 'undefined') return;
  installed = true;
  document.addEventListener('click', (e) => {
    // Programmatic clicks are the app moving, not a hand. `.click()` in
    // code, a tour advancing itself, a test driving the page — none of
    // them is an act anybody performed.
    if (!e.isTrusted) return;
    const name = classifyAct(e.target);
    if (name) playActCue(name);
  }, true);
}
