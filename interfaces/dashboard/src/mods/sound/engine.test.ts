/**
 * The bounds are the feature.
 *
 * A pack made of numbers is safe only while the numbers are checked —
 * and the reason to check them now, while every pack is ours, is that
 * the point of the arc is that they will not always be. A person
 * authoring a cue can reach for 20 kHz at full gain by accident far
 * more easily than they can write a malformed colour.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  SOUND_PACKS, CUE_NAMES, CUE_LIMITS, WAVES,
  isSafeCue, playCue, soundPackById, armAudio, resetAudioForTests, type Cue,
} from './engine';

beforeEach(() => { resetAudioForTests(); });

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
    const node = () => ({
      connect: () => {}, disconnect: () => {},
      frequency: { setValueAtTime: () => {}, exponentialRampToValueAtTime: () => {} },
      gain: { setValueAtTime: () => {}, exponentialRampToValueAtTime: () => {} },
      start: (t: number) => { started.push(t); }, stop: () => {},
      type: 'sine', onended: null,
    });
    (window as unknown as { AudioContext: unknown }).AudioContext = class {
      currentTime = 0;
      destination = {};
      createOscillator() { return node(); }
      createGain() { return node(); }
      resume() { return Promise.resolve(); }
      close() { return Promise.resolve(); }
    };
    return started;
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
    join(__dirname, '..', 'ModPanel.tsx'), 'utf8');

  it('offers every pack', () => {
    // Generated from the catalogue, so adding a pack cannot half-land as
    // a set of cues nobody can select.
    expect(panel).toContain('SOUND_PACKS.map');
  });

  it('previews the pack that was clicked, not the one that was stored', () => {
    // `setValue` is async. Previewing through the stored id plays the
    // pack you just LEFT, which is the kind of bug that reads as the
    // preview being broken rather than as one frame of staleness.
    const onClick = /SOUND_PACKS\.map\([\s\S]{0,600}?preview\((\w+)/.exec(panel);
    expect(onClick, 'the pack chips no longer preview').not.toBeNull();
    expect(onClick![1], 'the preview reads a stored id instead of the clicked pack')
      .toBe('p');
  });

  it('restores the level it silenced, not a default', () => {
    // A mute that comes back at 100% is a mute people stop using.
    expect(panel).toContain('beforeMute');
    expect(panel).toMatch(/beforeMute\.current = volume/);
  });

  it('reports the gate\'s STATE and where it lives, not just that one exists', () => {
    // The audit's sharpest finding. A section reading 100% while the
    // product is silent is a section that reads as broken — and naming
    // that "a switch exists somewhere" leaves the person hunting.
    expect(panel, 'the gate is no longer reported').toContain('theme.sound_gate_label');
    expect(panel, 'the gate reports existence, not state').toContain('alertSoundOn');
    expect(panel, 'the gate does not say where it lives').toContain('theme.sound_gate_where');
  });

  it('puts reset last in the header, as the size section does', () => {
    // One rule for both slider sections: the trailing control returns
    // the section to its default. A person who learns one header should
    // not have to relearn the next.
    const muteAt = panel.indexOf('theme.sound_mute');
    const resetAt = panel.indexOf('theme.sound_reset');
    expect(muteAt, 'the mute control is gone').toBeGreaterThan(0);
    expect(resetAt, 'the sound section has no reset').toBeGreaterThan(0);
    expect(resetAt, 'reset is not the trailing control').toBeGreaterThan(muteAt);
  });
});
