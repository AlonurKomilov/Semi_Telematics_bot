/**
 * Every pack on every axis, in one place — what a store would list.
 *
 * Each axis keeps its own index (the door for icons, the cue engine for
 * sound); this file only gathers them, so that "everything a person can
 * choose" is one array and not ten imports. `packs.test.ts` holds it
 * equal to the folders beside it and sweeps every entry against
 * `ItemMeta`.
 */
import type { ItemMeta } from './meta';
import { MODS } from './mods';
import { SOUND_PACKS } from './sound';
import { KEY_PACKS } from './keys';
import { WALLPAPERS } from './wallpaper';
import { CURSOR_PACKS } from './cursor';
import { SHADER_PACKS } from './shader';
import { MATERIAL_PACKS } from './material';
import { THEME_PACKS } from './theme';
import { FONT_PACKS } from './font';
import { ICON_PACKS } from './icons';
import { CORNERS } from './corners';
import { MOTION_PACKS } from './motion';

export interface ItemAxis {
  /** The folder under `mods/packs/`, and the noun a tile is filed under. */
  readonly axis: string;
  readonly items: readonly ItemMeta[];
}

export const ITEM_AXES: readonly ItemAxis[] = [
  { axis: 'mods',      items: MODS },
  { axis: 'theme',     items: THEME_PACKS },
  { axis: 'font',      items: FONT_PACKS },
  { axis: 'icons',     items: ICON_PACKS },
  { axis: 'material',  items: MATERIAL_PACKS },
  { axis: 'corners',   items: CORNERS },
  { axis: 'motion',    items: MOTION_PACKS },
  { axis: 'wallpaper', items: WALLPAPERS },
  { axis: 'cursor',    items: CURSOR_PACKS },
  { axis: 'shader',    items: SHADER_PACKS },
  { axis: 'sound',     items: SOUND_PACKS },
  { axis: 'keys',      items: KEY_PACKS },
];
