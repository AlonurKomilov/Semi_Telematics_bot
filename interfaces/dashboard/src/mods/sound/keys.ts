/**
 * The keyboard, as four sounds.
 *
 * GX's `keyboard_sounds` block has exactly four event keys —
 * TYPING_LETTER, TYPING_SPACE, TYPING_ENTER, TYPING_BACKSPACE — and no
 * per-physical-key mapping. That granularity is the owner's call and it
 * is the right one twice over: one sound on every key is monotonous, and
 * four classes give the mechanical-keyboard feel a single click cannot.
 *
 * It is also the security ceiling. A per-key mapping would make typing
 * audible as text to anyone in the room; four classes leak word
 * boundaries and corrections and nothing finer — which is still enough
 * to matter, and is why `isSensitiveTarget` below exists.
 *
 * Synthesis, like every other sound here: a click is five numbers, so
 * there is no upload route, no storage, no cache, no CSP and nothing to
 * moderate. Opera's own docs spend their guidance on WAV-vs-mp3 latency;
 * that problem does not exist for us.
 */
import { type Cue, type CueLimits } from './engine';

export const KEY_CLASSES = ['letter', 'space', 'enter', 'backspace'] as const;
export type KeyClass = (typeof KEY_CLASSES)[number];

/**
 * A keyboard click is not a notification cue and must not borrow its
 * bounds. `CUE_LIMITS` floors duration at 20ms, tuned for something you
 * are meant to notice; a key wants 8-25ms, below that floor. The gain
 * ceiling comes down hard too — 0.4 is measured as already loud on
 * headphones for ONE cue, and typing delivers eight a second.
 */
export const KEY_LIMITS: CueLimits = {
  freq: { min: 20, max: 12_000 },
  dur: { min: 0.006, max: 0.05 },
  gain: { min: 0, max: 0.08 },
};

export interface KeyPack {
  readonly id: string;
  readonly label: string;
  readonly cues: Readonly<Record<KeyClass, Cue>>;
}

/**
 * Two packs, and deliberately not more.
 *
 * `click` is a dry board — a short square blip with the pitch dropping
 * on the wider keys, which is what a real keyboard does: a spacebar is
 * bigger, so it sounds lower. `soft` is the same shape with the edge
 * taken off, for somebody who wants to know the key registered without
 * announcing it to the room.
 *
 * Backspace is the one class that moves DOWN in both — a correction
 * should not sound like progress.
 */
export const KEY_PACKS: readonly KeyPack[] = [
  {
    id: 'click',
    label: 'Click',
    cues: {
      letter:    { wave: 'square',   from: 2200, to: 1700, dur: 0.012, gain: 0.045 },
      space:     { wave: 'square',   from: 1500, to: 1100, dur: 0.016, gain: 0.05 },
      enter:     { wave: 'triangle', from: 1800, to: 2400, dur: 0.018, gain: 0.05 },
      backspace: { wave: 'square',   from: 1600, to: 1000, dur: 0.014, gain: 0.042 },
    },
  },
  {
    id: 'soft',
    label: 'Soft',
    cues: {
      letter:    { wave: 'sine',     from: 1400, to: 1150, dur: 0.014, gain: 0.035 },
      space:     { wave: 'sine',     from: 1000, to: 820,  dur: 0.018, gain: 0.038 },
      enter:     { wave: 'triangle', from: 1200, to: 1600, dur: 0.020, gain: 0.038 },
      backspace: { wave: 'sine',     from: 1100, to: 780,  dur: 0.016, gain: 0.032 },
    },
  },
];

export const keyPackById = (id: string): KeyPack | undefined =>
  KEY_PACKS.find((p) => p.id === id);

/**
 * Which of the four a key press is, or null for keys that make no sound.
 *
 * Modifiers, arrows, function keys and shortcuts are silent: they are
 * not typing, and a Ctrl+C that clicks is a sound with no meaning behind
 * it. `event.repeat` is dropped for the reason a held key exists at all
 * — the browser fires it around thirty times a second, and thirty clicks
 * a second is not a keyboard, it is a buzz.
 */
