/**
 * The notification lane's sound.
 *
 * A banner IS the notification, so this is the one place a notification
 * speaks — whatever raised it, by priority. Before, sound was attached
 * to one PAGE's rising queue counter: a dispatcher standing on the
 * alerts board heard an arrival twice (the counter and the banner would
 * both have spoken once the lane gained a voice), and the other six
 * roles never heard anything at all because that counter renders for
 * three personas.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { playCue } = vi.hoisted(() => ({ playCue: vi.fn() }));
vi.mock('./engine', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  playCue,
}));

import { playBannerCue, resetBannerCueForTests } from './cue';
import { SOUND_PACKS } from './engine';
import { preferences, DEFS } from '../../preferences';

const pack = () => SOUND_PACKS.find((p) => p.id === 'chime')!;
const on = (alerts = true, volume = 1) => {
  preferences.set('dispatch.soundOn', alerts);
  preferences.set('mods.sound.volume', volume);
  preferences.set('mods.sound.pack', 'chime');
};

beforeEach(() => {
  playCue.mockClear();
  localStorage.clear();
  resetBannerCueForTests();
  on();
});

describe('priority decides the sound', () => {
  it('a danger banner sounds critical', () => {
    playBannerCue('danger');
    expect(playCue).toHaveBeenCalledWith(pack().cues.critical, 1);
  });

  it('a warning sounds the ordinary alert', () => {
    playBannerCue('warn');
    expect(playCue).toHaveBeenCalledWith(pack().cues.alert, 1);
  });

  it('a confirmation sounds success', () => {
    playBannerCue('ok');
    expect(playCue).toHaveBeenCalledWith(pack().cues.success, 1);
  });

  it('info and neutral say nothing', () => {
    // A lane that chimes for everything trains people to stop hearing
    // it, and nothing arrives as `info` that anyone must look up for.
    for (const tone of ['info', 'neutral'] as const) {
      resetBannerCueForTests();
      playBannerCue(tone);
    }
    expect(playCue, 'the quiet tones spoke').not.toHaveBeenCalled();
  });

  it('and the three loud tones are three DIFFERENT sounds', () => {
    // Mapping two severities onto one cue would make the priority
    // invisible, which is the whole point of mapping at all.
    const p = pack();
    const heard = new Set([p.cues.critical, p.cues.alert, p.cues.success]);
    expect(heard.size).toBe(3);
  });

  it('a caller may name its own cue instead of a tone', () => {
    playBannerCue('undo');
    expect(playCue).toHaveBeenCalledWith(pack().cues.undo, 1);
  });
});

describe('the gate', () => {
  it('is closed by default — a fresh screen announces nothing out loud', () => {
    // Read from the REGISTRY, not from the store: clearing localStorage
    // leaves the store's in-memory value alone, so a store read here
    // would just report what `on()` wrote a moment ago.
    expect(DEFS['dispatch.soundOn'].default).toBe(false);
  });

  it('silences every tone while it is off', () => {
    on(false);
    for (const tone of ['danger', 'warn', 'ok'] as const) {
      resetBannerCueForTests();
      playBannerCue(tone);
    }
    expect(playCue, 'a banner spoke with alert sound off').not.toHaveBeenCalled();
  });

  it('respects a silenced screen', () => {
    on(true, 0);
    playBannerCue('danger');
    expect(playCue).not.toHaveBeenCalled();
  });
});

describe('one poll announces itself once', () => {
  it('drops a second cue inside the floor rather than stacking it', () => {
    // LiveAlertWatcher raises up to three banners per tick, and every
    // cue connects its own gain straight to the destination — three
    // would sum into one loud noise rather than three sounds.
    playBannerCue('danger');
    playBannerCue('warn');
    playBannerCue('ok');
    expect(playCue, 'a burst of banners made a burst of noise').toHaveBeenCalledTimes(1);
  });

  it('and the one that lands is the first, not the quietest', () => {
    playBannerCue('danger');
    playBannerCue('info');
    expect(playCue).toHaveBeenCalledWith(pack().cues.critical, 1);
  });

  it('a later, separate arrival is not merged', () => {
    playBannerCue('warn');
    resetBannerCueForTests();          // stands for the next poll tick
    playBannerCue('warn');
    expect(playCue).toHaveBeenCalledTimes(2);
  });
});
