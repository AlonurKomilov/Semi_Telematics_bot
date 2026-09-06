/**
 * The toast lane's sound, and the pass-through under it.
 *
 * `bannerCue.test.ts` guards the other lane. This one guards the thing
 * that lane does not have to worry about: 318 call sites that were
 * written before any of them could make a noise, and must keep working
 * exactly as they did while gaining one.
 *
 * So every test here asserts BOTH halves — what was heard AND what
 * reached sonner. A wrapper that sounds correctly and drops the toast's
 * options on the floor would pass half of them.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { playCue } = vi.hoisted(() => ({ playCue: vi.fn() }));
vi.mock('../mods/sound/engine', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  playCue,
}));

const { sonner } = vi.hoisted(() => ({
  sonner: Object.assign(vi.fn(() => 'plain'), {
    success: vi.fn(() => 's1'), error: vi.fn(() => 'e1'), warning: vi.fn(() => 'w1'),
    info: vi.fn(), loading: vi.fn(), message: vi.fn(),
    custom: vi.fn(), dismiss: vi.fn(), promise: vi.fn(),
  }),
}));
vi.mock('sonner', () => ({ toast: sonner, Toaster: () => null }));

import { toast } from './toast';
import { resetToastCueForTests } from '../mods/sound/cue';
import { SOUND_PACKS } from '../mods/sound/engine';
import { preferences } from '../preferences';

const pack = () => SOUND_PACKS.find((p) => p.id === 'chime')!;
const heard = () => (playCue.mock.calls as unknown[][]).map((c) => c[0]);

beforeEach(() => {
  playCue.mockClear();
  for (const k of Object.keys(sonner)) {
    const fn = (sonner as unknown as Record<string, { mockClear?: () => void }>)[k];
    fn.mockClear?.();
  }
  sonner.mockClear();
  localStorage.clear();
  resetToastCueForTests();
  preferences.set('mods.sound.ui', true);
  preferences.set('mods.sound.volume', 1);
  preferences.set('mods.sound.pack', 'chime');
});

describe('the tone picks the cue', () => {
  it('a success sounds success', () => {
    toast.success('Saved');
    expect(playCue).toHaveBeenCalledWith(pack().cues.success, 1);
    expect(sonner.success).toHaveBeenCalledWith('Saved', undefined);
  });

  it('an error sounds error', () => {
    toast.error('Refused');
    expect(playCue).toHaveBeenCalledWith(pack().cues.error, 1);
    expect(sonner.error).toHaveBeenCalledWith('Refused', undefined);
  });

  /** Every warning toast in this app reports a part that did not go
   *  through — rows skipped, a scan that failed to attach, a reminder
   *  throttled. That is what the `error` cue means. */
  it('a warning sounds error, because a warning here is a refusal in part', () => {
    toast.warning('3 skipped');
    expect(playCue).toHaveBeenCalledWith(pack().cues.error, 1);
    expect(sonner.warning).toHaveBeenCalledWith('3 skipped', undefined);
  });
});

describe('what stays silent', () => {
  it('info, loading, message and a plain toast', () => {
    toast.info('FYI'); toast.loading('Working…');
    toast.message('Hi'); toast('Plain');
    expect(playCue, 'a lane that chimes for everything is a lane nobody hears')
      .not.toHaveBeenCalled();
    expect(sonner.info, 'the toast stopped being shown').toHaveBeenCalled();
    expect(sonner.loading).toHaveBeenCalled();
    expect(sonner).toHaveBeenCalledWith('Plain', undefined);
  });

  /** The banner lane is BUILT on `toast.custom` and sounds itself
   *  through `playBannerCue`, under a different gate. Sounding it here
   *  would announce every alert twice — the exact double a listener at
   *  the `<Toaster>` would have shipped. */
  it('and custom, which is the banner lane speaking with its own voice', () => {
    toast.custom(() => ({}) as never);
    expect(playCue).not.toHaveBeenCalled();
    expect(sonner.custom).toHaveBeenCalled();
  });
});

describe('a caller that knows better', () => {
  it('names its own cue', () => {
    toast.success('3 deleted', { cue: 'undo' });
    expect(playCue).toHaveBeenCalledWith(pack().cues.undo, 1);
    expect(playCue).not.toHaveBeenCalledWith(pack().cues.success, 1);
  });

  it('or asks for silence', () => {
    toast.error('already announced', { cue: false });
    expect(playCue).not.toHaveBeenCalled();
    expect(sonner.error, 'silencing the cue silenced the toast').toHaveBeenCalled();
  });

  /** Silence means the player is never asked — not asked for nothing.
   *  `playToastCue` bumps the 400ms floor on every call, so a silenced
   *  toast that still reached it would swallow the NEXT real one, which
   *  is a bug you would hear once and never reproduce. */
  it('and a silenced toast does not spend the floor the next one needs', () => {
    toast.error('already announced', { cue: false });
    toast.success('this one must be heard');
    expect(playCue).toHaveBeenCalledWith(pack().cues.success, 1);
  });

  it('and `cue` never reaches sonner', () => {
    toast.success('x', { cue: 'undo', duration: 4000, id: 'abc' });
    expect(sonner.success).toHaveBeenCalledWith('x', { duration: 4000, id: 'abc' });
  });
});

describe('the 318 call sites keep working', () => {
  it('options pass through untouched when there is no cue', () => {
    const action = { label: 'Undo', onClick: () => {} };
    toast.success('m', { duration: 9, id: 'k', action });
    expect(sonner.success).toHaveBeenCalledWith('m', { duration: 9, id: 'k', action });
  });

  it('the toast id comes back to the caller', () => {
    expect(toast.success('m')).toBe('s1');
    expect(toast.error('m')).toBe('e1');
    expect(toast('m')).toBe('plain');
  });

  it('and everything the wrapper does not touch is sonner itself', () => {
    expect(toast.dismiss).toBe(sonner.dismiss);
    expect(toast.promise).toBe(sonner.promise);
    expect(toast.loading).toBe(sonner.loading);
    expect(toast.custom).toBe(sonner.custom);
  });
});

describe('the gate and the floor', () => {
  it('says nothing at all when interface sound is off', () => {
    preferences.set('mods.sound.ui', false);
    toast.success('Saved');
    expect(playCue).not.toHaveBeenCalled();
    expect(sonner.success, 'the gate silenced the toast, not just the cue')
      .toHaveBeenCalled();
  });

  /** One bulk action raises a toast per failed row, and every cue
   *  connects its own gain straight to the destination — ten would SUM
   *  into one loud noise instead of ten sounds. */
  it('a burst of toasts announces itself once', () => {
    for (let i = 0; i < 10; i++) toast.error(`row ${i} failed`);
    expect(heard()).toHaveLength(1);
    expect(sonner.error, 'the floor swallowed the toasts too')
      .toHaveBeenCalledTimes(10);
  });

  it('and two genuinely separate actions are two sounds', () => {
    toast.success('first');
    resetToastCueForTests();          // stands for 400ms passing
    toast.success('second');
    expect(heard()).toHaveLength(2);
  });
});
