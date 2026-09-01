/**
 * The mods service — one import surface.
 *
 * `theme` stopped being the right word for this some time ago. It is a
 * colour word, and what lives here now is colour, corners, scale,
 * material, motion, icon weight, a page entrance and sound. "Mods" is
 * the noun that covers them, and it is the vocabulary the product is
 * modelled on: Mods is the umbrella, and a theme is the colour part
 * inside it — hence `mods/theme` and `mods/sound` rather than a flat
 * pile.
 *
 * Application code imports from HERE, not from the files below. Two
 * exceptions, and both are structural rather than sloppy:
 *
 *   `preferences/registry.ts` imports the catalogue, the injector and
 *     the sound engine DIRECTLY. It cannot come through this barrel:
 *     `mods/context` imports the preferences service, so a barrel that
 *     re-exports the context would close a cycle. The registry is
 *     underneath this service, not a consumer of it.
 *
 *   Tests import the file they test directly, so a failure names the
 *     module rather than the barrel.
 *
 * What is deliberately NOT here: `dispatch.soundOn`. That is the live
 * alerts feature's own gate, and it belongs to the feature that uses it
 * — the sound service sets the level and the pack, never whether a
 * particular feature speaks.
 */

// ── the service ─────────────────────────────────────────────────────
export {
  ThemeProvider, useTheme, applyTheme, applySize,
  type Theme, type Mode, type Accent, type RadiusVariant,
  type Material, type Motion, type Size, type ColorTheme,
} from './context';

export { ThemeToggle } from './ModPanel';
export { IconWeight } from './icons/IconWeight';

// ── the catalogue ───────────────────────────────────────────────────
export {
  THEME_PACKS, THEME_MODS, THEME_MATERIALS, THEME_MOTIONS, THEME_ICONS,
  ICON_STROKE, PACK_TOKENS,
  packById, modById, modMatchesAxes, activeModId,
  type ThemePack, type ThemeMod, type ThemeAxes,
  type ThemeMaterial, type ThemeMotion, type ThemeIcons,
} from './catalogue';

// ── installing values ───────────────────────────────────────────────
export {
  applyModTokens, modStyleText, isModToken, isSafeValue, seedTokens,
  MOD_TOKENS, type ApplyResult,
} from './inject';

// ── theme: the colour half ──────────────────────────────────────────
export { derivePalette, DERIVED_TOKENS, type ThemeSeed } from './theme/palette';
export {
  contrastRatio, readableOn, clampLightness, clampSurface,
  parseHex, toHex, oklchToSrgb, srgbToOklch, srgbInGamut, maxChroma,
  relLum, over, AA_TEXT, AA_LARGE, AAA_TEXT, type RGB,
} from './theme/contrast';

// ── sound ───────────────────────────────────────────────────────────
export {
  SOUND_PACKS, CUE_NAMES, CUE_LIMITS, WAVES,
  soundPackById, isSafeCue, playCue, armAudio,
  type SoundPack, type Cue, type CueName, type Wave,
} from './sound/engine';
export { useCue } from './sound/useCue';
