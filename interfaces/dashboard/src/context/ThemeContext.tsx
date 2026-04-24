import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';

export type ColorTheme = 'dark-blue' | 'dark-purple' | 'dark-green' | 'light';
export type Density = 'compact' | 'default' | 'comfortable';
export type RadiusVariant = 'sharp' | 'rounded' | 'pill';

export interface Theme {
  color: ColorTheme;
  density: Density;
  radius: RadiusVariant;
}

interface ThemeContextValue {
  theme: Theme;
  setTheme: (partial: Partial<Theme>) => void;
}

const DEFAULT_THEME: Theme = { color: 'dark-blue', density: 'default', radius: 'rounded' };

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
  const [theme, setThemeState] = useState<Theme>(() => {
    try {
      const saved = localStorage.getItem('dashboard-theme');
      return saved ? { ...DEFAULT_THEME, ...JSON.parse(saved) } : DEFAULT_THEME;
    } catch {
      return DEFAULT_THEME;
    }
  });

  useEffect(() => {
    applyTheme(theme);
  }, [theme]);

  const setTheme = (partial: Partial<Theme>) => {
    setThemeState((prev) => {
      const next = { ...prev, ...partial };
      localStorage.setItem('dashboard-theme', JSON.stringify(next));
      return next;
    });
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
