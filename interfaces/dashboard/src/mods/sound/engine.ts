/**
 * Cues, as synthesis parameters — not as files.
 *
 * The obvious build is a folder of mp3s and a loader, and it is the
 * wrong one here. The app's only existing audio already synthesises, and
 * says why: no bundle weight, no hosted-audio caching to reason about.
 * Following that has a consequence worth naming, because it decides the
 * shape of the whole feature:
 *
 *   A sound pack made of NUMBERS needs no asset pipeline at all.
 *
 * No bytes, so no upload route, no storage quota, no IndexedDB, no cache
 * headers, no CSP, and nothing to moderate. It rides the same validated
 * manifest the colour tokens do — a handful of numbers in stated ranges
 * — which means the per-user version of this is a few sliders rather
 * than a file picker and everything behind one.
 *
 * What it costs: no recorded sound. No voice, no sampled click. For an
 * operations dashboard that is a fair trade, and arguably the better
 * one — a synthesised cue is instant, weighs nothing, and cannot fail to
 * load at the moment it is needed.
 */

export type CueName =
  /** An alert arrived. */
  | 'alert'
  /** An alert arrived that cannot wait. */
  | 'critical'
  /** Something was saved. */
  | 'success'
  /** Something was refused. */
  | 'error'
  /** A destructive action, and its window to undo. */
  | 'undo';

export const CUE_NAMES: readonly CueName[] =
  ['alert', 'critical', 'success', 'error', 'undo'];

export type Wave = 'sine' | 'triangle' | 'square' | 'sawtooth';
export const WAVES: readonly Wave[] = ['sine', 'triangle', 'square', 'sawtooth'];

/** One cue: a glide from one pitch to another under a decaying envelope. */
export interface Cue {
  readonly wave: Wave;
  /** Hz at the start and at the end. Equal values hold a single pitch. */
  readonly from: number;
  readonly to: number;
  /** Seconds. */
  readonly dur: number;
  /** Peak gain before the listener's own volume, 0..1. */
  readonly gain: number;
}

export interface SoundPack {
  readonly id: string;
  readonly label: string;
  readonly cues: Readonly<Record<CueName, Cue>>;
}

// ── the bounds a cue must live inside ────────────────────────────────
// Not defensive padding: these are what stops a pack — ours today, a
// person's later — from producing something that hurts. 20 Hz and
// 12 kHz keep it inside what a laptop speaker reproduces without
// distorting; a second and a half is the longest a UI cue can be before
// it is in the way; 0.4 is measured against the existing chime's 0.18
// peak and is already loud on headphones.
export const CUE_LIMITS = {
  freq: { min: 20, max: 12_000 },
  dur: { min: 0.02, max: 1.5 },
  gain: { min: 0, max: 0.4 },
} as const;

const inRange = (v: unknown, { min, max }: { min: number; max: number }) =>
  typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max;

export function isSafeCue(c: unknown): c is Cue {
  if (!c || typeof c !== 'object') return false;
  const q = c as Partial<Cue>;
  return (WAVES as readonly string[]).includes(q.wave as string)
    && inRange(q.from, CUE_LIMITS.freq)
    && inRange(q.to, CUE_LIMITS.freq)
    && inRange(q.dur, CUE_LIMITS.dur)
    && inRange(q.gain, CUE_LIMITS.gain);
}

// ── the packs ────────────────────────────────────────────────────────

/**
 * `chime` is not a new sound. Its `alert` cue is the one this app has
 * always played — 880 Hz gliding to 440 over 0.35s at gain 0.18, a sine
 * — reproduced exactly, so turning the engine on changes nothing anybody
 * would notice. The other four are the same voice answering different
 * questions.
 */
export const SOUND_PACKS: readonly SoundPack[] = [
  {
    id: 'chime',
    label: 'Chime',
    cues: {
      alert:    { wave: 'sine', from: 880,  to: 440,  dur: 0.35, gain: 0.18 },
      // Two things separate critical from alert without being louder:
      // it starts higher and falls further. Loudness is the listener's
      // setting, not ours to spend on urgency.
      critical: { wave: 'sine', from: 1320, to: 330,  dur: 0.45, gain: 0.20 },
      // Rising, because everything that went right rises.
      success:  { wave: 'sine', from: 660,  to: 990,  dur: 0.16, gain: 0.14 },
      error:    { wave: 'triangle', from: 320, to: 190, dur: 0.28, gain: 0.16 },
      // Short and neutral: it marks that a window opened, and the window
      // is the message.
      undo:     { wave: 'sine', from: 520,  to: 520,  dur: 0.10, gain: 0.12 },
    },
  },
  {
    id: 'blip',
    label: 'Blip',
    // Square waves, short. Reads as instrumentation rather than
    // notification — for a yard terminal where a chime sounds like a
    // phone somebody left on a desk.
    cues: {
      alert:    { wave: 'square', from: 1000, to: 1000, dur: 0.06, gain: 0.10 },
      critical: { wave: 'square', from: 1400, to: 700,  dur: 0.14, gain: 0.13 },
      success:  { wave: 'square', from: 1200, to: 1600, dur: 0.05, gain: 0.08 },
      error:    { wave: 'sawtooth', from: 240, to: 160, dur: 0.16, gain: 0.12 },
      undo:     { wave: 'square', from: 800,  to: 800,  dur: 0.04, gain: 0.08 },
    },
  },
];

export const soundPackById = (id: string): SoundPack | undefined =>
  SOUND_PACKS.find((p) => p.id === id);

// ── the engine ───────────────────────────────────────────────────────

let ctx: AudioContext | null = null;
let unlocked = false;

