import type { Mod } from '../../../catalogue';

/**
 * Long Haul — the daylight half of the pair.
 *
 * Night Haul is for the end of a shift; this is for the middle of one.
 * Bright cab, sun on the glass, six hours of straight road: the horizon
 * on the ground, wind rather than the rumble under it, and SNAPPY
 * motion, because the thing that tires a person on a long day is
 * waiting for a screen to finish moving.
 *
 * It names its bed and does not start it — no look may.
 */
export const longHaul: Mod = {
  id: 'long-haul', label: 'Long Haul', accent: 'green', motion: 'snappy',
  wallpaper: 'long-haul', wallpaperPage: 'long-haul',
  ambience: 'wind',
  description: 'For the middle of a long day — the horizon, moving air, and nothing kept waiting',
};
