import { createContext, useContext, useEffect, type ReactNode } from 'react';

import { usePreference } from '../preferences';
import type {
  ThemeColor, ThemeDensity, ThemeRadius, ThemeSetting,
} from '../preferences';

// The stored SHAPE is owned by the preferences registry (it's the single
// source of truth for what persists); these aliases keep the long-standing
// names every consumer already imports from here.
export type ColorTheme = ThemeColor;
export type Density = ThemeDensity;
export type RadiusVariant = ThemeRadius;
export type Theme = ThemeSetting;

interface ThemeContextValue {
  theme: Theme;
  setTheme: (partial: Partial<Theme>) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

function applyTheme(theme: Theme) {
  const root = document.documentElement;
  if (theme.color === 'light') {
    root.classList.remove('dark');
  } else {
    root.classList.add('dark');
  }
  root.dataset.theme = theme.color;
  root.dataset.density = theme.density;
  root.dataset.radius = theme.radius;
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  // Persistence (default, legacy 'dashboard-theme' migration, and the
  // partial-object completion this used to do inline) lives in the
  // preferences registry now.  This provider only applies the theme to
  // the DOM and exposes the partial-update ergonomics consumers expect.
  const { value: theme, setValue } = usePreference('theme');

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = (partial: Partial<Theme>) => {
    setValue((prev) => ({ ...prev, ...partial }));
  };

  return (
    <ThemeContext.Provider value={{ theme, setTheme }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme() {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error('useTheme must be used within ThemeProvider');
  return ctx;
}