/**
 * ONE context, created lazily and never torn down.
 *
 * The existing chime builds and closes a context per call, which is
 * correct for one sound a minute and unusable for a pack: browsers cap
 * concurrent AudioContexts at around six, and construction is not free.
 * A single context that outlives the page is what every audio library
 * does, for this reason.
 */
function context(): AudioContext | null {
  if (ctx) return ctx;
  try {
    const Ctor = (window as unknown as { AudioContext?: typeof AudioContext }).AudioContext
      ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Ctor) return null;
    ctx = new Ctor();
    hookLifecycle();
    return ctx;
  } catch {
    return null;   // audio is best-effort and never breaks a page
  }
}

/**
 * Stop the audio callback while nobody is looking.
 *
 * The context above is never torn down, deliberately — but "never torn
 * down" used to mean the audio thread ran for the whole session, and a
 * tablet in a cab has that session open for a shift. Suspending on hide
 * costs nothing: the alert poll is frozen by the query client while the
 * document is hidden, so no cue can arrive during the window anyway.
 *
 * An idle TIMER was the other half of this and is deliberately not here.
 * It would have to fire on a visible screen, which is precisely where a
 * cue must not be late — and the case it was meant for, a backgrounded
 * tablet, is the case where browsers clamp `setTimeout` to a minute or
 * more and the timer is least reliable. Hiding is the signal; a clock
 * guessing at hiding is not.
 *
 * Installed once, after a context exists, and never removed. The handler
 * reads the module-scope `ctx` rather than closing over one, so
 * `resetAudioForTests` nulling it is automatically safe and there is no
 * stacked-listener class of bug to reason about. The pattern is
 * `components/banners/stagedAction.tsx`'s, for the same reasons.
 */
let lifecycleHooked = false;
function hookLifecycle(): void {
  if (lifecycleHooked || typeof document === 'undefined') return;
  lifecycleHooked = true;
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) suspendIfRunning();
  });
}

function suspendIfRunning(): void {
  const c = ctx;
  // `state` is the browser's, and the browser suspends contexts under
  // policies of its own — so it is the only authority. A boolean of our
  // own tracking running-ness would be wrong the first time Chrome
  // suspended one without asking.
  if (!c || typeof c.suspend !== 'function' || c.state !== 'running') return;
  void c.suspend().catch(() => { /* best-effort, like everything here */ });
}

/**
 * Called from a real gesture, because browsers require one.
 *
 * The pattern is already in the acknowledge panel, which plays on the
 * click that enables sound precisely so the grant lands. This is that,
 * extracted: any click anywhere unlocks, and after the first one the
 * listener removes itself.
 */
export function armAudio(): void {
  if (unlocked) return;
  const arm = () => {
    unlocked = true;
    void context()?.resume().catch(() => { /* best-effort */ });
    window.removeEventListener('pointerdown', arm);
    window.removeEventListener('keydown', arm);
  };
  window.addEventListener('pointerdown', arm, { once: true });
  window.addEventListener('keydown', arm, { once: true });
}

/**
 * Play one cue at a listener's volume.
 *
 * Silent — not throwing — when audio is unavailable, locked, muted or
 * the cue is malformed. A sound that fails is a sound nobody hears; a
 * sound that throws is a page that stops.
 */
export function playCue(cue: Cue, volume: number): void {
  if (!unlocked || volume <= 0 || !isSafeCue(cue)) return;
  const c = context();
  if (!c) return;
  // A suspended context has a FROZEN CLOCK. Scheduling against
  // `currentTime` here would place the note at a moment that never
  // arrives, `onended` would never fire, and the disconnect below would
  // never run — so every cue played while suspended would leak an
  // oscillator and a gain node onto the graph permanently. Resume first,
  // then emit from the promise. Nothing is awaited on the way in: the
  // signature stays `void`, and the rejection path stays swallowed.
  if (c.state === 'suspended' && typeof c.resume === 'function') {
    void c.resume().then(() => emit(c, cue, volume)).catch(() => { /* best-effort */ });
    return;
  }
  emit(c, cue, volume);
}

/** The oscillator itself, once the context is known to be running. */
function emit(c: AudioContext, cue: Cue, volume: number): void {
  try {
    const osc = c.createOscillator();
    const gain = c.createGain();
    osc.connect(gain);
    gain.connect(c.destination);
    osc.type = cue.wave;
    const t = c.currentTime;
    osc.frequency.setValueAtTime(cue.from, t);
    // Exponential, because pitch is heard logarithmically — a linear
    // ramp between the same two numbers sounds like it stalls at the top.
    // It also cannot reach zero, hence the floor on both ends.
    osc.frequency.exponentialRampToValueAtTime(Math.max(cue.to, 1), t + cue.dur * 0.6);
    const peak = Math.max(cue.gain * volume, 0.0001);
    gain.gain.setValueAtTime(peak, t);
    gain.gain.exponentialRampToValueAtTime(0.0001, t + cue.dur);
    osc.start(t);
    osc.stop(t + cue.dur + 0.02);
    // The node graph is released when it ends; the CONTEXT stays.
    osc.onended = () => { try { osc.disconnect(); gain.disconnect(); } catch { /* gone */ } };
  } catch {
    /* best-effort */
  }
}

/** Test seam: forget the context and the gesture grant. */
export function resetAudioForTests(): void {
  try { void ctx?.close(); } catch { /* ignore */ }
  ctx = null;
  unlocked = false;
  // NOT `lifecycleHooked`. The listener is never removed — resetting the
  // flag would let the next context stack a second one, and the handler
  // reads module-scope `ctx` so the existing one keeps working across
  // every reset. One listener for the life of the document is the whole
  // design; forgetting it here is what would break it.
}
