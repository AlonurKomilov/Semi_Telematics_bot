/**
 * The cue player for everything that is not a React component.
 *
 * `useCue` is a hook, and the four cues nobody could hear were unheard
 * largely because of that: an undo window opens inside `undoableAction`,
 * a plain function in a banners module, and a hook cannot go there.
 * Every attempt to wire one ended at the rules-of-hooks lint, which runs
 * in CI. So the pack-and-volume resolution moves here, `useCue` delegates
 * to it, and there is still exactly one implementation.
 *
 * Reads through `preferences.get` rather than `usePreference` — the same
 * shape `features/alerts/bannerLevel.ts` uses for the same reason, and the
 * store keeps the value live across tabs on its own.
 *
 * **Imported by its file path, never through `../index`.** `mods/index`
 * exports `ModPanel`, `ModPanel` imports `undoableAction`, and a barrel
 * import from a banners module would close that ring. Vite reports
 * nothing; the binding is simply `undefined` at module-init time. The
 * rule is stated in `mods/index.ts` and already practised by
 * `preferences/registry.ts`.
 */
import { preferences } from '../../preferences';
import { armAudio, playCue, soundPackById, type CueName } from './engine';
import { pickKeyCue, KEY_LIMITS } from './keys';

/**
 * Play an interface cue, if this screen has asked for interface sound.
 *
 * The gate is checked HERE rather than at each call site. A caller that
 * has to remember to ask is a caller that forgets, and the failure is
 * silent in the worst direction — sound nobody consented to, on a shared
 * office floor. `mods.sound.volume` is a level and defaults to 1, so
 * `mods.sound.ui` is the only thing between a fresh account and noise.
 *
 * Alert sound does NOT come through here. It has its own gate
 * (`dispatch.soundOn`) and its own call site, and folding the two would
 * mean one switch answering two questions.
 */
export function playUiCue(name: CueName): void {
  if (!preferences.get('mods.sound.ui')) return;
  const volume = preferences.get('mods.sound.volume');
  if (volume <= 0) return;
  const cue = soundPackById(preferences.get('mods.sound.pack'))?.cues[name];
  if (cue) playCue(cue, volume);
}

/**
 * Start listening for the gesture that unlocks audio — but only for a
 * screen that has asked for sound.
 *
 * Arming is not free: the listener it installs creates an AudioContext
 * on the first click, and a context is never torn down. Six of the nine
 * roles had no path to arming at all, which is why their volume dial was
 * decorative; the fix is not to arm everyone, it is to arm whoever has a
 * sound gate on.
 */
export function armIfWanted(): void {
  if (preferences.get('mods.sound.ui')
    || preferences.get('dispatch.soundOn')
    || preferences.get('mods.sound.keyboard')) armAudio();
}

/**
 * The click for one key press.
 *
 * The gate is `mods.sound.keyboard`, not `mods.sound.ui`: somebody may
 * want to hear an undo window open and not want to hear every letter
 * they type. Two questions, two switches.
 *
 * Volume is shared, and deliberately so. `engine.test.ts` holds the line
 * that Sounds has exactly ONE intensity — two numbers multiplying into
 * one gain is how a person ends up at 40% of 40%, hears almost nothing,
 * and concludes the feature is broken.
 */
export function playKeyCue(e: KeyboardEvent): void {
  if (!preferences.get('mods.sound.keyboard')) return;
  const volume = preferences.get('mods.sound.volume');
  if (volume <= 0) return;
  const cue = pickKeyCue(e, preferences.get('mods.sound.keyboard.pack'));
  if (cue) playCue(cue, volume, KEY_LIMITS);
}

/**
 * One `keydown` listener for the window, installed when the gate is on
 * and never removed — the same shape as the visibility hook in the
 * engine, and for the same reason: a listener that comes and goes with a
 * preference is a listener that stacks.
 *
 * `keydown` rather than `keyup`: a real keyboard makes its noise when
 * the key goes down.
 */
let keySoundInstalled = false;
export function installKeySound(): void {
  if (keySoundInstalled || typeof window === 'undefined') return;
  keySoundInstalled = true;
  window.addEventListener('keydown', playKeyCue);
}