export function classify(e: KeyboardEvent): KeyClass | null {
  if (e.repeat) return null;
  if (e.ctrlKey || e.metaKey || e.altKey) return null;
  if (e.key === 'Enter') return 'enter';
  if (e.key === 'Backspace' || e.key === 'Delete') return 'backspace';
  if (e.key === ' ' || e.key === 'Spacebar') return 'space';
  // One character means a character was typed. Everything longer is a
  // NAME — 'Shift', 'ArrowLeft', 'F5' — and names are not typing.
  return e.key.length === 1 ? 'letter' : null;
}

/**
 * Whether this element must stay silent.
 *
 * Not only passwords, on the owner's instruction: anything of the same
 * shape. Four distinguishable classes make length, word boundaries and
 * correction structure audible to anyone within earshot, and on a shared
 * dispatch floor that is a real channel rather than a theoretical one.
 *
 * Three ways in, on purpose:
 *
 *   - `type="password"` — the obvious one, and free.
 *   - the `autocomplete` token — how the platform already labels a
 *     credential, a card number or a one-time code, so a field that
 *     told the browser what it holds has told us too.
 *   - `data-no-key-sound`, checked on the element AND its ancestors, so
 *     a whole PII fieldset can be silenced in one place rather than
 *     field by field.
 *
 * The public driver application — where the SSN and date of birth live —
 * needs none of these: it mounts on its own React tree outside
 * `ModProvider` and outside preferences, so no sound can reach it at
 * all. `keys.test.ts` pins that so a future refactor that folds the two
 * trees together fails here rather than in the field.
 */
const SENSITIVE_AUTOCOMPLETE = /password|cc-|credit-card|one-time-code|otp/i;

export function isSensitiveTarget(el: EventTarget | null): boolean {
  if (!el || typeof (el as HTMLElement).closest !== 'function') return false;
  const node = el as HTMLElement;
  if (node.closest('[data-no-key-sound]')) return true;
  const input = node as HTMLInputElement;
  if ((input.type || '').toLowerCase() === 'password') return true;
  const ac = input.autocomplete ?? node.getAttribute('autocomplete') ?? '';
  return SENSITIVE_AUTOCOMPLETE.test(ac);
}

/**
 * The floor between two clicks.
 *
 * Every cue builds its own oscillator straight onto the destination, so
 * concurrent cues SUM — the peak is the total, not the loudest. At eight
 * keystrokes a second that is how a click becomes a buzz. 30ms is longer
 * than the longest key cue above (20ms), so two can never overlap, and a
 * click that arrives late is worse than one that never arrives: this
 * DROPS rather than queues.
 *
 * A shared master gain would be the other way to solve it, and is a
 * larger refactor of a file that has none — the limiter alone removes
 * the overlap that makes the summation audible.
 */
const MIN_GAP_MS = 30;
let lastAt = -Infinity;

/** Test seam — the rate limiter is module state like the context is. */
export function resetKeySoundForTests(): void {
  lastAt = -Infinity;
}

/**
 * The cue one key press earns, or null.
 *
 * PURE of preferences on purpose. This file is a leaf: the registry
 * imports `KEY_PACKS` to sanitise the pack name, so a `preferences`
 * import here would close the ring registry → keys → preferences →
 * registry. The gate and the volume are read one layer up, in `cue.ts`,
 * which is already the preferences-aware layer.
 *
 * The rate limit lives here rather than there because it is a property
 * of the keyboard, not of the person: it is what stops eight keystrokes
 * a second summing into a buzz, and it must count every press that got
 * this far whether or not a pack resolves.
 */
export function pickKeyCue(e: KeyboardEvent, packId: string): Cue | null {
  const cls = classify(e);
  if (!cls) return null;
  if (isSensitiveTarget(e.target)) return null;

  const now = performance.now();
  if (now - lastAt < MIN_GAP_MS) return null;
  lastAt = now;

  return keyPackById(packId)?.cues[cls] ?? null;
}
