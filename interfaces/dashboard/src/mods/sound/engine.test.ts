/**
 * The bounds are the feature.
 *
 * A pack made of numbers is safe only while the numbers are checked —
 * and the reason to check them now, while every pack is ours, is that
 * the point of the arc is that they will not always be. A person
 * authoring a cue can reach for 20 kHz at full gain by accident far
 * more easily than they can write a malformed colour.
 */
import { describe, it, expect, beforeEach, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  CUE_NAMES, CUE_LIMITS, WAVES, isSafeCue, playCue, armAudio, resetAudioForTests,
  audioGraphForTests, actionDestination, duckActions, type Cue,
} from './engine';
import { SOUND_PACKS, soundPackById } from '../store/items/sound';

beforeEach(() => { resetAudioForTests(); });

/** The limiter node, for every fake context in this file. */
const compressor = () => ({
  connect: () => {}, disconnect: () => {},
  threshold: { value: 0 }, knee: { value: 0 },
  ratio: { value: 0 }, attack: { value: 0 }, release: { value: 0 },
});

describe('the packs we ship', () => {
  it('answers every cue the app can ask for', () => {
    // A missing cue is silence at the moment something happened, which
    // reads as the feature being off rather than as a gap in a pack.
    for (const pack of SOUND_PACKS)
      for (const name of CUE_NAMES)
        expect(pack.cues[name], `${pack.id} has no "${name}" cue`).toBeDefined();
  });

  it('stays inside the bounds, every cue of it', () => {
    for (const pack of SOUND_PACKS)
      for (const name of CUE_NAMES)
        expect(isSafeCue(pack.cues[name]), `${pack.id}.${name} is out of bounds`).toBe(true);
  });

  it('reproduces the chime this app has always played', () => {
    // Turning the engine on must change nothing anybody would notice.
    // These four numbers are the ones the old `playChime` used, and if
    // they move, somebody has redesigned a sound while adding a feature.
    expect(soundPackById('chime')!.cues.alert)
      .toEqual({ wave: 'sine', from: 880, to: 440, dur: 0.35, gain: 0.18 });
  });

  it('has ids that are unique and usable as a stored value', () => {
    const ids = SOUND_PACKS.map((p) => p.id);
    expect(new Set(ids).size).toBe(ids.length);
    for (const p of SOUND_PACKS) {
      expect(p.id).toMatch(/^[a-z][a-z0-9-]*$/);
      expect(p.label.trim()).not.toBe('');
      expect(soundPackById(p.id)).toBe(p);
    }
    expect(soundPackById('nope')).toBeUndefined();
  });
});

describe('what a cue may contain', () => {
  const ok: Cue = { wave: 'sine', from: 440, to: 440, dur: 0.2, gain: 0.1 };

  it('accepts a well-formed cue', () => {
    expect(isSafeCue(ok)).toBe(true);
    for (const wave of WAVES) expect(isSafeCue({ ...ok, wave })).toBe(true);
  });

  it('refuses what would hurt to hear', () => {
    // Each of these is a plausible slip in a hand-authored pack rather
    // than an attack: a gain of 1, a frequency in kHz because the field
    // said "frequency", a duration in milliseconds.
    for (const bad of [
      { ...ok, gain: 1 },              // four times the ceiling
      { ...ok, gain: -0.1 },
      { ...ok, from: 19_000 },         // above what a laptop reproduces
      { ...ok, to: 0 },
      { ...ok, dur: 5 },               // a cue that outlasts the action
      { ...ok, dur: 0 },
      { ...ok, wave: 'noise' },
    ]) expect(isSafeCue(bad), JSON.stringify(bad)).toBe(false);
  });

  it('refuses what is not a cue at all', () => {
    for (const bad of [null, undefined, 42, 'sine', [], {}, { wave: 'sine' }])
      expect(isSafeCue(bad), JSON.stringify(bad)).toBe(false);
    expect(isSafeCue({ ...ok, from: NaN })).toBe(false);
    expect(isSafeCue({ ...ok, dur: Infinity })).toBe(false);
  });

  it('states bounds that are ordered and non-empty', () => {
    for (const [k, r] of Object.entries(CUE_LIMITS))
      expect(r.min, `${k} bounds are inverted`).toBeLessThan(r.max);
    // Silence must be expressible; a cue at gain 0 is how a pack turns
    // one event off without removing it.
    expect(CUE_LIMITS.gain.min).toBe(0);
  });
});

