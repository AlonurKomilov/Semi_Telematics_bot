/**
 * The patterns this app ships — one `.css` file each, listed here.
 *
 * Resource, not engine: `mods/wallpaper.ts` defines what a wallpaper is
 * (the type, still or live, the ground it paints, the ink it must clear)
 * and knows nothing about which exist. `none` has no file on purpose — flat chrome
 * is the absence of a rule, not a rule that undoes one.
 */
import type { Wallpaper } from '../../../wallpaper';

export const WALLPAPERS: readonly Wallpaper[] = [
  { id: 'none',   label: 'None',   kind: 'still', description: 'Flat chrome, the way it has always been' },
  // Can move: with Live on, the pools drift the other way from
  // Plasma's clouds and half as fast.
  { id: 'mesh',   label: 'Mesh',   kind: 'live',  description: 'Two soft pools of the accent, low in the corners' },
  { id: 'grid',   label: 'Grid',   kind: 'still', description: 'Fine ruled lines, like engineering paper' },
  { id: 'grain',  label: 'Grain',  kind: 'still', description: 'A fine tooth, the way paper stock has one' },
  { id: 'paper',  label: 'Paper',  kind: 'still', description: 'Drawn fibres, as if the chrome were pressed sheet' },
  // Can move: with Live on, the clouds drift. Same pack, same chip,
  // same gate — the live state moves a layer the gate has measured.
  { id: 'plasma', label: 'Plasma', kind: 'live', description: 'Soft accent clouds under a fine tooth' },
  // Night Haul's own. Named for its pack on every shelf it reaches, the
  // way a pack's items are: the wallpaper picker says "Night Haul", and
  // so do the sound and keyboard pickers.
  { id: 'long-haul', label: 'Long Haul', kind: 'still',
    description: 'The horizon — a wide band of light where the road meets the sky' },
  { id: 'night-haul', label: 'Night Haul', kind: 'still',
    description: 'The road under headlights — light low and wide, nothing above the horizon' },
];

export const WALLPAPER_IDS = WALLPAPERS.map((w) => w.id);

export const wallpaperById = (id: string): Wallpaper | undefined =>
  WALLPAPERS.find((w) => w.id === id);
