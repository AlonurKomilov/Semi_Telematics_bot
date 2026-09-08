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
  ModProvider, useMods, applyTheme, applySize,
  type Theme, type Mode, type Accent, type RadiusVariant,
  type Material, type Motion, type Size, type ColorTheme,
} from './context';

export { ModPanel } from './panel/ModPanel';
export { ModControls } from './panel/ModControls';
export { default as Modifications } from './Modifications';
export { MODS_HREF, MODS_PAGE_HREF } from './href';
export { MODS_PERMISSION } from './access';
export { pageWallpaperFor } from './wallpaper';
export { ModsLock, lockedModsKeys, useCanMods } from './ModsLock';
export { SURFACES, surfaceFor, surfaceById, type Surface } from './surfaces';
export { default as ModsPage } from './page/ModsPage';
export { default as SizeCard } from './SizeCard';
export { IconWeight } from './icons/IconWeight';

// ── the catalogue ───────────────────────────────────────────────────
export {
  MOD_MOTIONS, MOD_ICONS,
  PACK_TOKENS,
  modMatchesAxes,
  type ThemePack, type Mod, type ModAxes,
  type ModMaterial, type ModMotion, type ModIcons,
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
  srgbToLab, deltaE2000, distance,
} from './theme/contrast';

// ── sound ───────────────────────────────────────────────────────────
export {
  CUE_NAMES, CUE_LIMITS, WAVES,
  isSafeCue, playCue, armAudio,
  type SoundPack, type Cue, type CueName, type Wave,
} from './sound/engine';
export { SOUND_PACKS, soundPackById } from './packs/sound';
export { useCue } from './sound/useCue';
export { THEME_PACKS, packById } from './packs/theme';
export { FONT_PACKS, MOD_FONTS } from './packs/font';
export { MODS, modById } from './packs/mods';
export { MATERIAL_PACKS, MATERIAL_IDS, materialPackById } from './packs/material';
export type { MaterialPack } from './material';
