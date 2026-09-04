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


// ── The notification lane ────────────────────────────────────────────

/**
 * What a banner may sound: a cue by name, or a tone to map from.
 *
 * `Tone` is not imported — `lib/status` is a UI module and this file is
 * a leaf the preferences registry depends on. The five tone names are
 * restated as a union instead, and `bannerCue.test.ts` checks the two
 * lists agree.
 */
export type BannerTone = 'ok' | 'warn' | 'danger' | 'info' | 'neutral';
export type BannerCue = CueName | BannerTone;

/**
 * Priority, as sound.
 *
 * The banner already knows how serious it is — `LiveAlertWatcher` maps
 * an alert's severity to a tone before it raises one — so the tone is
 * the priority signal, already computed, already on screen as colour.
 * This is the same fact said out loud.
 *
 * `info` and `neutral` are deliberately silent. A notification lane
 * that chimes for everything trains people to stop hearing it, and the
 * things that arrive as `info` are the ones nobody needs to look up for.
 */
const TONE_CUE: Readonly<Record<BannerTone, CueName | null>> = {
  danger: 'critical',
  warn: 'alert',
  ok: 'success',
  info: null,
  neutral: null,
};

const isTone = (v: BannerCue): v is BannerTone => v in TONE_CUE;

/**
 * The floor between two banner cues.
 *
 * `LiveAlertWatcher` raises up to three banners on one poll tick, and
 * every cue connects its own gain straight to the destination — so
 * three would SUM into one loud noise rather than three sounds. One
 * poll should announce itself once. 400ms is longer than the longest
 * cue (0.45s critical is the outlier and overlaps by 50ms at worst,
 * which is inaudible) and far shorter than the 60s poll, so two
 * genuinely separate arrivals are never merged.
 *
 * DROP, not queue: a chime that arrives after the banner it belongs to
 * has been read is worse than no chime.
 */
const BANNER_GAP_MS = 400;
let lastBannerAt = -Infinity;

/** Test seam — the floor is module state, like the audio context. */
export function resetBannerCueForTests(): void {
  lastBannerAt = -Infinity;
}

/**
 * Sound one notification.
 *
 * Gated by `dispatch.soundOn`, which is the switch that has always meant
 * "tell me out loud when something arrives" — its own note says so. It
 * is device-scoped and off by default, because a shared dispatch floor
 * is the room this feature lives in.
 */
export function playBannerCue(cue: BannerCue): void {
  if (!preferences.get('dispatch.soundOn')) return;
  const name = isTone(cue) ? TONE_CUE[cue] : cue;
  if (!name) return;
  const volume = preferences.get('mods.sound.volume');
  if (volume <= 0) return;

  const now = performance.now();
  if (now - lastBannerAt < BANNER_GAP_MS) return;
  lastBannerAt = now;

  const c = soundPackById(preferences.get('mods.sound.pack'))?.cues[name];
  if (c) playCue(c, volume);
}
