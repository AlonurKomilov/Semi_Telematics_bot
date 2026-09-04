import { createContext, useContext, useEffect, type ReactNode } from 'react';

import { usePreference } from '../preferences';
import { SIZE_REGIONS, themeColorAlias } from '../preferences/registry';
import { publishAppearanceDefault } from '../preferences/appearance';
import { applyModTokens } from './inject';
import { accentTokens } from './theme/accent';
import { paletteTokens } from './theme/canvas';
import { packById, THEME_PACKS } from './catalogue';
import { armIfWanted, installKeySound } from './sound/cue';
import { useAmbient } from './ambient/useAmbient';
import { AMBIENT_SCALE } from './ambient/ambient';
import type {
  ThemeColor,
  ThemeMode,
  ThemeAccent,
  ModRadius,
  ModMaterial,
  ModMotion,
  ModSetting,
  SizeSetting,
} from '../preferences';

// The stored SHAPE is owned by the preferences registry (it's the single
// source of truth for what persists); these aliases keep the long-standing
// names every consumer already imports from here.
/** @deprecated The pre-split spelling — see preferences/registry.ts. */
export type ColorTheme = ThemeColor;
export type Mode = ThemeMode;
export type Accent = ThemeAccent;
export type RadiusVariant = ModRadius;
export type Material = ModMaterial;
export type Motion = ModMotion;
export type Theme = ModSetting;
export type Size = SizeSetting;

interface ModContextValue {
  theme: Theme;
  setTheme: (partial: Partial<Theme>) => void;
  size: Size;
  setSize: (partial: Partial<Size>) => void;
}

const ModContext = createContext<ModContextValue | null>(null);

/**
 * Turn a stored theme into DOM state.  THE definition of that mapping.
 *
 * Exported because it has a second implementation: the pre-paint boot
 * script in ``index.html`` has to do exactly this before React exists,
 * or the first painted frame is the wrong theme.  Two implementations
 * of one mapping drift silently — a theme that renders correctly after
 * hydration and wrong before it is the kind of bug nobody files.  So
 * ``themeBoot.test.ts`` runs the inline script and this function over
 * the same inputs and asserts they agree; that test is the only reason
 * this is not a module-private function.
 */
export function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme.mode === 'light') {
    root.classList.remove('dark');
  } else {
    root.classList.add('dark');
  }
  root.dataset.accent = theme.accent;
  // Deprecated alias — see registry.ts. Kept stamped for one release so
  // a [data-theme] selector or observer still written against the old
  // spelling keeps working while it is being migrated.
  root.dataset.theme = themeColorAlias(theme.mode, theme.accent);
  root.dataset.radius = theme.radius;
  // The material axis. Static CSS keyed on this attribute, exactly like
  // the accent presets — so `colour.test.ts` and the chrome guards can
  // still see every value off disk.
  root.dataset.material = theme.material;
  root.dataset.motion = theme.motion;
  // Last of the attributes, and the one the boot script most has to
  // agree with: a typeface changes the width of every word, so getting
  // it a frame late reflows the page rather than recolouring it.
  root.dataset.font = theme.font;
}

/**
 * Write the Size multipliers onto ``<html>``.
 *
 * Style PROPERTIES, not a data attribute: the values are continuous, so
 * there is no finite set of attribute values a stylesheet could match.
 * Every length in tailwind.config.js is `calc(step × var(--size-…, 1))`,
 * so setting these four reshapes the app with no class name changing and
 * no React re-render — the cascade does the work.
 *
 * Each axis carries the GLOBAL multiplier folded in, so one control can
 * drive all four while the axes stay individually addressable.  Regions
 * are published separately and MULTIPLY: a component scopes itself by
 * reading `--size-region-<name>` into its own `--size-*`. Multiplication
 * is the only correct composition — the nested-fallback form
 * `var(--region, var(--global, 1))` REPLACES the global the moment a
 * region is set, silently discarding it.
 *
 * Exported for the same reason as applyTheme: the boot script is its
 * second implementation and the drift test compares them.
 */
export function applySize(size: Size, ambient = 1) {
  const root = document.documentElement;
  // Ambient rides the SAME multiplier the Size control writes, rather
  // than a transform. A `scale()` would create a containing block and
  // break every `position: fixed` under it — the dialogs, the toasts and
  // the assistant panel all live there. This is the axis the whole app
  // already resizes through, so ambient costs no new mechanism and
  // composes with whatever the person already chose.
  const g = size.global * ambient;
  root.style.setProperty('--size-text', String(size.text * g));
  root.style.setProperty('--size-control', String(size.control * g));
  root.style.setProperty('--size-layout', String(size.layout * g));
  root.style.setProperty('--size-panel', String(size.panel * g));
  for (const r of SIZE_REGIONS) {
    const own = size.regions[r];
    // OVERLAYS HOLD STILL. Regions multiply the global, so an alert
    // would otherwise be blown up along with the page — and an alert
    // that changes size depending on how long nobody has been at the
    // desk is two designs fighting. It keeps the size it was drawn at
    // and the ambient layout gives it room instead; the division is
    // exact, so a person's own overlay setting still applies on top.
    const v = r === 'overlays' && ambient !== 1 ? (own ?? 1) / ambient : own;
    if (v === undefined) root.style.removeProperty(`--size-region-${r}`);
    else root.style.setProperty(`--size-region-${r}`, String(v));
  }
}

