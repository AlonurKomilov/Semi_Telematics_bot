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
 * one is: append here, paste the generated block into index.css, and
 * add the id to the boot script's own array in index.html.
 *
 * That last one cannot be generated away — the pre-paint script runs
 * before any module loads and so cannot import this file (design.md §2
 * rule 4). It is the one copy that stays, and `themeBoot.test.ts`
 * exists to compare the two: it runs the shipped script against
 * `applyTheme` over every valid theme and fails on disagreement. It
 * caught azure's omission on the first run.
 *
 * These are OURS. Nothing in the product edits them — not an account,
 * not an account owner. A user picks from what we prepared, the same way
 * they pick dark or light today. When per-user authoring arrives, the
 * seed is still the artifact and this list is still the catalogue; only
 * the delivery changes, from a committed CSS block to properties applied
 * at runtime.
 */
import type { ThemeMode } from './palette';
// Type-only, so no runtime cycle: registry.ts imports THEME_PACKS as a
// value, and this import is erased.
import type { ThemeRadius } from '../preferences/registry';

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
  { id: 'azure',  label: 'Azure',  seed: { light: '#027689', dark: '#0796ae' } },
] as const;

/** The three tokens an accent block re-points, and therefore the three a
 *  pack's seed is responsible for. `--chart-1` moves with the accent too
 *  but is NOT derived from the seed: the ramp's job is series
 *  separation, which is a property of all five slots together. */
export const PACK_TOKENS = ['--primary', '--primary-hover', '--primary-text'] as const;

/**
 * What a surface is MADE OF, as opposed to what colour it is.
 *
 * An axis, not a pack field — it belongs beside corners in the panel,
 * because it is a property of the whole app rather than of one look. A
 * mod may set it, the same way a mod sets corners.
 *
 * Kept here rather than in the preferences registry for the same reason
 * the accent set is: one list, and the registry derives from it.
 */
export const THEME_MATERIALS = ['solid', 'glass'] as const;
export type ThemeMaterial = (typeof THEME_MATERIALS)[number];

export const packById = (id: string): ThemePack | undefined =>
  THEME_PACKS.find((p) => p.id === id);

/**
 * A MOD is a named combination of axes we already have — not a new
 * colour.
 *
 * The first draft put `radius` and `size` onto ThemePack, and that was
 * wrong twice over. It conflated "a hue with its derived tokens" with "a
 * look", and it meant every look needed a hue of its own — which the
 * palette cannot supply. Measured while choosing azure: every remaining
 * hue either collides with `--danger` under simulated colour blindness,
 * collides with `--warn`, or duplicates a pack we ship. A model that
 * demands a new hue per look runs out after one.
 *
 * Referencing a pack instead costs nothing: no seed, no CSS block, no
 * swatch, no ramp rotation. A mod is a row in this list and nothing else.
 *
 * And this is the axis Opera GX cannot distribute. A GX mod carries
 * colours, sounds and wallpapers; corner radius and UI density are
 * user settings there, unreachable from a mod. Ours are already values
 * on `<html>` — `data-radius` and the `--size-*` multipliers — so a mod
 * can simply carry them.
 */
export interface ThemeMod {
  readonly id: string;
  readonly label: string;
  /** The colour pack this look wears. Must be a `THEME_PACKS` id. */
  readonly accent: string;
  readonly radius?: ThemeRadius;
  /** The global size multiplier. At or above 1 only — the panel's own
   *  slider starts at 100% because everything below it waits on the
   *  24px hit-target floor (design.md §5.1), and a mod must not reach
   *  somewhere the control cannot follow it back from. */
  readonly size?: number;
  /** What surfaces are made of. Omit and the person's own choice stands. */
  readonly material?: ThemeMaterial;
  /** One line, shown under the label. Says who the look is FOR. */
  readonly why: string;
}

/**
 * Deliberately NOT carrying `mode`. Dark or light is the one axis that
 * is about the room a person is sitting in — a bright yard office at
 * noon, a cab at 2am — and a look that seizes it makes the screen
 * unreadable for exactly the reason they chose the other one. A mod
 * dresses the app; it does not decide where you are.
 */
export const THEME_MODS: readonly ThemeMod[] = [
  { id: 'cab',  label: 'Cab',  accent: 'azure', radius: 'pill',    size: 1.25,
    why: 'Tablet in a moving truck — bigger targets, gloved hands' },
  { id: 'wall', label: 'Wall', accent: 'blue',  radius: 'rounded', size: 1.45,
    why: 'A display read from across the room' },
] as const;

export const modById = (id: string): ThemeMod | undefined =>
  THEME_MODS.find((m) => m.id === id);

/**
 * Which mod, if any, the current axes ADD UP TO — `''` for none.
 *
 * Here rather than in the panel because it is the only interesting logic
 * a mod has, and inside a component it could only be tested by rendering
 * one. It is also the answer to "what happens when someone tweaks a
 * corner after applying a look": nothing is stored, nothing is
 * invalidated, the sum simply stops matching and the chip goes quiet.
 */
export const activeModId = (
  accent: string, radius: string, size: number, material: string,
): string =>
  THEME_MODS.find((m) =>
    m.accent === accent
    && (m.radius === undefined || m.radius === radius)
    && (m.material === undefined || m.material === material)
    // Float compare: the size arrives from a slider and a stored JSON
    // round-trip, so `===` against 1.25 is a coin toss.
    && (m.size === undefined || Math.abs(m.size - size) < 1e-6))?.id ?? '';
