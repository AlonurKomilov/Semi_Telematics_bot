/**
 * Every pack on every axis, in one place — what a store would list.
 *
 * Each axis keeps its own index (the door for icons, the cue engine for
 * sound); this file only gathers them, so that "everything a person can
 * choose" is one array and not ten imports. `packs.test.ts` holds it
 * equal to the folders beside it and sweeps every entry against
 * `PackMeta`.
 */
import type { PackMeta } from './meta';
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

export interface PackAxis {
  /** The folder under `mods/packs/`, and the noun a tile is filed under. */
  readonly axis: string;
  readonly packs: readonly PackMeta[];
}

export const PACK_AXES: readonly PackAxis[] = [
  { axis: 'mods',      packs: MODS },
  { axis: 'theme',     packs: THEME_PACKS },
  { axis: 'font',      packs: FONT_PACKS },
  { axis: 'icons',     packs: ICON_PACKS },
  { axis: 'material',  packs: MATERIAL_PACKS },
  { axis: 'wallpaper', packs: WALLPAPERS },
  { axis: 'cursor',    packs: CURSOR_PACKS },
  { axis: 'shader',    packs: SHADER_PACKS },
  { axis: 'sound',     packs: SOUND_PACKS },
  { axis: 'keys',      packs: KEY_PACKS },
];
