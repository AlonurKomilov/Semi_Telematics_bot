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

import { playUiCue, armIfWanted } from './cue';
import { preferences } from '../../preferences';
import { SOUND_PACKS } from './engine';

const set = (ui: boolean, volume = 1, pack = 'chime', alert = false) => {
  preferences.set('mods.sound.ui', ui);
  preferences.set('mods.sound.volume', volume);
  preferences.set('mods.sound.pack', pack);
  preferences.set('dispatch.soundOn', alert);
};

beforeEach(() => {
  playCue.mockClear(); armAudio.mockClear();
  localStorage.clear();
  set(false);
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
});
