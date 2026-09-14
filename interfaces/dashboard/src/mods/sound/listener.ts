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

/**
 * Controls that will get their OWN name in a later stage.
 *
 * Silent now rather than sounding `press` and being changed underneath
 * people later. A cue somebody has learned is a cue that cannot be
 * quietly reassigned — the whole value of the vocabulary is that one
 * sound means one thing for as long as they use the product.
 */
const DEFERRED = [
  '[role="switch"]', '[role="tab"]', '[role="radio"]', '[role="checkbox"]',
  '[aria-pressed]', '[role="menuitem"]', '[role="menuitemradio"]',
  '[role="menuitemcheckbox"]', '[role="option"]',
  '[data-slot="checkbox"]', '[data-slot="radio"]', '[data-slot="select-item"]',
  'input[type="checkbox"]', 'input[type="radio"]',
].join(',');

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
  const el = node.closest(`${PRESSABLE},${DEFERRED}`);
  if (!el) return null;

  // Disabled is checked on the ELEMENT, never up the tree: a disabled
  // fieldset legitimately contains controls that are themselves live.
  if (el.matches('[aria-disabled="true"],[data-disabled]')) return null;
  if ((el as HTMLButtonElement).disabled) return null;

  // Its own name is coming. Sounding it as a press today would teach a
  // meaning we would have to un-teach.
  if (el.matches(DEFERRED)) return null;

  return 'press';
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
