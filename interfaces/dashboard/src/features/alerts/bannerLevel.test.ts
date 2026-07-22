import { describe, it, expect, beforeEach } from 'vitest';
import {
  getBannerLevel, resetBannerLevelForTests, setBannerLevel,
} from './bannerLevel';

beforeEach(() => {
  localStorage.clear();
  resetBannerLevelForTests();
});

describe('banner level store', () => {
  it('defaults to critical-only (the sane default at fleet volume)', () => {
    expect(getBannerLevel()).toBe('critical');
  });

  it('roundtrips a chosen level', () => {
    setBannerLevel('off');
    expect(getBannerLevel()).toBe('off');
    setBannerLevel('all');
    expect(getBannerLevel()).toBe('all');
  });

  it('falls back to the default on a corrupted value', () => {
    localStorage.setItem('notif.bannerLevel', 'loud');
    expect(getBannerLevel()).toBe('critical');
  });
});
