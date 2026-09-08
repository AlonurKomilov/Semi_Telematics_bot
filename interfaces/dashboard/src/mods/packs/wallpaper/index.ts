/**
 * The patterns this app ships — one `.css` file each, listed here.
 *
 * Resource, not engine: `mods/wallpaper.ts` defines what a wallpaper is
 * (the type, still or live, the ground it paints, the ink it must clear)
 * and knows nothing about which exist. `none` has no file on purpose — flat chrome
 * is the absence of a rule, not a rule that undoes one.
 */
import type { Wallpaper } from '../../wallpaper';

export const WALLPAPERS: readonly Wallpaper[] = [
  { id: 'none',   label: 'None',   kind: 'still', description: 'Flat chrome, the way it has always been' },
  { id: 'mesh',   label: 'Mesh',   kind: 'still', description: 'Two soft pools of the accent, low in the corners' },
  { id: 'grid',   label: 'Grid',   kind: 'still', description: 'Fine ruled lines, like engineering paper' },
  { id: 'grain',  label: 'Grain',  kind: 'still', description: 'A fine tooth, the way paper stock has one' },
  { id: 'paper',  label: 'Paper',  kind: 'still', description: 'Drawn fibres, as if the chrome were pressed sheet' },
  { id: 'plasma', label: 'Plasma', kind: 'still', description: 'Soft accent clouds under a fine tooth' },
  // Live: the same clouds, drifting. Same axis, same chips, same gate —
  // the pack moves a layer the gate has already measured.
  { id: 'plasma-live', label: 'Plasma live', kind: 'live', description: 'The accent clouds, drifting slowly under the tooth' },
];

export const WALLPAPER_IDS = WALLPAPERS.map((w) => w.id);

export const wallpaperById = (id: string): Wallpaper | undefined =>
  WALLPAPERS.find((w) => w.id === id);
