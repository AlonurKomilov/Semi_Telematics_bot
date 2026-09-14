/**
 * The act axis, as a mechanism.
 *
 * Every cue in `engine.ts` means the app ANSWERED you. This axis is the
 * other half — what happens when you touch something — and the two
 * differ by two orders of magnitude in how often they fire. That single
 * fact is what every rule below is about: a vocabulary this frequent is
 * not made bearable by taste, it is made bearable by arithmetic.
 *
 * `acts.ts` is pure of preferences and of the pack catalogue, so every
 * branch here runs with no DOM, no AudioContext and no stub.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import {
  ACT_NAMES, ACT_FAMILY, ACT_LIMITS, pickActCue, streakScale,
  resetActSoundForTests, type ActPack, type ActName,
} from './acts';
import { KEY_LIMITS } from './keys';
import { CUE_LIMITS, isCueWithin } from './engine';
import { ACT_PACKS } from '../store/items/acts';

const QUIET = { lastNotifyAt: -Infinity, lastKeyAt: -Infinity };
const pack = ACT_PACKS[0];

beforeEach(() => { resetActSoundForTests(); });

describe('the band is the ladder, made unbreakable', () => {
  /**
   * The interface answering a touch must not be louder than the
   * keystroke that caused it, and neither may approach a notification.
   * Written as a type-checked bound rather than as restraint, because
   * restraint is what erodes.
   */
  it('acts are quieter than keys, which are quieter than answers', () => {
    expect(ACT_LIMITS.gain.max).toBeLessThan(KEY_LIMITS.gain.max);
    expect(KEY_LIMITS.gain.max).toBeLessThan(CUE_LIMITS.gain.max);
  });

  it('and shorter than a cue is allowed to be', () => {
    // A press is 14ms, which is ILLEGAL in CUE_LIMITS — its floor is
    // 20ms because it was tuned for something you are meant to notice.
    // That is the whole reason this is a third axis and not five more
    // names on the first one.
    expect(ACT_LIMITS.dur.min).toBeLessThan(CUE_LIMITS.dur.min);
  });

  it('every shipped pack answers every act, inside the band', () => {
    expect(ACT_PACKS.length, 'no act packs — this checks nothing').toBeGreaterThan(2);
    for (const p of ACT_PACKS)
      for (const name of ACT_NAMES)
        expect(isCueWithin(p.cues[name], ACT_LIMITS), `${p.id}.${name}`).toBe(true);
  });

  /**
   * ON and OFF are the same numbers reversed, in every pack.
   *
   * A person learns one shape and reads its reverse for free — and no
   * pack author can ship a pair that disagrees about which way is up.
   */
  it('and every toggle pair is an exact mirror', () => {
    for (const p of ACT_PACKS) {
      const on = p.cues.toggle_on;
      const off = p.cues.toggle_off;
      expect({ ...off, from: off.to, to: off.from }, `${p.id}`).toEqual(on);
    }
  });

  it('every act belongs to a family somebody can switch off', () => {
    for (const name of ACT_NAMES)
      expect(ACT_FAMILY[name], `${name} has no family`).toBeTruthy();
  });
});

describe('one act, one voice', () => {
  it('drops a second act inside the floor', () => {
    expect(pickActCue('press', pack, 1000, QUIET)).toBeTruthy();
    expect(pickActCue('press', pack, 1050, QUIET), 'two cues overlapped').toBeNull();
    expect(pickActCue('press', pack, 1200, QUIET)).toBeTruthy();
  });

  /**
   * The floor has to outlast the sound, not merely the gap between two
   * clicks: `engine.ts` stops an oscillator at `dur + 0.02`, so a 70ms
   * cue occupies 90ms. There is no master gain — two overlapping cues
   * SUM — so this is the arithmetic that keeps a burst from becoming one
   * loud noise.
   */
  it('and the floor outlasts the longest cue this band allows', () => {
    const occupancy = (ACT_LIMITS.dur.max + 0.02) * 1000;
    expect(pickActCue('press', pack, 0, QUIET)).toBeTruthy();
    expect(pickActCue('press', pack, occupancy - 1, QUIET),
      'a cue could start while the last one was still sounding').toBeNull();
  });

  it('DROPS rather than queues — a late cue is worse than none', () => {
    pickActCue('press', pack, 0, QUIET);
    pickActCue('press', pack, 10, QUIET);
    pickActCue('press', pack, 20, QUIET);
    // The third press at 200 is heard because the floor cleared, not
    // because two were waiting in line.
    expect(pickActCue('press', pack, 200, QUIET)).toBeTruthy();
  });
});

describe('it gets out of the way', () => {
  it('says nothing while the app is still speaking', () => {
    expect(pickActCue('press', pack, 1000, { lastNotifyAt: 900, lastKeyAt: -Infinity }),
      'a press landed on top of an alert').toBeNull();
    expect(pickActCue('press', pack, 1000, { lastNotifyAt: 600, lastKeyAt: -Infinity }))
      .toBeTruthy();
  });

  it('and while somebody is typing — the keyboard owns that stretch', () => {
    expect(pickActCue('press', pack, 1000, { lastNotifyAt: -Infinity, lastKeyAt: 900 }))
      .toBeNull();
  });

  /**
   * Deference must not SPEND the floor. An act refused because the app
   * was speaking would otherwise silence the next one too — a second
   * penalty for something the person did not do.
   */
  it('and a deferred act does not cost the next one its turn', () => {
    expect(pickActCue('press', pack, 1000, { lastNotifyAt: 900, lastKeyAt: -Infinity }))
      .toBeNull();
    expect(pickActCue('press', pack, 1010, QUIET), 'the refusal spent the floor')
      .toBeTruthy();
  });
});

describe('a streak decays', () => {
  it('scales by count, and never to silence', () => {
    expect(streakScale(0)).toBe(1);
    expect(streakScale(2)).toBe(1);
    expect(streakScale(3)).toBeLessThan(1);
    expect(streakScale(6)).toBeLessThan(streakScale(3));
    expect(streakScale(50), 'a streak went silent — ten repeats are nine murmurs, '
      + 'not nine nothings').toBeGreaterThan(0);
  });

  it('ten repeats become one announcement and nine murmurs', () => {
    let last = 1;
    for (let i = 0; i < 10; i++) {
      const got = pickActCue('press', pack, i * 100, QUIET);
      expect(got).toBeTruthy();
      last = got!.scale;
    }
    expect(last).toBeLessThan(1);
  });

  it('and a different act starts its own', () => {
    for (let i = 0; i < 8; i++) pickActCue('press', pack, i * 100, QUIET);
    expect(pickActCue('chip', pack, 900, QUIET)!.scale,
      'a chip inherited a press streak').toBe(1);
  });

  it('and quiet ends one', () => {
    for (let i = 0; i < 8; i++) pickActCue('press', pack, i * 100, QUIET);
    expect(pickActCue('press', pack, 5_000, QUIET)!.scale).toBe(1);
  });
});

describe('an unanswered act', () => {
  it('is silent, but still counts against the floor', () => {
    // A pack that cannot answer is not a reason to let the next cue
    // arrive early: the floor is a property of the hand, not the pack.
    const empty = { id: 'x', label: 'x', description: 'x', cues: {} } as unknown as ActPack;
    expect(pickActCue('press', empty, 1000, QUIET)).toBeNull();
    expect(pickActCue('press', pack, 1040, QUIET), 'the floor was not spent').toBeNull();
  });

  it('and an unknown pack is silence, not a throw', () => {
    expect(pickActCue('press' as ActName, undefined, 1000, QUIET)).toBeNull();
  });
});
