import { audioContext, isUnlocked, type Wave } from './engine';
import type { ItemMeta } from '../store/items/meta';

/**
 * A BED — sound that is simply there, rather than sound that answers.
 *
 * GX ships background music: real tracks, megabytes, fetched. This is
 * the same decision the wallpaper made and for the same reasons —
 * nothing is fetched, nothing is stored, there is no CSP question and
 * no licence to carry. A bed is DESCRIBED: a filtered noise floor and
 * up to three slow tones under it, built by the browser from a few
 * numbers. Road hum and rain are noise shaped by a filter; that is what
 * they physically are, so describing them is not a compromise.
 *
 * It is declarative for the same reason a cue is: the engine knows how
 * to build one and knows nothing about which exist, so a new bed is a
 * file in `store/items/ambience/` and no edit here.
 */
export interface BedTone {
  readonly wave: Wave;
  readonly freq: number;
  readonly gain: number;
}

export interface Bed {
  /** The floor: noise, shaped. */
  readonly filter: 'lowpass' | 'bandpass' | 'highpass';
  readonly freq: number;
  /** Filter sharpness. Higher is narrower; left out means the default. */
  readonly q?: number;
  readonly gain: number;
  /** Slow tones under the floor. Three at most — more is a chord, and a
   *  chord is a piece of music, which is the thing this is not. */
  readonly tones?: readonly BedTone[];
}

export interface AmbiencePack extends ItemMeta {
  readonly bed: Bed;
}

/**
 * What a bed may be.
 *
 * The gain ceiling is a THIRD of a cue's, and that is the whole
 * difference between a sound you notice and a sound you sit inside. A
 * cue is 0.35s; this plays for an eight-hour shift, and loudness that
 * is merely acceptable for a moment is exhausting for a day.
 */
export const BED_LIMITS = {
  freq: { min: 20, max: 12_000 },
  gain: { min: 0, max: 0.12 },
  tones: 3,
} as const;

const inBand = (v: unknown, { min, max }: { min: number; max: number }) =>
  typeof v === 'number' && Number.isFinite(v) && v >= min && v <= max;

export function isBedWithin(b: unknown, limits = BED_LIMITS): b is Bed {
  if (!b || typeof b !== 'object') return false;
  const bed = b as Bed;
  if (!['lowpass', 'bandpass', 'highpass'].includes(bed.filter)) return false;
  if (!inBand(bed.freq, limits.freq) || !inBand(bed.gain, limits.gain)) return false;
  if (bed.q !== undefined && !inBand(bed.q, { min: 0.0001, max: 30 })) return false;
  const tones = bed.tones ?? [];
  if (tones.length > limits.tones) return false;
  return tones.every((t) => inBand(t.freq, limits.freq) && inBand(t.gain, limits.gain));
}

/** Two seconds of brown noise, looped. Brown rather than white because
 *  white is a hiss and brown is a rumble, and every bed here is a
 *  rumble with something taken off the top. */
function noiseBuffer(ctx: AudioContext): AudioBuffer | null {
  try {
    const frames = Math.floor(ctx.sampleRate * 2);
    const buf = ctx.createBuffer(1, frames, ctx.sampleRate);
    const data = buf.getChannelData(0);
    let last = 0;
    for (let i = 0; i < frames; i += 1) {
      const white = Math.random() * 2 - 1;
      last = (last + 0.02 * white) / 1.02;
      data[i] = last * 3.5;
    }
    return buf;
  } catch {
    return null;
  }
}

/** Everything currently sounding, so it can be taken down again. */
let playing: { nodes: AudioNode[]; gain: GainNode } | null = null;

/** Fade, so starting and stopping is never a click in the ears. */
const FADE_S = 0.6;

export function stopBed(): void {
  const p = playing;
  playing = null;
  if (!p) return;
  const ctx = audioContext();
  try {
    if (ctx && p.gain.gain.setTargetAtTime) {
      p.gain.gain.setTargetAtTime(0, ctx.currentTime, FADE_S / 3);
    }
  } catch { /* best-effort */ }
  // Disconnect after the fade has had time to run. A source stopped
  // mid-amplitude is the click this exists to avoid.
  const teardown = () => {
    for (const n of p.nodes) {
      try {
        (n as AudioScheduledSourceNode).stop?.();
        n.disconnect();
      } catch { /* already gone */ }
    }
    try { p.gain.disconnect(); } catch { /* already gone */ }
  };
  if (typeof setTimeout === 'function') setTimeout(teardown, FADE_S * 1000 + 100);
  else teardown();
}

/**
 * Start a bed, or replace the one playing.
 *
 * Silent — never throwing — when audio is locked, muted, unavailable or
 * the bed is malformed, exactly like `playCue`: a sound that fails is a
 * sound nobody hears, a sound that throws is a page that stops.
 */
export function startBed(bed: unknown, volume: number): void {
  stopBed();
  if (!isUnlocked() || volume <= 0 || !isBedWithin(bed)) return;
  const ctx = audioContext();
  if (!ctx) return;
  try {
    const out = ctx.createGain();
    out.gain.value = 0;
    out.connect(ctx.destination);

    const nodes: AudioNode[] = [];
    const buf = noiseBuffer(ctx);
    if (buf) {
      const src = ctx.createBufferSource();
      src.buffer = buf;
      src.loop = true;
      const filter = ctx.createBiquadFilter();
      filter.type = bed.filter;
      filter.frequency.value = bed.freq;
      if (bed.q !== undefined) filter.Q.value = bed.q;
      const g = ctx.createGain();
      g.gain.value = bed.gain;
      src.connect(filter); filter.connect(g); g.connect(out);
      src.start();
      nodes.push(src, filter, g);
    }
    for (const tone of bed.tones ?? []) {
      const osc = ctx.createOscillator();
      osc.type = tone.wave;
      osc.frequency.value = tone.freq;
      const g = ctx.createGain();
      g.gain.value = tone.gain;
      osc.connect(g); g.connect(out);
      osc.start();
      nodes.push(osc, g);
    }
    if (!nodes.length) { out.disconnect(); return; }
    out.gain.setTargetAtTime?.(volume, ctx.currentTime, FADE_S / 3);
    playing = { nodes, gain: out };
  } catch {
    playing = null;   // best-effort, like everything here
  }
}

/** Whether a bed is sounding — for the tests, and for nothing else. */
export const bedIsPlaying = (): boolean => playing !== null;
