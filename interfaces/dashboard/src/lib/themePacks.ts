/**
 * The theme packs — ONE list, and the reason this file exists.
 *
 * The accent used to be a closed union with six independent pins: a
 * TypeScript type, a runtime array, a membership test in the sanitiser,
 * a hand-written copy inside index.html's pre-paint script, a CSS block
 * per accent per mode, and a test asserting the array literally. Adding
 * a fourth meant finding all six, and missing one failed silently in a
 * different place each time.
 *
 * A pack is a NAME and a SEED. Everything else about it — the fill, the
 * hover, the accent as text — is derived by `derivePalette`, and the
 * guard in themePacks.test.ts asserts the CSS we ship equals what the
 * derivation produces. So a pack cannot drift from its seed, and adding
 * one is: append here, paste the generated block into index.css.
 *
 * These are OURS. Nothing in the product edits them — not an account,
 * not an account owner. A user picks from what we prepared, the same way
 * they pick dark or light today. When per-user authoring arrives, the
 * seed is still the artifact and this list is still the catalogue; only
 * the delivery changes, from a committed CSS block to properties applied
 * at runtime.
 */
import type { ThemeMode } from './palette';

export interface ThemePack {
  /** The stored value, and the `data-accent` attribute. */
  readonly id: string;
  /** Shown in the picker. Not translated — see the theme panel's rule. */
  readonly label: string;
  /** `--primary` per mode. The canvas comes from the mode, not the pack;
   *  a pack that carried its own canvas would be taking the light/dark
   *  choice away from the person using it. */
  readonly seed: Readonly<Record<ThemeMode, string>>;
}

export const THEME_PACKS: readonly ThemePack[] = [
  { id: 'blue',   label: 'Blue',   seed: { light: '#2a5cda', dark: '#427bff' } },
  { id: 'purple', label: 'Purple', seed: { light: '#7d40c8', dark: '#9b61ea' } },
  { id: 'green',  label: 'Green',  seed: { light: '#197112', dark: '#38aa2f' } },
] as const;

/** The three tokens an accent block re-points, and therefore the three a
 *  pack's seed is responsible for. `--chart-1` moves with the accent too
 *  but is NOT derived from the seed: the ramp's job is series
 *  separation, which is a property of all five slots together. */
export const PACK_TOKENS = ['--primary', '--primary-hover', '--primary-text'] as const;

export const packById = (id: string): ThemePack | undefined =>
  THEME_PACKS.find((p) => p.id === id);
