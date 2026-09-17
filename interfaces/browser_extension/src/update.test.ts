/**
 * The update notice's logic, and mostly the one bug it is built to
 * avoid: a version comparison done on strings.
 */
import { describe, it, expect } from 'vitest';

import {
  compareVersions, isNewer, channelOf, noticeFor, isCheckDue, CHECK_EVERY_MS,
} from './update';

describe('compareVersions', () => {
  it('compares the third digit as a NUMBER, not as text', () => {
    // The whole reason this function exists.  As strings, '19' < '9'
    // because '1' < '9' — so a string comparison would have gone quiet
    // at exactly the release where this project's third digit passed
    // nine, months after anyone would think to look.
    expect('0.5.19.0' > '0.5.9.0').toBe(false);          // the trap itself
    expect(compareVersions('0.5.19.0', '0.5.9.0')).toBe(1);
    expect(isNewer('0.5.19.0', '0.5.9.0')).toBe(true);
  });

  it('treats a missing trailing segment as zero', () => {
    expect(compareVersions('0.5.19', '0.5.19.0')).toBe(0);
    expect(isNewer('0.5.19', '0.5.19.0')).toBe(false);
  });

  it('orders every digit, not only the last', () => {
    expect(compareVersions('1.0.0.0', '0.99.99.99')).toBe(1);
    expect(compareVersions('0.6.0.0', '0.5.99.99')).toBe(1);
    expect(compareVersions('0.5.19.1', '0.5.19.0')).toBe(1);
  });

  it('is silent rather than throwing on a version it cannot read', () => {
    // A manifest we do not understand is not an update.
    expect(isNewer('', '0.5.19.0')).toBe(false);
    expect(isNewer('0.5.19.0', '')).toBe(false);
    expect(() => compareVersions('nonsense', '0.5.x.0')).not.toThrow();
  });
});

describe('channelOf', () => {
  it('reads the packaging difference that already exists', () => {
    // build_packages.py strips `key` for the store and keeps it for
    // sideload; nothing new had to be invented to tell them apart.
    expect(channelOf({ key: 'MIIBIjANBg...' })).toBe('sideload');
    expect(channelOf({})).toBe('store');
    expect(channelOf(null)).toBe('store');
  });
});

describe('noticeFor', () => {
  const base = { installed: '0.5.18.1', latest: '0.5.19.0', channel: 'store' as const };

  it('says nothing when the panel is current, or ahead', () => {
    expect(noticeFor({ ...base, latest: '0.5.18.1' })).toBeNull();
    expect(noticeFor({ ...base, latest: '0.5.17.0' })).toBeNull();
  });

  it('names the version and the channel when one is newer', () => {
    expect(noticeFor(base)).toEqual({ version: '0.5.19.0', channel: 'store' });
    expect(noticeFor({ ...base, channel: 'sideload' }))
      .toEqual({ version: '0.5.19.0', channel: 'sideload' });
  });

  it('stays quiet about the version that was dismissed', () => {
    expect(noticeFor({ ...base, dismissed: '0.5.19.0' })).toBeNull();
  });

  it('speaks again for a version NEWER than the one dismissed', () => {
    // Dismissal is keyed to a version for this reason: a boolean would
    // silence the release that fixes whatever the reader hits next.
    expect(noticeFor({ ...base, latest: '0.5.20.0', dismissed: '0.5.19.0' }))
      .toEqual({ version: '0.5.20.0', channel: 'store' });
    // …and the same comparison must be numeric here too.
    expect(noticeFor({ ...base, latest: '0.5.19.0', dismissed: '0.5.9.0' }))
      .toEqual({ version: '0.5.19.0', channel: 'store' });
  });
});

describe('isCheckDue', () => {
  const now = 1_700_000_000_000;

  it('asks when it has never asked', () => {
    expect(isCheckDue(0, now)).toBe(true);
    expect(isCheckDue(Number.NaN, now)).toBe(true);
  });

  it('reuses a fresh answer instead of asking again on every open', () => {
    // The panel is opened and closed all day; without this it would ask
    // dozens of times a day for a number that moves about once a week.
    expect(isCheckDue(now - 60_000, now)).toBe(false);
    expect(isCheckDue(now - (CHECK_EVERY_MS - 1), now)).toBe(false);
  });

  it('asks again once the answer is an hour old', () => {
    expect(isCheckDue(now - CHECK_EVERY_MS, now)).toBe(true);
  });

  it('is not wedged shut by a clock that moved backwards', () => {
    // A laptop waking in another timezone, or a corrected system clock,
    // must not silence the check until real time catches up.
    expect(isCheckDue(now + 86_400_000, now)).toBe(true);
  });
});
