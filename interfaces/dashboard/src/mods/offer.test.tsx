/**
 * The offer comes from the store's local — every picker, every axis.
 *
 * `local.offered` is mocked to withhold a pack, and the chip has to
 * disappear. A picker that maps its own pack list instead keeps the
 * chip, which is the failure this file exists to catch: the store is
 * the source keeper, so nothing may draw a choice it did not hand out.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';

// `vi.mock` is hoisted above the file, so the spy has to be hoisted too
// or the factory closes over a variable that does not exist yet.
const { setTheme } = vi.hoisted(() => ({ setTheme: vi.fn() }));

// Spread the real modules: the preferences barrel pulls in AuthContext,
// which pulls in src/i18n.ts, which needs `initReactI18next` to exist.
// A bare factory here replaced the whole module and the suite failed to
// collect rather than failing an assertion.
vi.mock('react-i18next', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  useTranslation: () => ({ t: (_k: string, d?: string) => d ?? _k }),
}));
vi.mock('react-router-dom', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  Link: ({ to, children }: { to: string; children: React.ReactNode }) => (
    <a href={to}>{children}</a>
  ),
}));
vi.mock('./context', () => ({
  useMods: () => ({
    theme: {
      mode: 'dark', accent: 'blue', radius: 'md', material: 'solid',
      motion: 'default', icons: 'regular', mod: '',
    },
    setTheme,
    size: { global: 1, text: 1, control: 1, layout: 1, panel: 1, regions: {} },
    setSize: () => {},
  }),
  applySize: () => {},
}));
vi.mock('../preferences', async (orig) => ({
  ...(await orig<Record<string, unknown>>()),
  usePreference: (k: string) => ({
    value: k === 'mods.sound.volume' ? 1 : k === 'dispatch.soundOn' ? true : 'chime',
    setValue: () => {},
  }),
}));

// The picker's scope row asks who this is: it offers only the places
// this person may open. These tests are not about that gate, so they
// wear an owner's view — every place reachable, permissions settled.
vi.mock('../hooks/useViewPermissions', () => ({
  useViewPermissions: () => ({
    has: () => true, hasAny: () => true, hasWide: () => true,
    vehicleScope: 'all', role: 'owner', ready: true,
  }),
}));


/** What this run pretends is not carried: `axis/id`. */
const WITHHELD = ['theme/purple', 'font/mono'];

// The real door is the one under test at every CALL SITE — so the mock
// replaces the hook, not the preference it reads. A picker that never
// asks it is untouched by this, and that is exactly what fails.
vi.mock('./store/useOffered', async (orig) => {
  const real = await orig<typeof import('./store/useOffered')>();
  return {
    ...real,
    useOffered: () => <T,>(axis: string, items: readonly T[], idOf: (i: T) => string) =>
      items.filter((i) => !WITHHELD.includes(`${axis}/${idOf(i)}`)),
  };
});

import { ModControls } from './panel/ModControls';

describe('a pack the store does not hand out is not offered', () => {
  it('the accent chip is gone, and its neighbours stay', () => {
    render(<ModControls />);
    expect(screen.queryByText('Purple')).toBeNull();
    expect(screen.getByText('Green')).toBeTruthy();
  });

  it('the font chip is gone, and its neighbours stay', () => {
    render(<ModControls />);
    expect(screen.queryByText('Mono')).toBeNull();
    expect(screen.getByText('Serif')).toBeTruthy();
  });
});

describe('and no surface draws a choice the store did not hand out', () => {
  /** The lists that ARE packs. A value axis (the icon weights, the
   *  corner radii) is not one — nobody installs "bold".
   *
   *  The rule is about the CHOOSING, not the listing: `{X.map(` is a
   *  row of chips being drawn, while `= X.map(` builds the option table
   *  from the pack index, which is the rule that keeps a second list
   *  from being spelled by hand. Only the first has to ask the store. */
  const PACK_LISTS = [
    'ACCENT_OPTIONS', 'MATERIAL_OPTIONS', 'PACK_OPTIONS', 'MOD_OPTIONS',
    'THEME_PACKS', 'FONT_PACKS', 'CURSOR_PACKS', 'WALLPAPERS',
    'SHADER_PACKS', 'SOUND_PACKS', 'KEY_PACKS', 'MATERIAL_PACKS', 'ICON_PACKS', 'MODS',
  ];
  const files = ['panel', 'page'].flatMap((dir) =>
    readdirSync(join(__dirname, dir))
      .filter((f) => /\.tsx?$/.test(f) && !/\.test\.tsx?$/.test(f))
      .map((f) => join(__dirname, dir, f)));

  it('finds the surfaces to check', () => {
    expect(files.length, 'the scan is reading nothing').toBeGreaterThan(5);
  });

  it('a pack list is never mapped where a person is choosing', () => {
    for (const f of files) {
      const code = readFileSync(f, 'utf8')
        .replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '');
      for (const list of PACK_LISTS) {
        expect(code, `${f} maps ${list} itself — it must ask the store what is carried`)
          .not.toMatch(new RegExp(`\\{${list}\\.map\\(`));
      }
    }
  });

  it('and that scan can fail', () => {
    expect('{FONT_PACKS.map((f) => (').toMatch(/\{FONT_PACKS\.map\(/);
    expect("{offered('font', FONT_PACKS, (f) => f.id).map((f) => (").not.toMatch(/\{FONT_PACKS\.map\(/);
    // the table build is not a choosing surface, and must stay allowed
    expect('const FONTS = FONT_PACKS.map((f) => f.id);').not.toMatch(/\{FONT_PACKS\.map\(/);
  });
});
