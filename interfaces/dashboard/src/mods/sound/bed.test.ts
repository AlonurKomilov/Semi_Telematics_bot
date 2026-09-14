/**
 * The bed: what a shipped one may be, and what the engine does with it.
 *
 * A cue is 0.35s and a bed is a shift, so the interesting rules here are
 * about NOT being heard: quiet enough to sit inside, silent until a
 * gesture, and never two at once.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readdirSync } from 'node:fs';
import { join } from 'node:path';
import {
  BED_LIMITS, isBedWithin, startBed, stopBed, bedIsPlaying,
} from './bed';
import { CUE_LIMITS, resetAudioForTests, armAudio, audioGraphForTests } from './engine';
import { AMBIENCE_PACKS } from '../store/items/ambience';

describe('what a bed may be', () => {
  it('there are beds to check', () => {
    expect(AMBIENCE_PACKS.length, 'the shelf is empty').toBeGreaterThan(1);
  });

  it('every shipped bed is inside the band', () => {
    for (const a of AMBIENCE_PACKS)
      expect(isBedWithin(a.bed), `${a.id} is outside BED_LIMITS`).toBe(true);
  });

  it('and the band is far quieter than a cue', () => {
    // The whole difference between a sound you notice and a sound you
    // sit inside. A cue is a third of a second; this plays for a shift,
    // and loudness that is merely acceptable for a moment is exhausting
    // for a day.
    expect(BED_LIMITS.gain.max).toBeLessThan(CUE_LIMITS.gain.max / 2);
  });

  it('refuses a bed that is too loud, mis-shaped, or a chord', () => {
    const ok = { filter: 'lowpass', freq: 300, gain: 0.05 } as const;
    expect(isBedWithin(ok)).toBe(true);
    expect(isBedWithin({ ...ok, gain: 0.4 }), 'a cue-loud bed passed').toBe(false);
    expect(isBedWithin({ ...ok, filter: 'notch' }), 'an unknown filter passed').toBe(false);
    expect(isBedWithin({ ...ok, freq: 0 }), 'a sub-audible floor passed').toBe(false);
    expect(isBedWithin({ ...ok, tones: [1, 2, 3, 4].map(() => ({ wave: 'sine', freq: 100, gain: 0.01 })) }),
      'four tones is a chord, and a chord is music').toBe(false);
    expect(isBedWithin(null)).toBe(false);
  });

  it('a bed is a file, and the index is exactly the files', () => {
    const dir = join(__dirname, '..', 'store', 'items', 'ambience');
    const files = readdirSync(dir)
      .filter((f) => /\.ts$/.test(f) && !/^index\.ts$|\.test\.ts$/.test(f))
      .map((f) => f.replace(/\.ts$/, '')).sort();
    expect(AMBIENCE_PACKS.map((a) => a.id).sort()).toEqual(files);
  });
});

/**
 * jsdom has no AudioContext, and without a stub `startBed` returns early
 * for THAT reason — every assertion below would pass whatever the engine
 * did. The engine's own test file records the same lesson; this is it,
 * applied to the bed.
 */
const counts = { started: 0, stopped: 0 };
/** Where the bed's output landed, so "on the action side" is read rather
 *  than assumed. */
const wiredTo: string[] = [];
function stubAudio() {
  counts.started = 0; counts.stopped = 0;
  wiredTo.length = 0;
  const node = (tag = 'node') => ({
    tag,
    connect(to: { tag?: string }) { wiredTo.push(to?.tag ?? 'destination'); },
    disconnect: () => {},
    start: () => { counts.started += 1; }, stop: () => { counts.stopped += 1; },
    buffer: null, loop: false, type: 'sine',
    frequency: { value: 0 }, Q: { value: 0 },
    gain: { value: 0, setTargetAtTime: () => {} },
  });
  (window as unknown as { AudioContext: unknown }).AudioContext = class {
    state = 'running';
    currentTime = 0;
    sampleRate = 48_000;
    destination = { tag: 'destination' };
    createGain = () => node('gain');
    createBufferSource = () => node('src');
    createBiquadFilter = () => node('filter');
    createOscillator = () => node('osc');
    // The shared graph needs one. Without it `buildGraph` falls back and
    // the bed connects to the destination — which still PLAYS, so every
    // assertion in this file passed while the bed quietly left the side
    // that ducks under an alert.
    // A REAL compressor shape. The generic node above has no
    // `threshold`, so `buildGraph` threw on it, no graph was ever built,
    // and the routing test below passed whichever side the bed went to —
    // vacuous, and it took a mutation to notice.
    createDynamicsCompressor = () => ({
      ...node('limiter'),
      threshold: { value: 0 }, knee: { value: 0 },
      ratio: { value: 0 }, attack: { value: 0 }, release: { value: 0 },
    });
    createBuffer = (_ch: number, frames: number) => ({
      getChannelData: () => new Float32Array(frames),
    });
    resume = () => Promise.resolve();
    suspend = () => Promise.resolve();
  };
}

