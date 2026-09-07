/**
 * The accents this app ships — a seed per mode, and a `.css` file
 * holding the tokens derived from it. Blue has no file: it IS the base
 * `:root` / `.dark`, so an unstamped document paints what it always
 * painted.
 *
 * Resource, not engine: `mods/catalogue.ts` keeps the contract
 * (`ThemePack`, the three tokens a seed is responsible for) and knows
 * nothing about which packs exist. The CSS is DERIVED from the seed and
 * `catalogue.test.ts` holds the two within ΔE 3, so the file cannot
 * drift from the seed that describes it. A generator that writes these
 * files from the seeds is the honest end state; the layout is already
 * the one it would write into.
 */
import type { ThemePack } from '../../catalogue';

export const THEME_PACKS: readonly ThemePack[] = [
  { id: 'blue',   label: 'Blue',   seed: { light: '#2a5cda', dark: '#427bff' } },
  { id: 'purple', label: 'Purple', seed: { light: '#7d40c8', dark: '#9b61ea' } },
  { id: 'green',  label: 'Green',  seed: { light: '#3f7b04', dark: '#56a700' } },
  { id: 'azure',  label: 'Azure',  seed: { light: '#027689', dark: '#0796ae' } },
] as const;

export const packById = (id: string): ThemePack | undefined =>
  THEME_PACKS.find((p) => p.id === id);
