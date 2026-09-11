/**
 * Where a pack lands when somebody takes it, and what they call the
 * shelf it sat on.
 *
 * The store lists ten axes; each one already has a home in the settings
 * — a field on the theme preference, a preference of its own, or, for a
 * whole look, the installer that writes seven fields at once. This table
 * is the only place that says which, and `store.test.ts` holds it equal
 * to `PACK_AXES` in both directions: an axis nobody filed in here would
 * be a shelf whose Apply button did nothing.
 *
 * The labels are the words the rest of the product already uses — the
 * taxonomy's own titles, held to them by a test, so a shelf cannot come
 * to be called one thing here and another on /mods.
 */
import { MOD_DEFAULT, DEFS, type ModSetting } from '../../preferences/registry';

/** Where the chosen id is written. */
export type AxisHome =
  /** Fields on the theme preference. Two when one choice paints two
   *  places — the wallpaper is picked once in a store and lands on both
   *  the frame and the page, which is what "apply" means here. */
  | { readonly theme: readonly (keyof ModSetting)[]; readonly pref?: never; readonly mod?: never }
  /** A preference of its own — sound packs are not on `<html>`. */
  | { readonly pref: string; readonly theme?: never; readonly mod?: never }
  /** A whole look: `useApplyMod` writes it, with the undo it owes. */
  | { readonly mod: true; readonly theme?: never; readonly pref?: never };

export interface AxisUI {
  /** What a person calls this shelf. */
  readonly label: string;
  readonly home: AxisHome;
}

export const AXIS_UI: Readonly<Record<string, AxisUI>> = {
  mods:      { label: 'Mods',              home: { mod: true } },
  theme:     { label: 'Color',             home: { theme: ['accent'] } },
  font:      { label: 'Typeface',          home: { theme: ['font'] } },
  icons:     { label: 'Icons',             home: { theme: ['iconPack'] } },
  material:  { label: 'Material',          home: { theme: ['material'] } },
  wallpaper: { label: 'Wallpaper',         home: { theme: ['wallpaper', 'wallpaperPage'] } },
  cursor:    { label: 'Cursor',            home: { theme: ['cursor'] } },
  shader:    { label: 'Shaders',           home: { theme: ['shader'] } },
  sound:     { label: 'Interface sounds',  home: { pref: 'mods.sound.pack' } },
  keys:      { label: 'Keyboard',          home: { pref: 'mods.sound.keyboard.pack' } },
};

/**
 * The pack a shelf falls back to — the one that cannot be taken off it.
 *
 * Read from the same home the shelf declares, never listed again here:
 * an axis whose default is spelled twice is an axis that can disagree
 * with itself, and this one already has a third copy in `MOD_DEFAULT`
 * that the engine paints from. The looks shelf has no default — wearing
 * no look is a perfectly good answer, so every look may be dropped.
 */
export function defaultOf(axis: string): string | undefined {
  const home = AXIS_UI[axis]?.home;
  if (!home || home.mod) return undefined;
  if (home.pref) {
    const d = (DEFS as Record<string, { default: unknown }>)[home.pref]?.default;
    return typeof d === 'string' ? d : undefined;
  }
  const d = MOD_DEFAULT[home.theme![0]];
  return typeof d === 'string' ? d : undefined;
}
