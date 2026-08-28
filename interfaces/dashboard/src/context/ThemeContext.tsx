import { createContext, useContext, useEffect, type ReactNode } from 'react';

import { usePreference } from '../preferences';
import { SIZE_REGIONS, themeColorAlias } from '../preferences/registry';
import { publishAppearanceDefault } from '../preferences/appearance';
import type {
  ThemeColor,
  ThemeMode,
  ThemeAccent,
  ThemeRadius,
  ThemeSetting,
  SizeSetting,
} from '../preferences';

// The stored SHAPE is owned by the preferences registry (it's the single
// source of truth for what persists); these aliases keep the long-standing
// names every consumer already imports from here.
/** @deprecated The pre-split spelling — see preferences/registry.ts. */
export type ColorTheme = ThemeColor;
export type Mode = ThemeMode;
export type Accent = ThemeAccent;
export type RadiusVariant = ThemeRadius;
export type Theme = ThemeSetting;
export type Size = SizeSetting;

interface ThemeContextValue {
  theme: Theme;
  setTheme: (partial: Partial<Theme>) => void;
  size: Size;
  setSize: (partial: Partial<Size>) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

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
export function applySize(size: Size) {
  const root = document.documentElement;
  const g = size.global;
  root.style.setProperty('--size-text', String(size.text * g));
  root.style.setProperty('--size-control', String(size.control * g));
  root.style.setProperty('--size-layout', String(size.layout * g));
  root.style.setProperty('--size-panel', String(size.panel * g));
  for (const r of SIZE_REGIONS) {
    const v = size.regions[r];
    if (v === undefined) root.style.removeProperty(`--size-region-${r}`);
    else root.style.setProperty(`--size-region-${r}`, String(v));
  }
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Persistence (default, legacy 'dashboard-theme' migration, and the
  // partial-object completion this used to do inline) lives in the
  // preferences registry now.  This provider only applies the theme to
  // the DOM and exposes the partial-update ergonomics consumers expect.
  const { value: theme, setValue: setThemeValue } = usePreference('theme');
  const { value: size, setValue: setSizeValue } = usePreference('size');

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  useEffect(() => {
    applySize(size);
  }, [size]);

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
      return { ...next, color: themeColorAlias(next.mode, next.accent) };
    });
    queueMicrotask(publishAppearanceDefault);
  };
  const setSize = (partial: Partial<Size>) => {
    setSizeValue((prev) => ({ ...prev, ...partial }));
    queueMicrotask(publishAppearanceDefault);
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme, size, setSize }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}