describe('playing is best-effort and never throws', () => {
  const ok: Cue = { wave: 'sine', from: 440, to: 440, dur: 0.2, gain: 0.1 };

  it('survives audio not being available at all', () => {
    // jsdom ships no Web Audio, which is also the case that matters most
    // on a real device. A sound that fails is a sound nobody hears; one
    // that throws is a page that stops.
    expect(() => playCue(ok, 1)).not.toThrow();
    expect(() => playCue(null as unknown as Cue, 1)).not.toThrow();
  });

  /**
   * A stub, because without one this file cannot reach the branch that
   * matters. Every assertion below passed against the real engine with
   * its cue check DELETED — jsdom has no AudioContext, so `playCue`
   * returned early either way and the guard was measuring nothing.
   */
  function stubAudio() {
    const started: unknown[] = [];
    // Recorded so the volume chain can be measured rather than read.
    const peaks: number[] = [];
    let gainStages = 0;
    const node = () => ({
      connect: () => {}, disconnect: () => {},
      frequency: { setValueAtTime: () => {}, exponentialRampToValueAtTime: () => {} },
      gain: {
        setValueAtTime: (v: number) => { peaks.push(v); },
        exponentialRampToValueAtTime: () => {},
      },
      start: (t: number) => { started.push(t); }, stop: () => {},
      type: 'sine', onended: null,
    });
    (window as unknown as { AudioContext: unknown }).AudioContext = class {
      currentTime = 0;
      destination = {};
      // `state` and `suspend` are real here, and mutable, because the
      // engine now reads and writes them. A stub without them does not
      // fail loudly: a missing `suspend` throws inside a listener and
      // vitest reports an Errors line while the summary still says every
      // test passed. The state a browser owns is modelled, not asserted
      // away.
      state = 'running';
      createOscillator() { return node(); }
      createGain() { gainStages += 1; return node(); }
      createDynamicsCompressor() { return compressor(); }
      resume() { this.state = 'running'; return Promise.resolve(); }
      suspend() { this.state = 'suspended'; return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
    return Object.assign(started, {
      peaks,
      get gainStages() { return gainStages; },
    });
  }

  it('plays a good cue and refuses a malformed one, once unlocked', () => {
    const started = stubAudio();
    armAudio();
    // The gesture the browser requires, and the engine waits for.
    window.dispatchEvent(new Event('pointerdown'));

    playCue(ok, 1);
    expect(started.length, 'a well-formed cue did not play').toBe(1);

    for (const bad of [
      { ...ok, gain: 99 }, { ...ok, from: 40_000 }, { ...ok, dur: 9 },
      { ...ok, wave: 'noise' }, null,
    ]) playCue(bad as unknown as Cue, 1);
    expect(started.length, 'a malformed cue reached the oscillator').toBe(1);
  });

  it('stays silent until a gesture grants audio', () => {
    // Not a nicety — browsers refuse to start audio without one, and a
    // page that tries anyway gets a console warning on every attempt.
    // Tested WITH the stub installed, because without it there is no
    // AudioContext and the assertion passes for the wrong reason.
    const started = stubAudio();
    armAudio();
    playCue(ok, 1);
    expect(started.length, 'audio started before any gesture').toBe(0);
    window.dispatchEvent(new Event('pointerdown'));
    playCue(ok, 1);
    expect(started.length, 'the gesture did not unlock audio').toBe(1);
  });

  it('stays silent at zero volume even when everything works', () => {
    const started = stubAudio();
    armAudio();
    window.dispatchEvent(new Event('pointerdown'));
    playCue(ok, 0);
    expect(started.length).toBe(0);
  });
});

describe('the level is not a second gate', () => {
  it('does not default to silence', async () => {
    // This was wrong first. `dispatch.soundOn` is already the opt-in — a
    // device boolean defaulting to false, with its own toggle in the
    // live panel. A volume of 0 by default would double-gate it: turn
    // the toggle on, hear nothing, conclude the feature is broken.
    const { DEFS } = await import('../../preferences/registry');
    expect(DEFS['mods.sound.volume'].default, 'volume defaults to silence again')
      .toBeGreaterThan(0);
    expect(DEFS['dispatch.soundOn'].default, 'the real opt-in stopped being opt-in')
      .toBe(false);
  });
});

describe('the panel section', () => {
  const panel = readFileSync(
    join(__dirname, '..', 'panel', 'Sounds.tsx'), 'utf8');

  it('offers every pack', () => {
    // Generated from what the store hands out, so adding a pack cannot
    // half-land as a set of cues nobody can select — and a pack this
    // install does not carry cannot be offered.
    expect(panel).toContain("offered('sound', SOUND_PACKS");
  });

  it('previews the pack that was clicked, not the one that was stored', () => {
    // `setValue` is async. Previewing through the stored id plays the
    // pack you just LEFT, which is the kind of bug that reads as the
    // preview being broken rather than as one frame of staleness.
    const onClick = /SOUND_PACKS[\s\S]{0,80}?\.map\([\s\S]{0,600}?preview\((\w+)/.exec(panel);
    expect(onClick, 'the pack chips no longer preview').not.toBeNull();
    expect(onClick![1], 'the preview reads a stored id instead of the clicked pack')
      .toBe('p');
  });

  it('restores the level it silenced, not a default', () => {
    // A mute that comes back at 100% is a mute people stop using.
    expect(panel).toContain('beforeMute');
    expect(panel).toMatch(/beforeMute\.current = volume/);
  });

  it('carries the alert gate as a CONTROL, not a pointer to one', () => {
    // The audit's sharpest finding was that this section could read 100%
    // while the product was silent, because the switch lived in the
    // alerts panel. Naming where it lived was the first fix and only
    // half of one: that panel renders for three of the nine roles, so
    // the other six were told where to go and could not go there. The
    // switch is here now, on the surface every role can see.
    expect(panel, 'the gate is no longer reported').toContain('mods.sound_gate_label');
    expect(panel, 'the gate is read but not writable here').toContain('setAlertSoundOn');
    expect(panel, 'the gate is still a signpost rather than a switch')
      .not.toContain('mods.sound_gate_where');
  });

  it('puts reset last in the header, as the size section does', () => {
    // One rule for both slider sections: the trailing control returns
    // the section to its default. A person who learns one header should
    // not have to relearn the next.
    const muteAt = panel.indexOf('mods.sound_mute');
    const resetAt = panel.indexOf('mods.sound_reset');
    expect(muteAt, 'the mute control is gone').toBeGreaterThan(0);
    expect(resetAt, 'the sound section has no reset').toBeGreaterThan(0);
    expect(resetAt, 'reset is not the trailing control').toBeGreaterThan(muteAt);
  });
});

/**
 * The Sounds percentage is the volume, and there must be exactly ONE of it.
 *
 * GX 2.0 gives each mods category an intensity dial. Ours already has
 * one for Sounds — `mods.sound.volume` is a 0..1 number and the panel
 * already renders it as a percentage — so the work is not to build a
 * dial but to keep it single.
 *
 * The failure this exists to prevent: a second "Sounds intensity"
 * alongside volume. Two numbers multiplying into one gain is how a
 * person ends up at 40% of 40%, hears almost nothing, and concludes the
 * feature is broken. The registry already records that failure once, for
 * the double-gated alert switch.
 *
 * Measured, not read: the stub records the peak the engine actually
 * writes, so a second multiplication anywhere in the chain shows up as a
 * number rather than as a diff someone has to notice.
 */
function stubAudioForVolume() {
  const peaks: number[] = [];
  let gainStages = 0;
  const node = () => ({
    connect: () => {}, disconnect: () => {},
    frequency: { setValueAtTime: () => {}, exponentialRampToValueAtTime: () => {} },
    gain: {
      setValueAtTime: (v: number) => { peaks.push(v); },
      exponentialRampToValueAtTime: () => {},
    },
    start: () => {}, stop: () => {}, type: 'sine', onended: null,
  });
  (window as unknown as { AudioContext: unknown }).AudioContext = class {
    currentTime = 0;
    destination = {};
    // `state` and `suspend` are real here, and mutable, because the
    // engine now reads and writes them. A stub without them does not
    // fail loudly: a missing `suspend` throws inside a listener and
    // vitest reports an Errors line while the summary still says every
    // test passed. The state a browser owns is modelled, not asserted
    // away.
    state = 'running';
    createOscillator() { return node(); }
    createGain() { gainStages += 1; return node(); }
    // The shared graph needs one. Without it `buildGraph` falls back and
    // every cue connects straight to the destination — which still
    // PLAYS, so nothing here would go red while the limiter silently
    // stopped existing. `two buses, one limiter` below is what catches
    // that; this keeps the volume test measuring the real path.
    createDynamicsCompressor() { return compressor(); }
    resume() { this.state = 'running'; return Promise.resolve(); }
    suspend() { this.state = 'suspended'; return Promise.resolve(); }
    close() { return Promise.resolve(); }
  };
  return { peaks, get gainStages() { return gainStages; } };
}

describe('one volume, one gain', () => {
  const CUE = { wave: 'sine', from: 880, to: 440, dur: 0.35, gain: 0.2 } as const;

  it('scales the cue exactly once by the volume, and only once', () => {
    // ONE test, three plays, one recorder — because the engine caches a
    // single AudioContext and never tears it down (browsers cap them at
    // about six, and the old chime built one per call). A second stub
    // would be installed and then ignored, and its empty array would
    // read as "no sound played" rather than "the stub was bypassed".
    const rec = stubAudioForVolume();
    armAudio();
    window.dispatchEvent(new Event('pointerdown'));

    playCue(CUE, 1);
    playCue(CUE, 0.5);
    playCue(CUE, 0.25);

    // Linear, checked at three points. A squared chain passes at 1 and
    // fails everywhere else, which is why one point is not enough.
    expect(rec.peaks[0]).toBeCloseTo(CUE.gain, 6);
    expect(rec.peaks[1]).toBeCloseTo(CUE.gain * 0.5, 6);
    expect(rec.peaks[2]).toBeCloseTo(CUE.gain * 0.25, 6);

    // The structural half: linearity could also survive two stages whose
    // product happens to be right today. One cue, one gain stage — plus
    // the two shared buses, built once with the context.
    //
    // Five does NOT prove the graph was built: the two bus gains are
    // created BEFORE the compressor, so a stub missing that node reaches
    // five and falls back to the destination. `two buses, one limiter`
    // reads the graph itself, which is the only thing that can tell the
    // two apart.
    expect(rec.gainStages).toBe(5);

    // And this test measured the REAL path. Without it the stub's
    // compressor is decoration: `buildGraph` would throw, the cue would
    // fall back to the destination, and all four assertions above would
    // still pass on a chain that is not the one that ships.
    expect(audioGraphForTests().limiter, 'the graph fell back — the chain '
      + 'measured above is not the one the product uses').toBeTruthy();
  });
});


/**
 * The audio thread stops while nobody is looking.
 *
 * The context is created once and never closed — correct, and it used to
 * mean the audio callback ran for the whole session. On a tablet in a
 * cab that session is a shift.
 *
 * The sharp edge is not the suspend, it is the cue that arrives after
 * one. A suspended context has a frozen clock: schedule against it and
 * the note is placed at a moment that never comes, `onended` never
 * fires, and the disconnect never runs — so every cue played while
 * suspended would leak a node pair onto the graph permanently, which is
 * worse than the drain it was meant to fix.
 */
describe('audio sleeps when the tab does', () => {
  interface StubCtx {
    state: string;
    suspends: number;
    resumes: number;
    started: number[];
  }
  let live: StubCtx | null = null;

  /** A context that models the two things the browser owns. */
  function stubLifecycle() {
    live = null;
    const node = () => ({
      connect: () => {}, disconnect: () => {},
      frequency: { setValueAtTime: () => {}, exponentialRampToValueAtTime: () => {} },
      gain: { setValueAtTime: () => {}, exponentialRampToValueAtTime: () => {} },
      start: (t: number) => { live!.started.push(t); }, stop: () => {},
      type: 'sine', onended: null,
    });
    (window as unknown as { AudioContext: unknown }).AudioContext = class {
      currentTime = 0;
      destination = {};
      state = 'running';
      suspends = 0;
      resumes = 0;
      started: number[] = [];
      constructor() { live = this as unknown as StubCtx; }
      createOscillator() { return node(); }
      createGain() { return node(); }
      resume() { this.resumes += 1; this.state = 'running'; return Promise.resolve(); }
      suspend() { this.suspends += 1; this.state = 'suspended'; return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
  }

  const CUE = { wave: 'sine', from: 880, to: 440, dur: 0.3, gain: 0.2 } as const;

  /** jsdom's `document.hidden` is a read-only accessor. */
  const setHidden = (hidden: boolean) => {
    Object.defineProperty(document, 'hidden', { value: hidden, configurable: true });
    document.dispatchEvent(new Event('visibilitychange'));
  };

  const unlockAndPlay = () => {
    armAudio();
    window.dispatchEvent(new Event('pointerdown'));
    playCue(CUE, 1);
  };

  beforeEach(() => { stubLifecycle(); setHidden(false); });

  it('the fixture is real — a cue plays and a context exists', () => {
    // Without this the three tests below could all pass on `live` being
    // null and nothing ever running.
    unlockAndPlay();
    expect(live, 'no context was constructed').not.toBeNull();
    expect(live!.started.length, 'the cue did not play').toBe(1);
    expect(live!.state).toBe('running');
  });

  it('suspends when the document hides', () => {
    unlockAndPlay();
    setHidden(true);
    expect(live!.suspends, 'the audio thread kept running on a hidden tab').toBe(1);
    expect(live!.state).toBe('suspended');
  });

  it('does not suspend twice, and does not suspend what is already asleep', () => {
    unlockAndPlay();
    setHidden(true);
    setHidden(false);
    setHidden(true);
    // The second hide finds it already suspended and leaves it alone;
    // only the first of each running→hidden transition calls through.
    expect(live!.suspends).toBe(1);
  });

  it('resumes for the next cue, and the note actually lands', async () => {
    unlockAndPlay();
    setHidden(true);
    expect(live!.state).toBe('suspended');

    // Counted RELATIVE to here: arming resumes the context too, so an
    // absolute number would be asserting about the gesture rather than
    // about the wake-up.
    const before = live!.resumes;

    playCue(CUE, 1);
    // Emitted from the resume promise, not in the same tick — that is
    // the whole point, so the assertion has to wait for it.
    expect(live!.started.length, 'played against a frozen clock').toBe(1);
    await Promise.resolve();
    await Promise.resolve();
    expect(live!.resumes - before, 'the context was never resumed').toBe(1);
    expect(live!.started.length, 'the cue after a suspend was lost').toBe(2);
  });

  it('installs ONE listener however many contexts come and go', () => {
    // Not detectable through behaviour: `suspendIfRunning` bails on a
    // context that is not running, so a second, third and fourth handler
    // are each a silent no-op and the suspend count stays 1. Stacked
    // listeners are invisible until something in the handler stops being
    // idempotent — so this asserts the design directly rather than an
    // effect of it.
    unlockAndPlay();                     // the hook is installed by now
    const spy = vi.spyOn(document, 'addEventListener');
    try {
      for (let i = 0; i < 2; i++) {
        resetAudioForTests();
        stubLifecycle();
        unlockAndPlay();
        expect(live, `no context was built on pass ${i} — nothing is proved`).not.toBeNull();
      }
      const added = spy.mock.calls.filter((c) => c[0] === 'visibilitychange');
      expect(added.length, 'a listener per context — they stack for the life of the page')
        .toBe(0);
    } finally {
      spy.mockRestore();
    }
  });

  it('a hidden tab with no context at all is not an error', () => {
    resetAudioForTests();
    expect(() => setHidden(true)).not.toThrow();
    expect(live, 'a context was built just to suspend it').toBeNull();
  });
});

/**
 * Two buses, one limiter.
 *
 * Every cue used to connect its own gain straight to the destination, so
 * two overlapping sounds SUMMED — and the one that lost was always the
 * one that mattered: a `critical` at 0.20 sits under an `error` toast at
 * 0.16 arriving 300ms later on a different clock, and nothing in the
 * product could tell them apart.
 *
 * A single master gain cannot fix that. Everything connects through it,
 * so pulling it down to protect the alert makes the alert quieter too.
 * What has to move is the side you did NOT ask for in that moment — your
 * own clicks, your keystrokes, the bed — which is why there are two.
 *
 * `buildGraph` is deliberately best-effort: a browser missing a node
 * must lose the limiter and keep the sound. The cost of that mercy is
 * that a broken stub looks exactly like a cautious browser, and the two
 * bus gains are created BEFORE the compressor, so even a gain-stage
 * count reaches the same number either way. These tests read the graph.
 */
describe('two buses, one limiter', () => {
  /** A fake context that records what connects to what. */
  function stubGraph({ compressor: hasCompressor = true } = {}) {
    const links: Array<{ from: string; to: string }> = [];
    const ducks: number[] = [];
    let seq = 0;
    const mk = (tag: string) => {
      const self: Record<string, unknown> = {
        tag: `${tag}${tag === 'gain' ? seq++ : ''}`,
        disconnect: () => {},
        frequency: { setValueAtTime: () => {}, exponentialRampToValueAtTime: () => {} },
        gain: {
          value: 0,
          setValueAtTime: () => {},
          exponentialRampToValueAtTime: () => {},
          setTargetAtTime: (v: number) => { ducks.push(v); },
        },
        threshold: { value: 0 }, knee: { value: 0 },
        ratio: { value: 0 }, attack: { value: 0 }, release: { value: 0 },
        start: () => {}, stop: () => {}, type: 'sine', onended: null,
      };
      self.connect = (to: { tag?: string }) =>
        links.push({ from: self.tag as string, to: to?.tag ?? 'destination' });
      return self;
    };
    (window as unknown as { AudioContext: unknown }).AudioContext = class {
      currentTime = 0;
      destination = { tag: 'destination' };
      state = 'running';
      createOscillator() { return mk('osc'); }
      createGain() { return mk('gain'); }
      createDynamicsCompressor() {
        if (!hasCompressor) throw new Error('no compressor here');
        return mk('limiter');
      }
      resume() { return Promise.resolve(); }
      suspend() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
    return { links, ducks };
  }

  const CUE = { wave: 'sine', from: 880, to: 440, dur: 0.05, gain: 0.2 } as const;
  const open = () => { armAudio(); window.dispatchEvent(new Event('pointerdown')); };

  it('is built with the context', () => {
    const rec = stubGraph();
    open();
    playCue(CUE, 1);
    const g = audioGraphForTests();
    expect(g.notify, 'no notify bus — every cue went straight to the destination').toBeTruthy();
    expect(g.action, 'no action bus — there is nothing to duck').toBeTruthy();
    expect(g.limiter, 'no limiter — a pile-up clips instead of compressing').toBeTruthy();
    // Both buses reach the limiter, and only the limiter reaches out.
    expect(rec.links).toContainEqual({ from: 'limiter', to: 'destination' });
    expect(rec.links.filter((l) => l.to === 'destination'))
      .toHaveLength(1);
  });

  it('sends a cue to the notify side unless it is told otherwise', () => {
    const rec = stubGraph();
    open();
    playCue(CUE, 1);
    const g = audioGraphForTests() as unknown as { notify: { tag: string } };
    expect(rec.links.some((l) => l.from.startsWith('gain') && l.to === g.notify.tag),
      'the default changed — six existing call sites all mean "the app answered"')
      .toBe(true);
  });

  it('and to the action side when it is', () => {
    const rec = stubGraph();
    open();
    playCue(CUE, 1, CUE_LIMITS, 'action');
    const g = audioGraphForTests() as unknown as { action: { tag: string } };
    expect(rec.links.some((l) => l.from.startsWith('gain') && l.to === g.action.tag)).toBe(true);
  });

  /**
   * The threshold is PLACED, not copied. It sits above the loudest
   * single cue this app can make — chime's `critical` at 0.20, which is
   * -13.98 dBFS — so a lone alert passes untouched and `chime.ts`'s
   * promise that it is "reproduced exactly" survives. Below that number
   * the limiter would be working on every alert, quietly.
   */
  it('leaves the loudest single cue alone', () => {
    stubGraph();
    open();
    playCue(CUE, 1);
    const lim = audioGraphForTests().limiter as unknown as { threshold: { value: number } };
    const loudest = Math.max(...SOUND_PACKS.flatMap(
      (p) => CUE_NAMES.map((n) => p.cues[n].gain)));
    expect(20 * Math.log10(loudest), 'a pack got louder than the threshold allows')
      .toBeLessThan(lim.threshold.value);
  });

  it('ducks the action side, and puts it back on a clock', () => {
    vi.useFakeTimers();
    try {
      const rec = stubGraph();
      open();
      playCue(CUE, 1);
      duckActions(400);
      expect(rec.ducks[0], 'the action side did not move').toBeLessThan(1);
      // UNCONDITIONAL: a restore that waits for the sound to end is a
      // restore that never happens the one time it does not, and the
      // failure is the whole product going quiet with nothing to blame.
      vi.advanceTimersByTime(400);
      expect(rec.ducks[rec.ducks.length - 1]).toBe(1);
    } finally {
      vi.useRealTimers();
    }
  });

  /**
   * The mercy, asserted. A browser without DynamicsCompressor must lose
   * the limiter and keep the SOUND — audio here is best-effort and never
   * breaks a page. Without this test the fallback is a branch nobody has
   * ever run.
   */
  it('still plays when the graph cannot be built', () => {
    const rec = stubGraph({ compressor: false });
    open();
    playCue(CUE, 1);
    const g = audioGraphForTests();
    expect(g.limiter, 'the compressor threw and was kept anyway').toBeNull();
    expect(g.notify, 'a half-built graph is worse than none').toBeNull();
    expect(rec.links.some((l) => l.from.startsWith('gain') && l.to === 'destination'),
      'the cue reached nothing — a missing limiter silenced the app')
      .toBe(true);
  });
});