const unlock = () => {
  armAudio();
  window.dispatchEvent(new Event('pointerdown'));
};

describe('the engine keeps quiet until it is allowed not to', () => {
  beforeEach(() => { stopBed(); resetAudioForTests(); stubAudio(); });

  it('plays a real bed once everything allows it — the positive control', () => {
    // Without this, every "plays nothing" below passes because the
    // engine plays nothing ever.
    unlock();
    startBed(AMBIENCE_PACKS[0].bed, 1);
    expect(bedIsPlaying(), 'the engine cannot play a bed at all').toBe(true);
  });

  /**
   * The bed is on the ACTION side, not the destination.
   *
   * It is not the app answering — it is the room tone under everything
   * you do, and it has to drop out of the way when an alert arrives.
   * Before the two buses existed it connected straight to the
   * destination, where nothing could move it; the whole point of the
   * graph is that the alert wins.
   *
   * Read as "only the limiter reaches the destination", because that is
   * the invariant the graph exists to hold, and it catches every future
   * node that wires itself out of the mix as well as this one.
   */
  it('plays into the side that ducks, never straight out', () => {
    unlock();
    startBed(AMBIENCE_PACKS[0].bed, 1);
    expect(counts.started, 'nothing played — the assertion below is vacuous')
      .toBeGreaterThan(0);
    // The OTHER way this goes vacuous, and the one that actually
    // happened: with no graph built, every route falls back to the
    // destination and the count below is 1 whatever the bed does.
    expect(audioGraphForTests().action, 'no graph — this proves nothing')
      .toBeTruthy();
    expect(
      wiredTo.filter((t) => t === 'destination'),
      'something reached the destination directly. Only the limiter may: '
        + 'anything else is a sound an alert cannot duck.',
    ).toHaveLength(1);
  });

  it('and stops when asked', () => {
    unlock();
    startBed(AMBIENCE_PACKS[0].bed, 1);
    stopBed();
    expect(bedIsPlaying()).toBe(false);
  });

  it('never layers two — a second start stops the first', () => {
    // Counted, not inferred. `bedIsPlaying()` is one boolean: a second
    // bed started over the first leaves it true either way, while the
    // first bed's sources keep sounding under the new one and every
    // switch adds another layer.
    vi.useFakeTimers();
    try {
      unlock();
      startBed(AMBIENCE_PACKS[0].bed, 1);
      const first = counts.started;
      expect(first, 'the stub recorded no sources').toBeGreaterThan(0);
      startBed(AMBIENCE_PACKS[1].bed, 1);
      vi.advanceTimersByTime(2_000);
      expect(counts.stopped, 'the first bed was left sounding under the second')
        .toBeGreaterThanOrEqual(first);
    } finally {
      vi.useRealTimers();
    }
  });

  it('plays nothing before a gesture has unlocked audio', () => {
    startBed(AMBIENCE_PACKS[0].bed, 1);
    expect(bedIsPlaying(), 'a page started making noise on its own').toBe(false);
  });

  it('plays nothing at zero volume, however unlocked', () => {
    unlock();
    startBed(AMBIENCE_PACKS[0].bed, 0);
    expect(bedIsPlaying(), 'a silenced app played a bed').toBe(false);
  });

  it('plays nothing for a bed outside the band', () => {
    unlock();
    startBed({ filter: 'lowpass', freq: 300, gain: 0.9 }, 1);
    expect(bedIsPlaying(), 'a bed nobody validated reached the speakers').toBe(false);
  });
});
