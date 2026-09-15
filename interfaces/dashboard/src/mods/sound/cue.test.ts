/**
 * The gate, and the arming that decides whether anyone can hear it.
 *
 * Two failures this exists for, both of which the product had.
 *
 * The engine declared five cues and played one, because the only player
 * was a hook and the places that wanted a cue are plain functions. That
 * is fixed by `playUiCue` existing — so what needs guarding is the thing
 * a non-hook player makes easy to get wrong: sound nobody asked for.
 * `mods.sound.volume` defaults to 1, so the gate is the entire distance
 * between a fresh account and noise on a shared office floor.
 *
 * And audio was never ARMED for six of the nine roles, which is why
 * their volume dial was decoration. Arming is not free either — it
 * builds an AudioContext that is never torn down — so it has to happen
 * for whoever asked and nobody else.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { playCue, armAudio } = vi.hoisted(() => ({
  playCue: vi.fn(), armAudio: vi.fn(),
}));
vi.mock('./engine', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  playCue, armAudio,
}));

import { playUiCue, armIfWanted, playKeyCue, playActCue } from './cue';
import { resetActSoundForTests, heardSoFar } from './acts';
import { resetKeySoundForTests, KEY_LIMITS } from './keys';
import { keyPackById } from '../store/items/keys';
import { preferences } from '../../preferences';
import { SOUND_PACKS } from '../store/items/sound';

const set = (ui: boolean, volume = 1, pack = 'chime', alert = false) => {
  preferences.set('mods.sound.background', false);
  preferences.set('mods.sound.ui', ui);
  preferences.set('mods.sound.volume', volume);
  preferences.set('mods.sound.pack', pack);
  preferences.set('dispatch.soundOn', alert);
};

const keys = (on: boolean, pack = 'click') => {
  preferences.set('mods.sound.keyboard', on);
  preferences.set('mods.sound.keyboard.pack', pack);
  resetKeySoundForTests();
};

const typed = (key: string) => {
  document.body.innerHTML = '<input type="text" />';
  const e = new KeyboardEvent('keydown', { key, bubbles: true });
  Object.defineProperty(e, 'target', { value: document.body.firstElementChild });
  return e;
};

beforeEach(() => {
  playCue.mockClear(); armAudio.mockClear();
  localStorage.clear();
  set(false);
  keys(false);
});

describe('the gate', () => {
  it('is closed by default — a fresh screen makes no interface sound', () => {
    // Read from the registry, not from what this test just wrote: the
    // default is the claim.
    localStorage.clear();
    expect(preferences.get('mods.sound.ui'), 'interface sound defaults ON').toBe(false);
    // …and the level deliberately does NOT default to silence, which is
    // why the gate has to carry the whole weight.
    expect(preferences.get('mods.sound.volume')).toBeGreaterThan(0);
  });

  it('silences every cue while it is off', () => {
    set(false, 1);
    for (const name of ['undo', 'success', 'error', 'critical', 'alert'] as const) playUiCue(name);
    expect(playCue, 'a cue played with the gate off').not.toHaveBeenCalled();
  });

  it('lets a cue through when it is on', () => {
    set(true, 1);
    playUiCue('undo');
    expect(playCue).toHaveBeenCalledTimes(1);
    const pack = SOUND_PACKS.find((p) => p.id === 'chime')!;
    expect(playCue).toHaveBeenCalledWith(pack.cues.undo, 1);
  });

  it('plays the cue the caller named, from the pack this person chose', () => {
    set(true, 0.5, 'blip');
    playUiCue('error');
    const blip = SOUND_PACKS.find((p) => p.id === 'blip')!;
    expect(playCue).toHaveBeenCalledWith(blip.cues.error, 0.5);
    // The two packs must differ here or this test is watching nothing.
    const chime = SOUND_PACKS.find((p) => p.id === 'chime')!;
    expect(blip.cues.error).not.toEqual(chime.cues.error);
  });

  it('still respects a silenced screen', () => {
    set(true, 0);
    playUiCue('undo');
    expect(playCue, 'volume 0 is a real setting, not a disabled state').not.toHaveBeenCalled();
  });

  it('does not throw on a pack that no longer exists', () => {
    set(true, 1, 'a-pack-that-was-deleted');
    expect(() => playUiCue('undo')).not.toThrow();
    expect(playCue).not.toHaveBeenCalled();
  });
});

describe('arming happens for whoever asked, and nobody else', () => {
  it('does not arm a screen with both gates off', () => {
    set(false, 1, 'chime', false);
    armIfWanted();
    expect(armAudio, 'built an AudioContext for a screen that wants no sound')
      .not.toHaveBeenCalled();
  });

  it('arms for interface sound', () => {
    set(true, 1, 'chime', false);
    armIfWanted();
    expect(armAudio).toHaveBeenCalled();
  });

  it('arms for alert sound too — the roles that already had it must not lose it', () => {
    set(false, 1, 'chime', true);
    armIfWanted();
    expect(armAudio).toHaveBeenCalled();
  });

  it('arms for background sound, which is the gate that needs it most', () => {
    // A cue is played FROM a click, so by the time it runs the gesture
    // has happened anyway. The bed asks at MOUNT — on a reload, before
    // anything has been clicked — so a screen whose only sound is the
    // bed is the one screen that cannot afford to be unarmed. It was
    // missing from this condition, and the bed made no sound at all.
    set(false, 1, 'chime', false);
    preferences.set('mods.sound.background', true);
    armIfWanted();
    expect(armAudio, 'the one gate that asks for audio before any click was left unarmed')
      .toHaveBeenCalled();
  });
});

describe('the keyboard is its own switch', () => {
  it('is closed by default', () => {
    localStorage.clear();
    expect(preferences.get('mods.sound.keyboard'), 'typing sound defaults ON').toBe(false);
  });

  it('stays silent while it is off, even with interface sound on', () => {
    set(true, 1);
    keys(false);
    playKeyCue(typed('a'));
    expect(playCue, 'the interface switch turned typing on too').not.toHaveBeenCalled();
  });

  it('plays while it is on, even with interface sound off', () => {
    set(false, 1);
    keys(true);
    playKeyCue(typed('a'));
    // Two questions, two switches — the whole reason for a second key.
    expect(playCue).toHaveBeenCalledWith(keyPackById('click')!.cues.letter, 1, KEY_LIMITS);
  });

  it('shares the one volume, and a silenced screen silences it', () => {
    set(false, 0);
    keys(true);
    playKeyCue(typed('a'));
    expect(playCue, 'volume 0 did not reach the keyboard').not.toHaveBeenCalled();
  });

  it('plays the pack this person chose', () => {
    set(false, 0.6);
    keys(true, 'soft');
    playKeyCue(typed(' '));
    expect(playCue).toHaveBeenCalledWith(keyPackById('soft')!.cues.space, 0.6, KEY_LIMITS);
  });

  it('arms audio for a screen that only wants typing', () => {
    set(false, 1, 'chime', false);
    keys(true);
    armIfWanted();
    expect(armAudio, 'the keyboard gate does not arm audio — it would be silent').toHaveBeenCalled();
  });
});

/**
 * The act axis has five gates and a tally, and they are the same
 * mechanism: nothing is counted that was not heard.
 *
 * Every per-shift figure behind this axis is a derivation — there is no
 * click telemetry in this product and none is being added for a
 * convenience feature — so the count on the panel is the only
 * measurement it has. A count that included refused acts would be a
 * measurement of the wrong thing, and worse than none, because somebody
 * would design against it.
 */
