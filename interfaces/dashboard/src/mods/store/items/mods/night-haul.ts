import type { Mod } from '../../../catalogue';

/**
 * The pack's own preset — one click for everything it brought.
 *
 * It names its own items on three axes and borrows the accent, because
 * the palette is full: every hue that clears the chart ramp under
 * simulated colour blindness is taken, and Azure is already the coolest
 * and calmest of them. A pack does not have to ship an item on an axis
 * to prepare that axis.
 *
 * Calm motion belongs to the same idea as the quiet cues: at the end of
 * a shift, things that move fast read as things that need answering.
 */
export const nightHaul: Mod = {
  id: 'night-haul', label: 'Night Haul', accent: 'azure', motion: 'calm',
  wallpaper: 'night-haul', wallpaperPage: 'night-haul',
  sound: 'night-haul', keys: 'night-haul',
  description: 'For the end of a long shift — low light, low sound, nothing in a hurry',
};