export function ModProvider({ children }: { children: ReactNode }) {
  // Persistence (default, legacy 'dashboard-theme' migration, and the
  // partial-object completion this used to do inline) lives in the
  // preferences registry now.  This provider only applies the theme to
  // the DOM and exposes the partial-update ergonomics consumers expect.
  const { value: theme, setValue: setThemeValue } = usePreference('mods.theme');
  const { value: size, setValue: setSizeValue } = usePreference('mods.size');
  // Read only to re-arm when either gate changes; the values themselves
  // are the sound section's business, not the theme provider's.
  const { value: uiSound } = usePreference('mods.sound.ui');
  const { value: alertSound } = usePreference('dispatch.soundOn');
  const { value: keySound } = usePreference('mods.sound.keyboard');
  const ambient = useAmbient();
  const root = document.documentElement;

  // Deliberately NOT inside `applyTheme`. That function is the mapping
  // the pre-paint script re-implements, and `themeBoot.test.ts` compares
  // the two — a stylesheet the boot script cannot write would show up as
  // permanent drift. Custom tokens therefore arrive one frame after
  // hydration, which is the honest cost of the boot script not being
  // able to import a validator.
  useEffect(() => {
    // The picked colour is re-derived here rather than stored derived,
    // and that is why `mode` is in the dependency list: the same hex has
    // to become a lighter accent on near-black than on white, or a
    // custom colour is legible in one mode and invisible in the other.
    //
    // It merges OVER a mod's tokens. In practice they never meet —
    // installing a mod writes an accent, and writing an accent clears
    // the picked colour — but if they ever did, the colour a person
    // typed outranks the one a mod brought with it.
    // Two seeds, one derivation, in order of how much they claim.
    //
    // A canvas installs the WHOLE palette — twenty-four tokens — so it
    // supersedes the accent's four rather than merging with them; the
    // accent is already inside what `derivePalette` returns. The brand
    // it derives from is the person's own colour if they picked one, and
    // otherwise the seed of the pack in force, so choosing a background
    // does not silently discard the accent they are wearing.
    //
    // `fitCanvas` can refuse — the semantic tones follow the mode rather
    // than the canvas, so a canvas chosen against its mode leaves them
    // unreadable. A refusal falls back to the accent path rather than
    // stranding the person on a half-applied palette; the panel says why.
    const seedBrand = theme.brand
      ?? (packById(theme.accent) ?? THEME_PACKS[0]).seed[theme.mode];
    const full = theme.canvas
      ? paletteTokens(theme.canvas, seedBrand, theme.mode).tokens
      : null;
    const picked = full
      ?? (theme.brand ? accentTokens(theme.brand, theme.mode).tokens : null);
    const merged = picked ? { ...(theme.tokens ?? {}), ...picked } : (theme.tokens ?? null);
    applyModTokens(merged);
  }, [theme.tokens, theme.brand, theme.canvas, theme.accent, theme.mode]);

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    applySize(size, ambient ? AMBIENT_SCALE : 1);
    // An attribute as well as the multiplier, so the stylesheet can let
    // navigation recede — that half is not a size question.
    if (ambient) root.dataset.ambient = '';
    else delete root.dataset.ambient;
  }, [size, ambient, root]);

  // Audio is unlocked by a gesture, so something has to be listening
  // BEFORE the gesture happens — and until now the only two listeners
  // were inside the live-alert panel and the pack-preview handler.
  // Dispatcher, fleet and safety could therefore hear the app; owner,
  // admin, hr, accounting and recruiter had a volume dial and no path
  // to sound at all.
  //
  // Armed here, once, and only for a screen that has asked: arming
  // installs a listener that builds an AudioContext on the next click,
  // and a context is never torn down. Keyed on the gates so flipping one
  // on takes effect without a reload.
  useEffect(() => {
    armIfWanted();
    // Installed once and never removed — a listener that comes and goes
    // with a preference is a listener that stacks. It reads the gate
    // itself on every press, so installing it while the gate is on and
    // leaving it there costs one early-return per keystroke.
    if (keySound) installKeySound();
  }, [uiSound, alertSound, keySound]);

  // Publishing the cross-device default belongs HERE, on the single
  // funnel every appearance write already passes through — not at the
  // call sites. It was wired from the profile card only at first, which
  // meant the topbar picker (the path almost everyone actually uses)
  // silently never published, and "use these settings on my other
  // devices" did nothing for them.
  const setTheme = (partial: Partial<Theme>) => {
    // Re-derive the deprecated `color` alias on every write. A caller
    // sets `{ mode }` or `{ accent }`, and a plain merge would leave the
    // alias describing the PREVIOUS pair — which is the drift the
    // deprecated-alias recipe exists to avoid. One writer, here.
    setThemeValue((prev) => {
      const next = { ...prev, ...partial };
      // Choosing a pack drops the picked colour. The two are one
      // question with two answers — the stylesheet says the same thing
      // with `:not([data-mod-accent])`, and a state where both are set
      // would leave the chips showing a pack that is not what paints.
      // Only when the caller did not name `brand` itself: a write that
      // sets both is setting the colour and saying which chip it sits
      // nearest, and it means it.
      if (partial.accent !== undefined && partial.brand === undefined) delete next.brand;
      return { ...next, color: themeColorAlias(next.mode, next.accent) };
    });
    queueMicrotask(publishAppearanceDefault);
  };
  const setSize = (partial: Partial<Size>) => {
    setSizeValue((prev) => ({ ...prev, ...partial }));
    queueMicrotask(publishAppearanceDefault);
  };

  return (
    <ModContext.Provider value={{ theme, setTheme, size, setSize }}>
      {children}
    </ModContext.Provider>
  );
}

export function useMods() {
  const ctx = useContext(ModContext);
  if (!ctx) throw new Error('useMods must be used within ModProvider');
  return ctx;
}