describe('an act is heard, or it is not counted', () => {
  const acts = (on: boolean, extra: Record<string, unknown> = {}) => {
    preferences.set('mods.sound.acts', on);
    preferences.set('mods.sound.acts.controls', true);
    preferences.set('mods.sound.acts.places', true);
    preferences.set('mods.sound.acts.selection', true);
    preferences.set('mods.sound.acts.pack', 'chime');
    preferences.set('mods.sound.snoozeUntil', 0);
    for (const [k, v] of Object.entries(extra)) preferences.set(k as never, v as never);
    resetActSoundForTests();
  };

  it('plays and counts when every gate is open', () => {
    acts(true);
    playActCue('press');
    expect(playCue).toHaveBeenCalledTimes(1);
    expect(heardSoFar().total).toBe(1);
    expect(heardSoFar().byAct.get('press')).toBe(1);
  });

  /** Its OWN master, not a wider reading of `mods.sound.ui`. That gate
   *  is about thirty cues a shift; this axis is two orders of magnitude
   *  more, and folding them would have taken every device that opted in
   *  from thirty to roughly nineteen hundred with no new consent. */
  it('and the interface gate does not open it', () => {
    acts(false);
    set(true);
    playActCue('press');
    expect(playCue, 'the act axis rode in on the answer gate').not.toHaveBeenCalled();
    expect(heardSoFar().total).toBe(0);
  });

  it('a closed family is silent, and uncounted', () => {
    acts(true, { 'mods.sound.acts.places': false });
    playActCue('page_open');
    expect(playCue).not.toHaveBeenCalled();
    expect(heardSoFar().total).toBe(0);
    // …and its neighbours still speak.
    playActCue('press');
    expect(playCue).toHaveBeenCalledTimes(1);
  });

  it('a snooze is silent, and uncounted', () => {
    acts(true, { 'mods.sound.snoozeUntil': Date.now() + 60_000 });
    playActCue('press');
    expect(playCue).not.toHaveBeenCalled();
    expect(heardSoFar().total).toBe(0);
  });

  it('and so is zero volume', () => {
    acts(true);
    preferences.set('mods.sound.volume', 0);
    playActCue('press');
    expect(playCue).not.toHaveBeenCalled();
    expect(heardSoFar().total).toBe(0);
  });

  /**
   * The floor is the one a naive tally gets wrong: the act HAPPENED,
   * the person did it, and nothing reached the room. Counting it would
   * report a volume nobody experienced.
   */
  it('and an act the floor dropped is not counted either', () => {
    acts(true);
    playActCue('press');
    playActCue('press');
    expect(playCue, 'two cues inside the floor overlapped').toHaveBeenCalledTimes(1);
    expect(heardSoFar().total, 'the tally counted an act nobody heard').toBe(1);
  });
});
